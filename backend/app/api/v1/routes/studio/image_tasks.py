from __future__ import annotations

"""资产与镜头相关的图片生成任务 API。

通过 TaskManager 调用 `ImageGenerationTask`，并使用 `GenerationTaskLink`
将任务与上层业务实体（演员形象/道具/场景/服装/角色/镜头分镜帧）建立关联。
"""

import base64
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.prompts import PromptTemplate as LcPromptTemplate
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import storage
from app.core.db import async_session_maker
from app.core.task_manager import DeliveryMode, SqlAlchemyTaskStore, TaskManager
from app.core.task_manager.types import TaskStatus
from app.core.tasks import ImageGenerationInput, ImageGenerationResult, ImageGenerationTask, ProviderConfig
from app.dependencies import get_db
from app.utils.files import create_file_from_url_or_b64
from app.models.llm import Model, ModelCategoryKey, ModelSettings, Provider
from app.models.studio import (
    ActorImage,
    ActorImageImage,
    AssetViewAngle,
    Character,
    CharacterImage,
    Costume,
    CostumeImage,
    PromptCategory,
    PromptTemplate,
    Prop,
    PropImage,
    Scene,
    SceneImage,
    ShotDetail,
    FileItem,
    ShotFrameType,
    ShotFrameImage,
)
from app.models.task_links import GenerationTaskLink
from app.schemas.common import ApiResponse, success_response
from app.api.v1.routes.film.common import TaskCreated, _CreateOnlyTask


router = APIRouter()


class StudioImageTaskRequest(BaseModel):
    """Studio 专用图片任务请求体：可选模型 ID，不传则用默认图片模型；供应商由模型反查。

    image_id 表示具体的图片模型 ID，例如：
    - 演员形象图片：ActorImageImage.id
    - 场景图片：SceneImage.id
    - 道具图片：PropImage.id
    - 服装图片：CostumeImage.id
    - 角色图片：CharacterImage.id
    - 分镜帧图片：ShotFrameImage.id
    """

    model_id: str | None = Field(
        None,
        description="可选模型 ID（models.id）；不传则使用 ModelSettings.default_image_model_id；Provider 由模型关联反查",
    )
    image_id: int | None = Field(
        None,
        description="图片模型 ID，如 ActorImageImage.id / SceneImage.id / PropImage.id 等；必须与路径主体 ID 匹配",
    )


def _provider_key_from_db_name(name: str) -> str:
    """将 Provider.name 映射为任务层 ProviderKey（openai | volcengine）。
    规范名称：openai、火山引擎；兼容旧命名（volc/doubao/bytedance）映射为 volcengine。
    无法映射时抛出 503。
    """
    n = (name or "").strip()
    n_lower = n.lower()
    if n_lower == "openai":
        return "openai"
    if n == "火山引擎" or "volc" in n_lower or "doubao" in n_lower or "bytedance" in n_lower:
        return "volcengine"
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Unsupported provider name: {name!r}. Expected: openai, 火山引擎.",
    )


async def _resolve_image_model(db: AsyncSession, model_id: str | None) -> Model:
    """根据显式 model_id 或默认图片模型解析 Model。"""
    effective_model_id = model_id
    if not effective_model_id:
        settings_row = await db.get(ModelSettings, 1)
        effective_model_id = settings_row.default_image_model_id if settings_row else None

    if not effective_model_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No image model configured in DB (missing explicit model_id and ModelSettings.default_image_model_id)",
        )

    model = await db.get(Model, effective_model_id)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Configured model_id not found in DB: {effective_model_id}",
        )
    if model.category != ModelCategoryKey.image:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Configured model is not an image model: {effective_model_id} (category={model.category})",
        )
    return model


async def _load_provider_config(db: AsyncSession, provider_id: str) -> ProviderConfig:
    """根据 provider_id 从 DB 解析 ProviderConfig；仅允许适用于图片生成的供应商（openai、火山引擎）。"""
    provider = await db.get(Provider, provider_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provider not found for provider_id={provider_id}",
        )
    try:
        provider_key = _provider_key_from_db_name(provider.name)
    except HTTPException as e:
        if e.status_code == status.HTTP_503_SERVICE_UNAVAILABLE and (provider.name or "").strip() == "阿里百炼":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="该供应商仅适用于文本生成，不支持图片生成（name=阿里百炼）",
            ) from e
        raise
    api_key = (provider.api_key or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Provider api_key is empty for provider_id={provider.id}",
        )
    base_url = (provider.base_url or "").strip() or None
    return ProviderConfig(provider=provider_key, api_key=api_key, base_url=base_url)  # type: ignore[arg-type]


def _prompt_from_description(description: str, *, not_found_msg: str) -> str:
    prompt = (description or "").strip()
    if not prompt:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=not_found_msg)
    return prompt


def _is_front_view(view_angle: AssetViewAngle | str | None) -> bool:
    if view_angle is None:
        return False
    value = view_angle.value if hasattr(view_angle, "value") else str(view_angle)
    return value == AssetViewAngle.front.value


def _map_view_angle_for_prompt(view_angle: AssetViewAngle | str | None) -> str:
    if view_angle is None:
        return ""
    raw = view_angle.value if hasattr(view_angle, "value") else str(view_angle)
    view_angle_map = {
        "RIGH": "纯右側面,严格右侧面，90度纯侧面轮廓，耳朵清晰可见",
        "RIGHT": "纯右側面,严格右侧面，90度纯侧面轮廓，耳朵清晰可见",
        "LEFT": "纯左侧面,严格左侧面，90度纯侧面轮廓，耳朵清晰可见",
        "BACK": "正后方,正后方视角，完全背对镜头，只能看到后脑勺和后背",
    }
    return view_angle_map.get(raw, raw)


async def _resolve_prompt_template(
    db: AsyncSession,
    *,
    category: PromptCategory,
) -> PromptTemplate | None:
    stmt = (
        select(PromptTemplate)
        .where(PromptTemplate.category == category)
        .order_by(PromptTemplate.is_default.desc(), PromptTemplate.updated_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalars().first()


def _render_prompt_template_content(
    content: str,
    *,
    variables: dict[str, object],
) -> str:
    tmpl = LcPromptTemplate.from_template(content)
    render_vars = {k: str(variables.get(k, "")) for k in tmpl.input_variables}
    return tmpl.format(**render_vars).strip()


async def _build_prompt_with_template(
    db: AsyncSession,
    *,
    category: PromptCategory,
    variables: dict[str, object],
    fallback_prompt: str,
    not_found_msg: str,
) -> str:
    template = await _resolve_prompt_template(db, category=category)
    if template is not None and template.content:
        rendered = _render_prompt_template_content(template.content, variables=variables)
        if rendered:
            return rendered
    return _prompt_from_description(fallback_prompt, not_found_msg=not_found_msg)


async def _resolve_front_image_ref(
    db: AsyncSession,
    *,
    image_model: type,
    parent_field_name: str,
    parent_id: str,
    preferred_quality_level: object | None,
) -> dict[str, str] | None:
    parent_field = getattr(image_model, parent_field_name)
    stmt = (
        select(image_model)
        .where(
            parent_field == parent_id,
            image_model.view_angle == AssetViewAngle.front,
            image_model.file_id.is_not(None),
        )
        .order_by(image_model.created_at.desc(), image_model.id.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    if not rows:
        return None

    target = rows[0]
    if preferred_quality_level is not None:
        for row in rows:
            if row.quality_level == preferred_quality_level:
                target = row
                break

    if not target.file_id:
        return None

    file_obj = await db.get(FileItem, target.file_id)
    if file_obj is None or not file_obj.storage_key:
        return None

    try:
        content = await storage.download_file(key=file_obj.storage_key)
    except Exception:  # noqa: BLE001
        return None
    if not content:
        return None

    content_type: str | None = None
    try:
        info = await storage.get_file_info(key=file_obj.storage_key)
        content_type = (info.content_type or "").strip().lower() or None
    except Exception:  # noqa: BLE001
        content_type = None

    if not content_type:
        guessed_type, _ = mimetypes.guess_type(file_obj.storage_key)
        content_type = (guessed_type or "").strip().lower() or None

    if not content_type or not content_type.startswith("image/"):
        content_type = "image/png"

    image_format = content_type.split("/", 1)[1].split(";", 1)[0].strip().lower() or "png"
    encoded = base64.b64encode(content).decode("ascii")
    data_url = f"data:image/{image_format};base64,{encoded}"
    return {"image_url": data_url}


def _asset_prompt_category(
    *,
    relation_type: str,
    is_front_view: bool,
) -> PromptCategory:
    mapping = {
        "actor_image_image": (PromptCategory.actor_image_front, PromptCategory.actor_image_other),
        "prop_image": (PromptCategory.prop_front, PromptCategory.prop_other),
        "scene_image": (PromptCategory.scene_front, PromptCategory.scene_other),
        "costume_image": (PromptCategory.costume_front, PromptCategory.costume_other),
    }
    front_category, other_category = mapping[relation_type]
    return front_category if is_front_view else other_category


def _shot_frame_prompt_category(frame_type: ShotFrameType | str) -> PromptCategory:
    value = frame_type.value if hasattr(frame_type, "value") else str(frame_type)
    if value == ShotFrameType.first.value:
        return PromptCategory.frame_head
    if value == ShotFrameType.last.value:
        return PromptCategory.frame_tail
    return PromptCategory.frame_key


async def _create_image_task_and_link(
    *,
    db: AsyncSession,
    model_id: str | None,
    relation_type: str,
    relation_entity_id: str,
    prompt: str,
    images: list[dict[str, str]] | None = None,
) -> TaskCreated:
    """创建图片生成任务，并在 `GenerationTaskLink` 中建立关联；Provider 由解析出的 Model 反查。"""
    store = SqlAlchemyTaskStore(db)
    tm = TaskManager(store=store, strategies={})

    model = await _resolve_image_model(db, model_id)
    provider_cfg = await _load_provider_config(db, model.provider_id)

    run_args: dict = {
        "provider": provider_cfg.provider,
        "api_key": provider_cfg.api_key,
        "base_url": provider_cfg.base_url,
        "input": {
            "prompt": prompt,
            # 生成参数与参考图统一从 DB 侧控制；此接口不接收覆盖参数。
            "model": model.name,
        },
    }
    if images:
        run_args["input"]["images"] = images

    task_record = await tm.create(
        task=_CreateOnlyTask(),
        mode=DeliveryMode.async_polling,
        run_args=run_args,
    )

    link = GenerationTaskLink(
        task_id=task_record.id,
        resource_type="image",
        relation_type=relation_type,
        relation_entity_id=relation_entity_id,
    )
    db.add(link)
    await db.commit()

    async def _persist_images_to_assets(
        session: AsyncSession,
        *,
        task_id: str,
        relation_type: str,
        relation_entity_id: str,
        result: ImageGenerationResult,
    ) -> None:
        """根据 relation_type 将生成的图片落库为 FileItem + 具体资产图片表。

        - 先将图片内容上传到 S3，创建 FileItem 记录；
        - 再根据 relation_type 写入 ActorImageImage / SceneImage 等业务图片表；
        - 当前实现先支持 actor_image / scene_image / prop_image / costume_image，其他类型可按需扩展。
        """
        from sqlalchemy import select

        from app.models.studio import (
            ActorImageImage,
            CharacterImage,
            CostumeImage,
            PropImage,
            SceneImage,
            ShotFrameImage,
        )

        # 目前 ImageGenerationResult.images 优先使用 url；若仅有 b64，可在此扩展为下载/解码再上传。
        images = result.images or []
        if not images:
            return

        # 简化起见：仅处理第一张图片
        item = images[0]
        if not item.url:
            # 暂不支持纯 base64 输出的自动落库
            return

        # 使用通用工具方法：从 URL 创建 FileItem 并上传到对象存储
        file_obj = await create_file_from_url_or_b64(
            session,
            url=item.url,
            name=f"{relation_type}-{relation_entity_id}",
            prefix=f"generated-images/{relation_type}/{relation_entity_id}",
        )
        file_id = file_obj.id

        link_stmt = (
            select(GenerationTaskLink)
            .where(
                GenerationTaskLink.task_id == task_id,
                GenerationTaskLink.relation_type == relation_type,
                GenerationTaskLink.relation_entity_id == relation_entity_id,
            )
            .limit(1)
        )
        link_result = await session.execute(link_stmt)
        link_row = link_result.scalars().first()
        if link_row is not None and not link_row.file_id:
            link_row.file_id = file_id

        # 根据 relation_type 将生成文件填充到已有 image 槽位的 file_id（仅首张生效）
        if relation_type == "actor_image_image":
            image_row = await session.get(ActorImageImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id
        elif relation_type == "scene_image":
            image_row = await session.get(SceneImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id
        elif relation_type == "prop_image":
            image_row = await session.get(PropImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id
        elif relation_type == "costume_image":
            image_row = await session.get(CostumeImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id
        elif relation_type == "character_image":
            image_row = await session.get(CharacterImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id
        elif relation_type == "shot_frame_image":
            image_row = await session.get(ShotFrameImage, int(relation_entity_id))
            if image_row is not None and not image_row.file_id:
                image_row.file_id = file_id

    async def _runner(task_id: str, args: dict) -> None:
        async with async_session_maker() as session:
            try:
                store2 = SqlAlchemyTaskStore(session)
                await store2.set_status(task_id, TaskStatus.running)
                await store2.set_progress(task_id, 10)

                provider = str(args.get("provider") or "")
                api_key = str(args.get("api_key") or "")
                base_url = args.get("base_url")
                input_dict = dict(args.get("input") or {})

                task = ImageGenerationTask(
                    provider_config=ProviderConfig(
                        provider=provider,  # type: ignore[arg-type]
                        api_key=api_key,
                        base_url=base_url,
                    ),
                    input_=ImageGenerationInput.model_validate(input_dict),
                )
                await task.run()
                result = await task.get_result()
                if result is None:
                    raise RuntimeError("Image generation task returned no result")

                await store2.set_result(task_id, result.model_dump())
                # 任务成功后，自动将生成图片上传到 S3 并落库到资产图片表
                await _persist_images_to_assets(
                    session,
                    task_id=task_id,
                    relation_type=relation_type,
                    relation_entity_id=relation_entity_id,
                    result=result,
                )
                await store2.set_progress(task_id, 100)
                await store2.set_status(task_id, TaskStatus.succeeded)
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                async with async_session_maker() as s2:
                    store3 = SqlAlchemyTaskStore(s2)
                    await store3.set_error(task_id, str(exc))
                    await store3.set_status(task_id, TaskStatus.failed)
                    await s2.commit()

    import asyncio

    asyncio.create_task(_runner(task_record.id, run_args))
    return TaskCreated(task_id=task_record.id)


@router.post(
    "/actor-images/{actor_image_id}/image-tasks",
    response_model=ApiResponse[TaskCreated],
    status_code=status.HTTP_201_CREATED,
    summary="演员形象/立绘图片生成（任务版）",
)
async def create_actor_image_generation_task(
    actor_image_id: str,
    body: StudioImageTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TaskCreated]:
    """为指定演员形象创建图片生成任务，并通过 `GenerationTaskLink` 关联。

    - path 参数 actor_image_id 表示 ActorImage.id
    - body.image_id 必须为该 ActorImage 下的 ActorImageImage.id
    """
    actor_image = await db.get(ActorImage, actor_image_id)
    if actor_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ActorImage not found")
    if body.image_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id is required for actor image generation",
        )
    image_row = await db.get(ActorImageImage, body.image_id)
    if image_row is None or image_row.actor_image_id != actor_image_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id does not belong to given actor_image_id",
        )
    is_front_view = _is_front_view(image_row.view_angle)
    category = _asset_prompt_category(
        relation_type="actor_image_image",
        is_front_view=is_front_view,
    )
    prompt = await _build_prompt_with_template(
        db,
        category=category,
        variables={
            "name": actor_image.name,
            "description": actor_image.description,
            "tags": ", ".join(actor_image.tags or []),
            "view_angle": _map_view_angle_for_prompt(image_row.view_angle),
            "quality_level": image_row.quality_level,
            "format": image_row.format,
        },
        fallback_prompt=actor_image.description,
        not_found_msg="ActorImage.description is empty",
    )
    
    created = await _create_image_task_and_link(
        db=db,
        model_id=body.model_id,
        relation_type="actor_image_image",
        relation_entity_id=str(image_row.id),
        prompt=prompt,
        images=(
            []
            if is_front_view
            else [ref]
            if (ref := await _resolve_front_image_ref(
                db,
                image_model=ActorImageImage,
                parent_field_name="actor_image_id",
                parent_id=actor_image_id,
                preferred_quality_level=image_row.quality_level,
            ))
            else []
        ),
    )
    return success_response(created, code=201)


@router.post(
    "/assets/{asset_type}/{asset_id}/image-tasks",
    response_model=ApiResponse[TaskCreated],
    status_code=status.HTTP_201_CREATED,
    summary="道具/场景/服装图片生成（任务版）",
)
async def create_asset_image_generation_task(
    asset_type: str,
    asset_id: str,
    body: StudioImageTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TaskCreated]:
    """为道具/场景/服装创建图片生成任务。

    - asset_type: prop / scene / costume
    - path 参数 asset_id 为对应资产 ID
    - body.image_id 必须为该资产下对应图片表记录的 ID（PropImage/SceneImage/CostumeImage）
    """
    if body.image_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id is required for asset image generation",
        )

    asset_type_norm = asset_type.strip().lower()
    if asset_type_norm == "prop":
        asset = await db.get(Prop, asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prop not found")
        image_row = await db.get(PropImage, body.image_id)
        if image_row is None or image_row.prop_id != asset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_id does not belong to given prop_id",
            )
        relation_type = "prop_image"
        is_front_view = _is_front_view(image_row.view_angle)
        category = _asset_prompt_category(
            relation_type=relation_type,
            is_front_view=is_front_view,
        )
        prompt = await _build_prompt_with_template(
            db,
            category=category,
            variables={
                "name": asset.name,
                "description": asset.description,
                "tags": ", ".join(asset.tags or []),
                "view_angle": _map_view_angle_for_prompt(image_row.view_angle),
                "quality_level": image_row.quality_level,
                "format": image_row.format,
            },
            fallback_prompt=asset.description,
            not_found_msg="Prop.description is empty",
        )
        images = (
            []
            if is_front_view
            else [ref]
            if (ref := await _resolve_front_image_ref(
                db,
                image_model=PropImage,
                parent_field_name="prop_id",
                parent_id=asset_id,
                preferred_quality_level=image_row.quality_level,
            ))
            else []
        )
    elif asset_type_norm == "scene":
        asset = await db.get(Scene, asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found")
        image_row = await db.get(SceneImage, body.image_id)
        if image_row is None or image_row.scene_id != asset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_id does not belong to given scene_id",
            )
        relation_type = "scene_image"
        is_front_view = _is_front_view(image_row.view_angle)
        category = _asset_prompt_category(
            relation_type=relation_type,
            is_front_view=is_front_view,
        )
        prompt = await _build_prompt_with_template(
            db,
            category=category,
            variables={
                "name": asset.name,
                "description": asset.description,
                "tags": ", ".join(asset.tags or []),
                "view_angle": _map_view_angle_for_prompt(image_row.view_angle),
                "quality_level": image_row.quality_level,
                "format": image_row.format,
            },
            fallback_prompt=asset.description,
            not_found_msg="Scene.description is empty",
        )
        images = (
            []
            if is_front_view
            else [ref]
            if (ref := await _resolve_front_image_ref(
                db,
                image_model=SceneImage,
                parent_field_name="scene_id",
                parent_id=asset_id,
                preferred_quality_level=image_row.quality_level,
            ))
            else []
        )
    elif asset_type_norm == "costume":
        asset = await db.get(Costume, asset_id)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Costume not found")
        image_row = await db.get(CostumeImage, body.image_id)
        if image_row is None or image_row.costume_id != asset_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="image_id does not belong to given costume_id",
            )
        relation_type = "costume_image"
        is_front_view = _is_front_view(image_row.view_angle)
        category = _asset_prompt_category(
            relation_type=relation_type,
            is_front_view=is_front_view,
        )
        prompt = await _build_prompt_with_template(
            db,
            category=category,
            variables={
                "name": asset.name,
                "description": asset.description,
                "tags": ", ".join(asset.tags or []),
                "view_angle": _map_view_angle_for_prompt(image_row.view_angle),
                "quality_level": image_row.quality_level,
                "format": image_row.format,
            },
            fallback_prompt=asset.description,
            not_found_msg="Costume.description is empty",
        )
        images = (
            []
            if is_front_view
            else [ref]
            if (ref := await _resolve_front_image_ref(
                db,
                image_model=CostumeImage,
                parent_field_name="costume_id",
                parent_id=asset_id,
                preferred_quality_level=image_row.quality_level,
            ))
            else []
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="asset_type must be one of: prop/scene/costume",
        )

    created = await _create_image_task_and_link(
        db=db,
        model_id=body.model_id,
        relation_type=relation_type,
        relation_entity_id=str(body.image_id),
        prompt=prompt,
        images=images,
    )
    return success_response(created, code=201)


@router.post(
    "/characters/{character_id}/image-tasks",
    response_model=ApiResponse[TaskCreated],
    status_code=status.HTTP_201_CREATED,
    summary="角色图片生成（任务版）",
)
async def create_character_image_generation_task(
    character_id: str,
    body: StudioImageTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TaskCreated]:
    """为角色创建图片生成任务（对应 CharacterImage 业务）。

    - path 参数 character_id 为 Character.id
    - body.image_id 必须为该角色下的 CharacterImage.id
    """
    character = await db.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    if body.image_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id is required for character image generation",
        )
    image_row = await db.get(CharacterImage, body.image_id)
    if image_row is None or image_row.character_id != character_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id does not belong to given character_id",
        )
    prompt = await _build_prompt_with_template(
        db,
        category=PromptCategory.combined,
        variables={
            "name": character.name,
            "description": character.description,
            "view_angle": _map_view_angle_for_prompt(image_row.view_angle),
            "quality_level": image_row.quality_level,
            "format": image_row.format,
        },
        fallback_prompt=character.description,
        not_found_msg="Character.description is empty",
    )
    created = await _create_image_task_and_link(
        db=db,
        model_id=body.model_id,
        relation_type="character_image",
        relation_entity_id=str(image_row.id),
        prompt=prompt,
    )
    return success_response(created, code=201)


@router.post(
    "/shot-details/{shot_detail_id}/frame-image-tasks",
    response_model=ApiResponse[TaskCreated],
    status_code=status.HTTP_201_CREATED,
    summary="镜头分镜帧图片生成（任务版）",
)
async def create_shot_frame_image_generation_task(
    shot_detail_id: str,
    body: StudioImageTaskRequest,
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[TaskCreated]:
    """为镜头分镜帧（ShotDetail）创建图片生成任务。

    - path 参数 shot_detail_id 为 ShotDetail.id
    - body.image_id 必须为该分镜下的 ShotFrameImage.id
    - relation_type 固定为 shot_frame_image，relation_entity_id 为 ShotFrameImage.id
    """
    shot_detail = await db.get(ShotDetail, shot_detail_id)
    if shot_detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ShotDetail not found")
    if body.image_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id is required for shot frame image generation",
        )
    image_row = await db.get(ShotFrameImage, body.image_id)
    if image_row is None or image_row.shot_detail_id != shot_detail_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_id does not belong to given shot_detail_id",
        )
    # ShotDetail 无 description：默认优先 key_frame_prompt，其次 first/last。
    prompt = (
        (shot_detail.key_frame_prompt or "").strip()
        or (shot_detail.first_frame_prompt or "").strip()
        or (shot_detail.last_frame_prompt or "").strip()
    )
    if not prompt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ShotDetail has no frame prompt (key/first/last are all empty)",
        )
    prompt = await _build_prompt_with_template(
        db,
        category=_shot_frame_prompt_category(image_row.frame_type),
        variables={
            "description": shot_detail.description,
            "atmosphere": shot_detail.atmosphere,
            "mood_tags": ", ".join(shot_detail.mood_tags or []),
            "camera_shot": shot_detail.camera_shot,
            "angle": shot_detail.angle,
            "movement": shot_detail.movement,
            "frame_type": image_row.frame_type,
            "first_frame_prompt": shot_detail.first_frame_prompt,
            "last_frame_prompt": shot_detail.last_frame_prompt,
            "key_frame_prompt": shot_detail.key_frame_prompt,
            "base_prompt": prompt,
        },
        fallback_prompt=prompt,
        not_found_msg="ShotDetail has no frame prompt (key/first/last are all empty)",
    )
    created = await _create_image_task_and_link(
        db=db,
        model_id=body.model_id,
        relation_type="shot_frame_image",
        relation_entity_id=str(image_row.id),
        prompt=prompt,
    )
    return success_response(created, code=201)


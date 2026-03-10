"""AI Studio/影视制作相关的数据库模型。

注释约定：
- 只解释“为什么这样设计/有什么约束”，避免复述字段名含义。
- 与前端 `front/src/mocks/data.ts` 对齐，但为可扩展性做了适度规范化（如对话拆表）。

资产语义约定（避免歧义）：
- 资产拆为四张独立表：演员形象/立绘（ActorImage）、场景（Scene）、道具（Prop）、服装（Costume）。
- 各表“归属/范围”由 `project_id` / `chapter_id` 表达；在分镜中的引用由对应 Link 表表达（ShotActorImageLink / ShotSceneLink / ShotPropLink / ShotCostumeLink）。

应用层保证（在类/字段注释中标记为「应用层保证」或「应用层需保证」）：
- 跨项目引用一致性：对白说话人/听者、角色服装与道具、镜头关联的场景/道具/服装/演员形象，应与镜头/角色所属项目一致或为全局资产。
- 主图唯一：各 *Image 表中同一父实体下至多一条 is_primary=True，需在写入/更新时维护。
- 全局演员重名：若业务要求全局演员也不重名，需在应用层校验。
- 时间线归属：TimelineClip 无 project/chapter 字段，若需按项目/章节维度的时间线，需在应用层绑定归属。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import TimestampMixin


class ProjectStyle(str, Enum):
    """项目题材/风格维度（不用于区分真人/动漫）。"""

    realism = "现实主义"
    scifi = "科幻"
    ancient = "古风"
    urban_comedy = "都市喜剧"


class ProjectVisualStyle(str, Enum):
    """画面表现形式维度：用于区分真人/动漫等。"""

    live_action = "真人"
    anime = "动漫"


class ChapterStatus(str, Enum):
    """章节生产状态。"""

    draft = "draft"
    shooting = "shooting"
    done = "done"


class ShotStatus(str, Enum):
    """镜头生成状态（更多是“生产流程”而非剧情状态）。"""

    pending = "pending"
    generating = "generating"
    ready = "ready"


class CameraShotType(str, Enum):
    """景别（与 `app.core.skills_runtime.schemas.ShotType` 对齐，存英文 code）。"""

    ecu = "ECU"  # 大特写
    cu = "CU"  # 特写
    mcu = "MCU"  # 中近景
    ms = "MS"  # 中景
    mls = "MLS"  # 中远景
    ls = "LS"  # 远景
    els = "ELS"  # 大远景


class CameraAngle(str, Enum):
    """机位角度（与 `app.core.skills_runtime.schemas.CameraAngle` 对齐，存英文 code）。"""

    eye_level = "EYE_LEVEL" # 平视
    high_angle = "HIGH_ANGLE" # 高角度
    low_angle = "LOW_ANGLE" # 低角度
    bird_eye = "BIRD_EYE" # 鸟瞰
    dutch = "DUTCH" # 荷兰式
    over_shoulder = "OVER_SHOULDER" # 过肩


class CameraMovement(str, Enum):
    """运镜方式（与 `app.core.skills_runtime.schemas.CameraMovement` 对齐，存英文 code）。"""

    static = "STATIC" # 静止
    pan = "PAN" # 平移
    tilt = "TILT" # 倾斜
    dolly_in = "DOLLY_IN" # 拉近
    dolly_out = "DOLLY_OUT" # 拉远
    track = "TRACK" # 轨道
    crane = "CRANE" # 摇臂
    handheld = "HANDHELD" # 手持
    steadicam = "STEADICAM" # 稳定器
    zoom_in = "ZOOM_IN"
    zoom_out = "ZOOM_OUT" # 拉近


class AssetQualityLevel(str, Enum):
    """资产精度等级（由低到高逐步补齐更多角度/细节图）。"""

    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"
    ultra = "ULTRA"


class AssetViewAngle(str, Enum):
    """资产图片角度（用于多视图描述同一资产）。"""

    front = "FRONT"
    left = "LEFT"
    right = "RIGHT"
    back = "BACK"
    three_quarter = "THREE_QUARTER"
    top = "TOP"
    detail = "DETAIL"


class FileType(str, Enum):
    """文件类型（用于素材库与时间线引用）。"""

    image = "image"
    video = "video"


class TimelineClipType(str, Enum):
    """时间线片段类型（视频/音频）。"""

    video = "video"
    audio = "audio"


class DialogueLineMode(str, Enum):
    """对白模式（与 `app.core.skills_runtime.schemas.DialogueLineMode` 对齐，存英文 code）。"""

    dialogue = "DIALOGUE" # 对白
    voice_over = "VOICE_OVER" # 旁白
    off_screen = "OFF_SCREEN" # 画外音
    phone = "PHONE" # 电话声


class VFXType(str, Enum):
    """视效类型（与 `app.core.skills_runtime.schemas.VFXType` 对齐，存英文 code）。"""

    none = "NONE" # 无
    particles = "PARTICLES" # 粒子
    volumetric_fog = "VOLUMETRIC_FOG" # 体积雾
    cg_double = "CG_DOUBLE" # 数字替身
    digital_environment = "DIGITAL_ENVIRONMENT" # 数字场景
    matte_painting = "MATTE_PAINTING" # 绘景
    fire_smoke = "FIRE_SMOKE" # 烟火
    water_sim = "WATER_SIM" # 水效
    destruction = "DESTRUCTION" # 破碎/解算
    energy_magic = "ENERGY_MAGIC" # 能量/魔法
    compositing_cleanup = "COMPOSITING_CLEANUP" # 合成/修脏
    slow_motion_time = "SLOW_MOTION_TIME" # 升格/慢动作
    other = "OTHER" # 其他


class Project(Base, TimestampMixin):
    """项目表。

    说明：
    - `stats` 使用 JSON 存储聚合统计，便于快速渲染与渐进扩展；如需要强一致统计可后续改为物化/触发器维护。
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="项目 ID")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="项目名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="项目简介")
    style: Mapped[ProjectStyle] = mapped_column(String(32), nullable=False, comment="题材/风格")
    visual_style: Mapped[ProjectVisualStyle] = mapped_column(
        String(16),
        nullable=False,
        default=ProjectVisualStyle.live_action,
        comment="画面表现形式（真人/动漫等）",
    )
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="随机种子")
    unify_style: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否统一风格（跨章节）")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="进度百分比（0-100）")
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, comment="聚合统计（JSON）")

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    characters: Mapped[list["Character"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    actor_images: Mapped[list["ActorImage"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    scenes: Mapped[list["Scene"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    props: Mapped[list["Prop"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    costumes: Mapped[list["Costume"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files: Mapped[list["FileItem"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_projects_updated_at", "updated_at"),
        Index("ix_projects_style", "style"),
        Index("ix_projects_visual_style", "visual_style"),
    )


class Chapter(Base, TimestampMixin):
    """章节表。

    约束：
    - `project_id + index` 唯一，保证一个项目内集数序号不重复。
    """

    __tablename__ = "chapters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="章节 ID")
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, comment="章节序号（项目内唯一）")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="章节标题")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="章节摘要")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="章节原文（未清洗/可较长）")
    condensed_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="由模型精简后的原文（用于抽取/提示词）")
    storyboard_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="分镜数量")
    status: Mapped[ChapterStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ChapterStatus.draft,
        comment="章节状态",
    )

    project: Mapped["Project"] = relationship(back_populates="chapters")
    shots: Mapped[list["Shot"]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files: Mapped[list["FileItem"]] = relationship(back_populates="chapter")

    __table_args__ = (
        UniqueConstraint("project_id", "index", name="uq_chapters_project_index"),
        Index("ix_chapters_updated_at", "updated_at"),
        Index("ix_chapters_status", "status"),
    )


class ActorImage(Base, TimestampMixin):
    """演员形象/立绘表。归属由 project_id/chapter_id 表达；镜头引用见 ShotActorImageLink。"""

    __tablename__ = "actor_images"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID")
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属项目 ID",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属章节 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="描述")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签")
    prompt_template_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="提示词模板 ID",
    )

    project: Mapped["Project | None"] = relationship(back_populates="actor_images")
    chapter: Mapped["Chapter | None"] = relationship()
    prompt_template: Mapped["PromptTemplate | None"] = relationship()
    images: Mapped[list["ActorImageImage"]] = relationship(
        back_populates="actor_image",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ActorImageImage.id",
    )
    shot_links: Mapped[list["ShotActorImageLink"]] = relationship(
        back_populates="actor_image",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_actor_images_name", "name"),
        Index("ix_actor_images_project_chapter", "project_id", "chapter_id"),
    )


class Scene(Base, TimestampMixin):
    """场景表。归属由 project_id/chapter_id 表达；镜头引用见 ShotSceneLink。"""

    __tablename__ = "scenes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID")
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属项目 ID",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属章节 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="描述")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签")
    prompt_template_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="提示词模板 ID",
    )

    project: Mapped["Project | None"] = relationship(back_populates="scenes")
    chapter: Mapped["Chapter | None"] = relationship()
    prompt_template: Mapped["PromptTemplate | None"] = relationship()
    images: Mapped[list["SceneImage"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SceneImage.id",
    )
    shot_links: Mapped[list["ShotSceneLink"]] = relationship(
        back_populates="scene",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_scenes_name", "name"),
        Index("ix_scenes_project_chapter", "project_id", "chapter_id"),
    )


class Prop(Base, TimestampMixin):
    """道具表。归属由 project_id/chapter_id 表达；镜头引用见 ShotPropLink；角色道具绑定见 CharacterPropLink。"""

    __tablename__ = "props"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID")
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属项目 ID",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属章节 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="描述")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签")
    prompt_template_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="提示词模板 ID",
    )

    project: Mapped["Project | None"] = relationship(back_populates="props")
    chapter: Mapped["Chapter | None"] = relationship()
    prompt_template: Mapped["PromptTemplate | None"] = relationship()
    images: Mapped[list["PropImage"]] = relationship(
        back_populates="prop",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PropImage.id",
    )
    shot_links: Mapped[list["ShotPropLink"]] = relationship(
        back_populates="prop",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    character_prop_links: Mapped[list["CharacterPropLink"]] = relationship(
        back_populates="prop",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_props_name", "name"),
        Index("ix_props_project_chapter", "project_id", "chapter_id"),
    )


class Costume(Base, TimestampMixin):
    """服装表。归属由 project_id/chapter_id 表达；镜头引用见 ShotCostumeLink；角色服装见 Character.costume_id。"""

    __tablename__ = "costumes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="ID")
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属项目 ID",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属章节 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="描述")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签")
    prompt_template_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("prompt_templates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="提示词模板 ID",
    )

    project: Mapped["Project | None"] = relationship(back_populates="costumes")
    chapter: Mapped["Chapter | None"] = relationship()
    prompt_template: Mapped["PromptTemplate | None"] = relationship()
    images: Mapped[list["CostumeImage"]] = relationship(
        back_populates="costume",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CostumeImage.id",
    )
    shot_links: Mapped[list["ShotCostumeLink"]] = relationship(
        back_populates="costume",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    characters: Mapped[list["Character"]] = relationship(back_populates="costume")

    __table_args__ = (
        Index("ix_costumes_name", "name"),
        Index("ix_costumes_project_chapter", "project_id", "chapter_id"),
    )


class Shot(Base,TimestampMixin):
    """镜头表（基础信息）。

    说明：
    - 目前未混入 `TimestampMixin`，避免在高频生成/更新流程中引入额外写放大；如需要审计可再补。
    - 细节放在 `ShotDetail` 以保持主表字段稳定。
    """

    __tablename__ = "shots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="镜头 ID")
    chapter_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属章节 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, comment="镜头序号（章节内唯一）")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="镜头标题")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL/路径")
    status: Mapped[ShotStatus] = mapped_column(
        String(32),
        nullable=False,
        default=ShotStatus.pending,
        comment="镜头状态",
    )
    script_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="剧本摘录")

    chapter: Mapped["Chapter"] = relationship(back_populates="shots")
    detail: Mapped["ShotDetail"] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    scene_links: Mapped[list["ShotSceneLink"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotSceneLink.index",
    )
    prop_links: Mapped[list["ShotPropLink"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotPropLink.index",
    )
    costume_links: Mapped[list["ShotCostumeLink"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotCostumeLink.index",
    )
    actor_image_links: Mapped[list["ShotActorImageLink"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotActorImageLink.index",
    )
    character_links: Mapped[list["ShotCharacterLink"]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotCharacterLink.index",
    )

    __table_args__ = (
        UniqueConstraint("chapter_id", "index", name="uq_shots_chapter_index"),
        Index("ix_shots_status", "status"),
    )


class ShotDetail(Base,TimestampMixin):
    """镜头细节（1:1）。

    设计点：
    - 与 `Shot` 共享主键，确保一条镜头最多一份细节，且删除镜头时细节级联删除。
    - `mood_tags` 用 JSON 存 list[str]，与前端 mock 对齐。

    应用层保证：
    - `scene_id` 所指场景应与镜头所属项目一致或为全局场景，避免跨项目引用。
    """

    __tablename__ = "shot_details"

    # 与 Shot 共享主键（1:1）：用外键作为主键，强制一对一。
    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("shots.id", ondelete="CASCADE"),
        primary_key=True,
        comment="镜头 ID（与 shots.id 共享主键）",
    )
    camera_shot: Mapped[CameraShotType] = mapped_column(
        String(16),
        nullable=False,
        comment="景别（存 code：ECU/CU/MCU/MS/MLS/LS/ELS；展示可用 schemas.SHOT_TYPE_ZH）",
    )
    angle: Mapped[CameraAngle] = mapped_column(
        String(16),
        nullable=False,
        comment="机位角度（存 code：EYE_LEVEL/HIGH_ANGLE/...；展示可用 schemas.CAMERA_ANGLE_ZH）",
    )
    movement: Mapped[CameraMovement] = mapped_column(
        String(16),
        nullable=False,
        comment="运镜方式（存 code：STATIC/PAN/...；展示可用 schemas.CAMERA_MOVEMENT_ZH）",
    )
    scene_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("scenes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联场景 ID（可空）；应用层需保证与镜头所属项目一致或全局",
    )
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="时长（秒）；镜头唯一时长来源")
    mood_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="情绪标签（JSON 数组）")
    atmosphere: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="氛围描述")
    follow_atmosphere: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否沿用氛围")
    has_bgm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否包含 BGM")
    vfx_type: Mapped[VFXType] = mapped_column(
        String(32),
        nullable=False,
        default=VFXType.none,
        comment="视效类型（存 code；展示可用 schemas.VFX_TYPE_ZH）",
    )
    vfx_note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="视效说明（简短可执行）")

    shot: Mapped["Shot"] = relationship(back_populates="detail")
    scene: Mapped["Scene | None"] = relationship()
    dialog_lines: Mapped[list["ShotDialogLine"]] = relationship(
        back_populates="shot_detail",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotDialogLine.index",
    )

    __table_args__ = (
        Index("ix_shot_details_camera_shot", "camera_shot"),
        Index("ix_shot_details_angle", "angle"),
    )


class ShotDialogLine(Base,TimestampMixin):
    """镜头对话行。

    设计点：
    - 前端 mock 里 `dialog` 为数组，这里拆表以支持排序、检索与后续对齐字幕/配音等能力。
    - `shot_detail_id + index` 唯一，保证行号稳定。

    应用层保证：
    - `speaker_character_id` / `target_character_id` 所指角色应与该镜头所属章节/项目一致，避免跨项目引用。
    """

    __tablename__ = "shot_dialog_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="对话行自增 ID")
    shot_detail_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("shot_details.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属镜头细节 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="行号（镜头内排序）")
    text: Mapped[str] = mapped_column(Text, nullable=False, comment="台词内容")
    line_mode: Mapped[DialogueLineMode] = mapped_column(
        String(16),
        nullable=False,
        default=DialogueLineMode.dialogue,
        comment="对白模式（DIALOGUE/VOICE_OVER/OFF_SCREEN/PHONE；对齐 schemas.DIALOGUE_LINE_MODE_ZH）",
    )
    speaker_character_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("characters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="说话角色 ID；应用层需保证与镜头所属项目一致",
    )
    target_character_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("characters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="听者角色 ID；应用层需保证与镜头所属项目一致",
    )

    shot_detail: Mapped["ShotDetail"] = relationship(back_populates="dialog_lines")
    speaker_character: Mapped["Character | None"] = relationship(foreign_keys=[speaker_character_id])
    target_character: Mapped["Character | None"] = relationship(foreign_keys=[target_character_id])

    __table_args__ = (
        UniqueConstraint("shot_detail_id", "index", name="uq_shot_dialog_lines_shot_index"),
    )


class Actor(Base, TimestampMixin):
    """演员表（与角色区分）。

    说明：
    - Actor 表示“表演者/演员”，可全局或项目级复用；角色（Character）归属项目并引用 Actor。
    - 项目级：`(project_id, name)` 唯一；全局（project_id 为空）时数据库不约束重名。

    应用层保证：
    - 若业务要求全局演员也不重名，需在应用层校验或约束。
    """

    __tablename__ = "actors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="演员 ID")
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="归属项目 ID（为空=全局演员）",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="演员名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="演员描述/备注")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="演员头像/缩略图")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签（JSON 数组）")

    project: Mapped["Project | None"] = relationship()
    characters: Mapped[list["Character"]] = relationship(back_populates="actor")

    __table_args__ = (
        Index("ix_actors_name", "name"),
        UniqueConstraint("project_id", "name", name="uq_actors_project_name"),
    )


class Character(Base, TimestampMixin):
    """角色表（归属项目）。

    组成约定：
    - 角色由：Actor（演员） + Costume（服装） + Props（道具）组成。
    - 最终在分镜中引用角色：`ShotCharacterLink`（shot_character_links）。

    应用层保证：
    - `costume_id` 所指服装应为该角色所属项目或全局资产，避免跨项目误用。
    """

    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="角色 ID")
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="角色名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="角色描述")
    actor_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("actors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="对应演员 ID（可空）",
    )
    costume_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("costumes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="服装 ID（可空）；应用层需保证与角色同项目或全局",
    )

    project: Mapped["Project"] = relationship(back_populates="characters")
    actor: Mapped["Actor | None"] = relationship(back_populates="characters")
    costume: Mapped["Costume | None"] = relationship(back_populates="characters")
    prop_links: Mapped[list["CharacterPropLink"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="CharacterPropLink.index",
    )
    shot_links: Mapped[list["ShotCharacterLink"]] = relationship(
        back_populates="character",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_characters_name", "name"),
        UniqueConstraint("project_id", "name", name="uq_characters_project_name"),
    )


class CharacterPropLink(Base,TimestampMixin):
    """角色与道具绑定（多对多）。

    应用层保证：
    - `prop_id` 所指道具应为该角色所属项目或全局资产，避免跨项目误用。
    """

    __tablename__ = "character_prop_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    character_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="角色 ID",
    )
    prop_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("props.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="道具 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="角色道具排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注（可选）")

    character: Mapped["Character"] = relationship(back_populates="prop_links")
    prop: Mapped["Prop"] = relationship(back_populates="character_prop_links")

    __table_args__ = (
        UniqueConstraint("character_id", "prop_id", name="uq_character_prop_links_character_prop"),
        UniqueConstraint("character_id", "index", name="uq_character_prop_links_character_index"),
    )


class ShotCharacterLink(Base,TimestampMixin):
    """镜头引用角色（多对多）。

    应用层保证：
    - 所引用角色应与镜头所属项目一致（角色表本身归属项目，写入时校验可避免跨项目引用）。
    """

    __tablename__ = "shot_character_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="镜头 ID",
    )
    character_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="角色 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="镜头内角色排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注（可选）")

    shot: Mapped["Shot"] = relationship(back_populates="character_links")
    character: Mapped["Character"] = relationship(back_populates="shot_links")

    __table_args__ = (
        UniqueConstraint("shot_id", "character_id", name="uq_shot_character_links_shot_character"),
        UniqueConstraint("shot_id", "index", name="uq_shot_character_links_shot_index"),
    )


class ActorImageImage(Base, TimestampMixin):
    """演员形象/立绘多角度图片。

    应用层保证：
    - 同一 `actor_image_id` 下至多一条 `is_primary=True`；库表无唯一约束，需在写入/更新时保证。
    """

    __tablename__ = "actor_image_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="图片行 ID")
    actor_image_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("actor_images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属演员形象 ID",
    )
    quality_level: Mapped[AssetQualityLevel] = mapped_column(
        String(16),
        nullable=False,
        default=AssetQualityLevel.low,
        index=True,
        comment="精度等级",
    )
    view_angle: Mapped[AssetViewAngle] = mapped_column(
        String(32),
        nullable=False,
        default=AssetViewAngle.front,
        index=True,
        comment="视角",
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False, comment="图片 URL")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="宽（px）")
    height: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="高（px）")
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="png", comment="格式")
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否主图；应用层需保证同一演员形象下至多一张主图",
    )

    actor_image: Mapped["ActorImage"] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("actor_image_id", "quality_level", "view_angle", name="uq_actor_image_images_quality_angle"),
    )


class SceneImage(Base, TimestampMixin):
    """场景多角度图片。

    应用层保证：
    - 同一 `scene_id` 下至多一条 `is_primary=True`；库表无唯一约束，需在写入/更新时保证。
    """

    __tablename__ = "scene_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="图片行 ID")
    scene_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("scenes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属场景 ID",
    )
    quality_level: Mapped[AssetQualityLevel] = mapped_column(String(16), nullable=False, default=AssetQualityLevel.low, index=True, comment="精度等级")
    view_angle: Mapped[AssetViewAngle] = mapped_column(String(32), nullable=False, default=AssetViewAngle.front, index=True, comment="视角")
    url: Mapped[str] = mapped_column(String(2048), nullable=False, comment="图片 URL")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="png")
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否主图；应用层需保证同一场景下至多一张主图",
    )

    scene: Mapped["Scene"] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("scene_id", "quality_level", "view_angle", name="uq_scene_images_quality_angle"),
    )


class PropImage(Base, TimestampMixin):
    """道具多角度图片。

    应用层保证：
    - 同一 `prop_id` 下至多一条 `is_primary=True`；库表无唯一约束，需在写入/更新时保证。
    """

    __tablename__ = "prop_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="图片行 ID")
    prop_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("props.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属道具 ID",
    )
    quality_level: Mapped[AssetQualityLevel] = mapped_column(String(16), nullable=False, default=AssetQualityLevel.low, index=True)
    view_angle: Mapped[AssetViewAngle] = mapped_column(String(32), nullable=False, default=AssetViewAngle.front, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="png")
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否主图；应用层需保证同一道具下至多一张主图",
    )

    prop: Mapped["Prop"] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("prop_id", "quality_level", "view_angle", name="uq_prop_images_quality_angle"),
    )


class CostumeImage(Base, TimestampMixin):
    """服装多角度图片。

    应用层保证：
    - 同一 `costume_id` 下至多一条 `is_primary=True`；库表无唯一约束，需在写入/更新时保证。
    """

    __tablename__ = "costume_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="图片行 ID")
    costume_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("costumes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属服装 ID",
    )
    quality_level: Mapped[AssetQualityLevel] = mapped_column(String(16), nullable=False, default=AssetQualityLevel.low, index=True)
    view_angle: Mapped[AssetViewAngle] = mapped_column(String(32), nullable=False, default=AssetViewAngle.front, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format: Mapped[str] = mapped_column(String(32), nullable=False, default="png")
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否主图；应用层需保证同一服装下至多一张主图",
    )

    costume: Mapped["Costume"] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("costume_id", "quality_level", "view_angle", name="uq_costume_images_quality_angle"),
    )


class ShotActorImageLink(Base,TimestampMixin):
    """镜头引用演员形象/立绘（多对多）。

    应用层保证：
    - 所引用的演员形象应与镜头所属项目一致或为全局资产，避免跨项目引用。
    """

    __tablename__ = "shot_actor_image_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    shot_id: Mapped[str] = mapped_column(String(64), ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True, comment="镜头 ID")
    actor_image_id: Mapped[str] = mapped_column(String(64), ForeignKey("actor_images.id", ondelete="CASCADE"), nullable=False, index=True, comment="演员形象 ID")
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="镜头内排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")

    shot: Mapped["Shot"] = relationship(back_populates="actor_image_links")
    actor_image: Mapped["ActorImage"] = relationship(back_populates="shot_links")

    __table_args__ = (
        UniqueConstraint("shot_id", "actor_image_id", name="uq_shot_actor_image_links_shot_actor_image"),
        UniqueConstraint("shot_id", "index", name="uq_shot_actor_image_links_shot_index"),
    )


class ShotSceneLink(Base,TimestampMixin):
    """镜头引用场景（多对多）。

    应用层保证：
    - 所引用场景应与镜头所属项目一致或为全局资产，避免跨项目引用。
    """

    __tablename__ = "shot_scene_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    shot_id: Mapped[str] = mapped_column(String(64), ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True, comment="镜头 ID")
    scene_id: Mapped[str] = mapped_column(String(64), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True, comment="场景 ID")
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="镜头内排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")

    shot: Mapped["Shot"] = relationship(back_populates="scene_links")
    scene: Mapped["Scene"] = relationship(back_populates="shot_links")

    __table_args__ = (
        UniqueConstraint("shot_id", "scene_id", name="uq_shot_scene_links_shot_scene"),
        UniqueConstraint("shot_id", "index", name="uq_shot_scene_links_shot_index"),
    )


class ShotPropLink(Base,TimestampMixin):
    """镜头引用道具（多对多）。

    应用层保证：
    - 所引用道具应与镜头所属项目一致或为全局资产，避免跨项目引用。
    """

    __tablename__ = "shot_prop_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    shot_id: Mapped[str] = mapped_column(String(64), ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True, comment="镜头 ID")
    prop_id: Mapped[str] = mapped_column(String(64), ForeignKey("props.id", ondelete="CASCADE"), nullable=False, index=True, comment="道具 ID")
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="镜头内排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")

    shot: Mapped["Shot"] = relationship(back_populates="prop_links")
    prop: Mapped["Prop"] = relationship(back_populates="shot_links")

    __table_args__ = (
        UniqueConstraint("shot_id", "prop_id", name="uq_shot_prop_links_shot_prop"),
        UniqueConstraint("shot_id", "index", name="uq_shot_prop_links_shot_index"),
    )


class ShotCostumeLink(Base,TimestampMixin):
    """镜头引用服装（多对多）。

    应用层保证：
    - 所引用服装应与镜头所属项目一致或为全局资产，避免跨项目引用。
    """

    __tablename__ = "shot_costume_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="关联行 ID")
    shot_id: Mapped[str] = mapped_column(String(64), ForeignKey("shots.id", ondelete="CASCADE"), nullable=False, index=True, comment="镜头 ID")
    costume_id: Mapped[str] = mapped_column(String(64), ForeignKey("costumes.id", ondelete="CASCADE"), nullable=False, index=True, comment="服装 ID")
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="镜头内排序")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="备注")

    shot: Mapped["Shot"] = relationship(back_populates="costume_links")
    costume: Mapped["Costume"] = relationship(back_populates="shot_links")

    __table_args__ = (
        UniqueConstraint("shot_id", "costume_id", name="uq_shot_costume_links_shot_costume"),
        UniqueConstraint("shot_id", "index", name="uq_shot_costume_links_shot_index"),
    )


class PromptCategory(str, Enum):
    """提示词模板类别。"""

    frame_head = "frame_head"
    frame_tail = "frame_tail"
    frame_key = "frame_key"
    video = "video"
    storyboard = "storyboard"
    bgm = "bgm"
    sfx = "sfx"
    role = "role"
    combined = "combined"


class PromptTemplate(Base,TimestampMixin):
    """提示词模板表。"""

    __tablename__ = "prompt_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="模板 ID")
    category: Mapped[PromptCategory] = mapped_column(String(32), nullable=False, index=True, comment="模板类别")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="模板名称")
    preview: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="预览文案")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="模板内容")
    variables: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="变量名列表（JSON 数组）")

    __table_args__ = (
        Index("ix_prompt_templates_name", "name"),
    )


class FileItem(Base, TimestampMixin):
    """素材文件表。"""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="文件 ID")
    type: Mapped[FileType] = mapped_column(String(16), nullable=False, index=True, comment="文件类型")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="文件名/标题")
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="缩略图 URL/路径")
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="标签（JSON 数组）")
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("chapters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属章节 ID（可空）",
    )

    project: Mapped["Project"] = relationship(back_populates="files")
    chapter: Mapped["Chapter"] = relationship(back_populates="files")

    __table_args__ = (
        Index("ix_files_updated_at", "updated_at"),
    )


class TimelineClip(Base):
    """时间线片段。

    说明：
    - `source_id` 为逻辑引用（例如文件/音频素材 ID），不强制外键，便于接入不同素材来源。
    - 表内无 project_id/chapter_id，不直接归属项目/章节。

    应用层保证：
    - 若业务需按项目/章节维度的时间线，需在应用层通过 source_id 或关联表绑定归属。
    """

    __tablename__ = "timeline_clips"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="片段 ID")
    type: Mapped[TimelineClipType] = mapped_column(String(16), nullable=False, index=True, comment="片段类型")
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True, comment="来源素材 ID（逻辑引用）")
    label: Mapped[str] = mapped_column(String(255), nullable=False, comment="显示标签")
    start: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="起始时间（秒）")
    end: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="结束时间（秒）")
    track: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="轨道号")

    __table_args__ = (
        Index("ix_timeline_clips_track", "track"),
    )


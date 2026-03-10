
# Jellyfish Backend

基于 FastAPI + LangChain/LangGraph + SQLAlchemy 的后端 API，使用 **uv** 管理依赖。

## 技术栈

- **FastAPI**：Web 框架
- **LangChain / LangGraph**：链与工作流编排，`PromptTemplate` 管理提示词
- **SQLAlchemy**：异步 ORM，默认 SQLite（可换 PostgreSQL 等）
- **uv**：包管理与虚拟环境

## 目录结构

```
backend/
├── pyproject.toml          # 项目与依赖（uv）
├── .python-version        # Python 版本
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # 配置（pydantic-settings）
│   ├── dependencies.py     # 依赖注入（如 get_db）
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py # 路由聚合
│   │       └── routes/     # 各模块路由
│   ├── core/
│   │   └── db.py           # SQLAlchemy 引擎与 Base
│   ├── models/             # ORM 模型
│   ├── schemas/            # Pydantic 请求/响应
│   ├── services/           # 业务逻辑
│   └── chains/             # LangChain PromptTemplate、LangGraph
├── tests/
├── .env.example
└── README.md
```

## 快速开始

### 1. 安装 uv（若未安装）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 进入 backend 并安装依赖

```bash
cd backend
uv sync
```

### 3. 配置环境

```bash
cp .env.example .env
# 按需编辑 .env
```

### 4. 初始化数据库（可选，MySQL/PostgreSQL 等）

如果你使用的是 **SQLite 默认配置**（`DATABASE_URL=sqlite+aiosqlite:///./jellyfish.db`），可以跳过本节，首次访问时会自动创建文件。

若切换到 **MySQL / PostgreSQL 等外部数据库**，建议先手动初始化表结构：

1. 在 `.env` 中配置数据库连接（示例）：

   ```env
   # SQLite（默认）
   # DATABASE_URL=sqlite+aiosqlite:///./jellyfish.db

   # MySQL（使用 aiomysql 驱动）
   # DATABASE_URL=mysql+aiomysql://user:pass@localhost:3306/jellyfish

   # PostgreSQL
   # DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/jellyfish
   ```

2. 运行初始化脚本（使用 uv）：

   ```bash
   cd backend
   uv sync               # 确保依赖已安装
   uv run python init_db.py
   ```

该脚本会导入所有 ORM 模型并调用 `Base.metadata.create_all()`，在目标数据库中创建所需的 27 张业务表。

### 5. 启动服务

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- API 文档：http://localhost:8000/docs  
- 健康检查：http://localhost:8000/health  
- API v1 示例：http://localhost:8000/api/v1/example/prompt  
- 影视技能（确认路由）：http://localhost:8000/api/v1/film （GET）；实体/分镜抽取见下方，需 **POST** 且路径带 **/api/v1** 前缀。

### 影视技能 API（需配置 OPENAI_API_KEY）

| 方法 | 完整路径 | 说明 |
|------|----------|------|
| GET  | `/api/v1/film` | 返回端点说明，用于确认路由已注册 |
| POST | `/api/v1/film/extract/entities` | 人物/地点/道具抽取 |
| POST | `/api/v1/film/extract/shotlist` | 分镜/镜头表抽取 |

未配置 `OPENAI_API_KEY` 时上述 POST 返回 503。

## 常用命令

| 命令 | 说明 |
|------|------|
| `uv sync` | 安装/同步依赖 |
| `uv add <pkg>` | 添加依赖 |
| `uv run uvicorn app.main:app --reload` | 开发运行 |
| `uv run pytest` | 运行测试 |

## 测试

### 单元测试与集成测试（Mock）

- **单元测试**：`tests/test_skills_runtime_runners.py`，对 `FilmEntityExtractor` / `FilmShotlistStoryboarder` 的 load_skill、run、format_output、extract 等行为做断言，使用 Mock Agent，无需网络与 API Key。
- **集成测试（Mock）**：`tests/test_skills_integration.py` 中的 `TestAppIntegration`、`TestSkillsPipelineIntegration`，覆盖 FastAPI 健康检查、示例路由、以及技能完整链路（真实 Prompt + Mock Agent + 真实解析）。

```bash
uv run pytest tests/test_skills_runtime_runners.py tests/test_skills_integration.py -v
```

### 真实 LLM 集成测试（可选）

带 `@pytest.mark.integration` 的用例会调用真实 ChatOpenAI（或兼容接口），需设置 `OPENAI_API_KEY`，否则自动跳过。

| 环境变量 | 说明 |
|----------|------|
| `OPENAI_API_KEY` | 必填，否则跳过真实 LLM 测试 |
| `OPENAI_BASE_URL` | 可选，API 地址（如代理、DashScope 兼容地址） |
| `OPENAI_MODEL` | 可选，模型名，默认 `gpt-4o-mini` |

**运行真实 LLM 集成测试：**

```bash
# 仅运行 integration 标记的测试
OPENAI_API_KEY=sk-xxx uv run pytest tests/test_skills_integration.py -m integration -v

# 使用自定义 base_url / model
OPENAI_API_KEY=sk-xxx OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 OPENAI_MODEL=qwen-max uv run pytest tests/test_skills_integration.py -m integration -v
```

真实 LLM 测试前请安装可选依赖：`uv sync --group dev`（含 `langchain-openai`）。

## 扩展说明

- **数据库**：在 `app/models/` 下新增模型并继承 `Base`，在 `app/core/db.py` 中可调用 `init_db()` 建表。
- **提示词**：在 `app/chains/prompts.py` 中增加 `PromptTemplate`。
- **工作流**：在 `app/chains/graphs.py` 中定义 `StateGraph` 并 `compile()`，在路由中 `ainvoke` 调用。

# FinOracle 后端

Python 3.11+、FastAPI、SQLAlchemy AsyncSession、MySQL 8.4、Redis 7.4、S3 兼容对象存储（Silo）与 Milvus。以下命令均在 `backend/` 目录执行。

## 运行

启动完整后端环境：

```sh
python3 scripts/init_env.py
docker compose up --build -d --wait
```

默认本机端口：API `8001`、MySQL `3306`、Redis `6379`、Gotenberg `3000`、对象存储 `9000`（控制台 `9001`）、Milvus `19530`。API 启动时自动运行 `alembic upgrade head`，`worker` 在后台执行解析与向量化，`dsh` 通过内部网络处理问答轮次。

本机调试（需安装 [uv](https://docs.astral.sh/uv/)）：

```sh
uv sync --locked
docker compose up -d --wait mysql redis minio milvus gotenberg
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

本机调试 worker 时先停止容器 worker（`docker compose stop worker`），再运行 `uv run python -m app.worker`。本机调试问答见 [dsh 本机调试](dsh/README.md#本机调试)。停止环境用 `docker compose down`（不要加 `--volumes` 以免清除持久卷）。

## 核心配置

完整模板见 `.env.example`，首次运行 `scripts/init_env.py` 会生成随机本地凭据（再次运行可补齐缺失的 `CHAT_DSH_TOKEN`）。

| 配置 | 默认值 / 说明 |
| --- | --- |
| `API_PORT` / `API_V1_PREFIX` | `8001` / `/api/v1` |
| `MYSQL_PASSWORD` / `JWT_SECRET_KEY` | 必填；JWT 密钥至少 32 字符 |
| `JWT_ACCESS_TOKEN_MINUTES` / `JWT_REFRESH_TOKEN_DAYS` | `30` / `7` |
| `LOGIN_MAX_FAILURES` / `LOGIN_LOCK_SECONDS` | `5` / `900` |
| `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | 三项同填或同空；启动时创建首个管理员或同步密码 |
| `S3_ENDPOINT_URL` / `S3_ACCESS_KEY` / `S3_SECRET_KEY` / `S3_BUCKET` | S3 兼容对象存储，默认桶 `finoracle-documents` |
| `GOTENBERG_URL` / `SOFFICE_PATH` | `.doc` / `.rtf` / `.odt` 转 PDF（优先 Gotenberg，其次本机 LibreOffice） |
| `AUTO_APPROVE_PARSED` | `true` 时完整解析自动进入向量化；`needs_ocr` 仍需人工批准 |
| `DASHSCOPE_API_KEY` | 阿里云百炼向量化密钥（worker 入库与 API 检索均需要） |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` / `MILVUS_URI` | 默认 `text-embedding-v4`、1024 维；更换模型后写入新 Milvus 集合 |
| `DEEPSEEK_API_KEY` / `CHAT_MODEL` / `CHAT_BASE_URL` | `dsh` 模型配置，默认 `deepseek-flash` |
| `CHAT_DSH_TOKEN` / `CHAT_DSH_URL` / `CHAT_DSH_CONTEXT_WINDOW` | FastAPI 与 `dsh` 桥接配置，默认上下文窗口 32768 tokens |

## 文档解析 CLI

`app/rag/parsing/` 支持 PDF、Word（`.docx`/`.doc`）、RTF、ODT、TXT/Markdown 与常见图片，按「解析 → 按需 OCR → 清洗 → 切片」输出带页码与条号的结构化切片。命令行无需数据库：

```sh
uv sync --locked --extra ocr
uv run python scripts/parse_document.py <文件> --output result.json
```

常用参数：`--ocr auto|rapidocr|tesseract|none`、`--chunker auto|article|item|length`、`--max-chars 800`、`--overlap 100`、`--blocks`。

## 代码组织

- `app/api/`：路由、鉴权依赖与请求绑定
- `app/services/`：认证、用户管理、制度库、个人收藏、文档入库/发布、检索与会话服务
- `app/repositories/`、`app/models/`、`app/db/`：SQLAlchemy 表模型与数据访问
- `app/security/`、`app/cache/`：Argon2、JWT 与 Redis 原子会话（命名空间 `finoracle:v2:auth:*`）
- `app/storage/`：S3 对象存储封装与对象键规则
- `app/rag/`：`parsing/`（解析/OCR/清洗/切片）、`indexing/`（向量化与 Milvus）、`retrieval/`（混合/视觉检索）与 `pipelines/`
- `app/agent/`：问答提示词、制度工具、引用校验与 `dsh` 桥接客户端
- `app/worker/`：基于 `FOR UPDATE SKIP LOCKED` 与租约续期的后台任务执行器

## 验证

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

单元测试不依赖外部服务，行覆盖率门槛为 80%。真实环境集成测试（需先启动 Compose）：

```sh
uv run python -m tests.integration
uv run python -m tests.integration --fresh-install
```

性能探针（仅允许回环地址，自动清理本轮生成的临时账号）：

```sh
uv run python scripts/benchmark.py --allow-disposable-users --samples 20 --warmup 3 --environment local-compose
```

更多接口与架构细节见 [API 说明](../docs/api.md)、[文档入库架构](../docs/document-ingestion.md) 与 [LLM 架构](../docs/llm-architecture.md)。

# FinOracle 后端

Python 3.11+、FastAPI、SQLAlchemy AsyncSession、MySQL 8.4、Redis 7.4。以下命令均在 `backend/` 目录执行。

## 运行

完整开发环境：

```sh
python3 scripts/init_env.py
docker compose up --build -d --wait
```

默认 API 端口为 8001，MySQL 为 3306，Redis 为 6379，Gotenberg（文档格式转换）为 3000；都仅绑定本机。首次启动需拉取约 420 MB 的 Gotenberg 镜像。API 启动时运行 `alembic upgrade head`。可用 `docker compose logs api` 查看启动情况。

本机调试应用需要安装 [uv](https://docs.astral.sh/uv/)，使用容器中的数据库和缓存：

```sh
uv sync --locked
docker compose up -d --wait mysql redis
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

此时接口文档位于 `http://127.0.0.1:8000/docs`。从项目根目录调用时使用 `uv --directory backend run ...`，确保读取正确的 `.env`。

停止完整环境用 `docker compose down`，数据会保留。不要为普通重启加 `--volumes`。已有 MySQL 持久卷的账号密码不会随着 `.env` 自动改变；更换密码需同步修改数据库账号。

## 接口

默认前缀为 `/api/v1`，实际请求及响应结构以 `/docs` 或 `/openapi.json` 为准。

| 方法 | 路径 | 用途 | 成功状态 |
| --- | --- | --- | --- |
| GET | `/health` | 进程存活 | 200 |
| GET | `/ready` | MySQL、Redis 就绪 | 200 |
| POST | `/auth/register` | 注册普通用户 | 201 |
| POST | `/auth/login` | 用户名或邮箱登录 | 200 |
| POST | `/auth/refresh` | 用 Refresh Token 换取新双令牌 | 200 |
| POST | `/auth/logout` | 退出当前会话 | 204 |
| GET | `/users/me` | 查询自己的公开资料 | 200 |
| PATCH | `/users/me` | 修改自己的用户名或邮箱 | 200 |
| GET | `/admin/users` | 管理员分页搜索、筛选用户 | 200 |
| GET | `/admin/users/{user_id}` | 管理员查询用户详情 | 200 |
| PATCH | `/admin/users/{user_id}/status` | 管理员启用或禁用普通用户 | 200 |
| POST | `/admin/documents/parse` | 管理员上传文档并返回解析结果（预览，不入库） | 200 |

注册提交 `username`、`email`、`password`、`confirm_password`；登录提交 `identifier`、`password`；刷新提交 `refresh_token`。退出与个人信息接口使用 `Authorization: Bearer <access_token>`。刷新接口不要求 Access Token。

Access Token 默认 30 分钟，Refresh Token 默认 7 天。刷新沿用同一会话，每次更换令牌标识，并将会话过期时间更新为新 Refresh Token 的过期时间。旧刷新令牌重放会撤销整个会话；客户端须串行刷新并替换保存的双令牌。退出会同时阻止该会话的 Access Token 和 Refresh Token。

登录标识统一去首尾空白并转小写，密码保持原样；数据库重音或不可见字符的近似匹配不会被当成同一登录标识。默认连续 5 次失败后限制该标识 15 分钟；密码验证成功后清除失败计数。受保护请求同时检查令牌、Redis 会话及数据库中的最新账号状态；Redis 不可用时拒绝认证并返回 503。

资料更新只接受非 null 的 `username`、`email`，至少一个；沿用注册规范化及唯一性规则。管理员列表默认 `page=1`、`page_size=20`（最多 100），支持 `q`、`role`、`status`。服务端验证数据库中的当前管理员角色。禁用使该用户全部旧登录失效，重新启用必须重新登录，旧令牌不会恢复。没有全部会话退出接口。详细请求、响应和前端处理见 [API 交接](../docs/backend-handoff.md)。

错误 JSON 固定包含 `code`、`message`、`request_id`。通常 401 表示凭据或令牌无效、403 表示账号禁用或权限不足、404 表示资源不存在、409 表示账号冲突、422 表示输入无效、429 表示登录受限、503 表示依赖不可用。时间统一输出 UTC。

## 配置

`.env.example` 列出完整默认项；真实 `.env` 不入库。`scripts/init_env.py` 为 MySQL 应用账号、root 账号和 JWT 分别生成随机值，已有 `.env` 会原样保留。

配置按性质分三处：随部署机器变化的配置（含密钥与路径）定义在 `app/core/config.py`，值写在 `.env` 或环境变量中，其中 `Settings` 为应用配置，`ParsingSettings` 为不含密钥、命令行也能加载的解析配置；解析算法的阈值和默认值在 `app/rag/parsing/options.py`，随代码修改和测试，不读环境变量；单次请求可调的解析参数见 `app/schemas/document.py`。

| 配置 | 默认值 / 说明 |
| --- | --- |
| `API_PORT` | Compose 对外端口，默认 8001 |
| `API_V1_PREFIX` | `/api/v1` |
| `MYSQL_PASSWORD` | 必填；`MYSQL_ROOT_PASSWORD` 仅供 Compose 初始化使用 |
| `JWT_SECRET_KEY` | 必填，至少 32 字符，拒绝常见占位值 |
| `JWT_ACCESS_TOKEN_MINUTES` | 30 |
| `JWT_REFRESH_TOKEN_DAYS` | 7 |
| `LOGIN_MAX_FAILURES` | 5 |
| `LOGIN_LOCK_SECONDS` | 900 |
| `OPENAPI_ENABLED` | `true`；设为 `false` 关闭文档入口 |
| `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD` | 可选首个管理员初始化；三项同时提供或全部不设置 |
| `GOTENBERG_URL` | 可选；`.doc`、`.rtf`、`.odt` 交给 Gotenberg 转为 PDF。Compose 为 API 容器设为 `http://gotenberg:3000`，本机 `uvicorn` 或命令行用 `http://127.0.0.1:3000` |
| `SOFFICE_PATH` | 可选；未配置 Gotenberg 时使用本机 LibreOffice `soffice`，为空时从 PATH 查找 |
| `CONVERSION_TIMEOUT_SECONDS` | 单个文档格式转换超时，默认 120；Compose 中 Gotenberg 自身超时设为 110 秒 |

管理员配置全部未设置时跳过初始化，部分配置会拒绝启动。不提供默认管理员凭据，初始化脚本也不生成管理员密码。已有管理员时直接跳过创建，保留当前用户名、邮箱、密码和状态；管理员修改个人资料后，原初始化配置不会阻止重启。仅在尚无管理员时使用配置创建首个管理员，不提升已有普通用户。

## 文档解析

`app/rag/parsing/` 将 PDF、Word（.docx/.doc）、RTF、ODT、TXT/Markdown 和图片（PNG/JPG/TIFF 等）解析为带来源位置的文本切片，结果暂不入库。接口和命令行是两个入口，共用 `services/document_parsing.py` 与 `schemas/document.py` 中的参数校验，输出格式相同：

```text
POST /admin/documents/parse ─┐
                             ├─► services/document_parsing ─► rag/parsing（解析 → OCR → 清洗 → 切片）
scripts/parse_document.py ───┘
```

| 参数（表单字段 / 命令行） | 默认 | 说明 |
| --- | --- | --- |
| `file` / 位置参数 | 必填 | 待解析文件 |
| `ocr` / `--ocr` | `auto` | `auto` 按 RapidOCR、Tesseract 的顺序选已安装的实现，`rapidocr`、`tesseract` 指定，`none` 不识别图片 |
| `chunker` / `--chunker` | `auto` | `auto`、`article`、`item`、`length` |
| `max_chunk_chars` / `--max-chars` | 800 | 100–4000 |
| `chunk_overlap` / `--overlap` | 100 | 须小于 `max_chunk_chars`，仅按长度切片时生效 |
| `include_blocks` / `--blocks` | false | 是否返回清洗后的文本块 |

接口需要管理员 Access Token，请求体为 `multipart/form-data`。上传内容写入临时文件，解析后立即删除；同一进程内一次只解析一个文档，并发请求依次处理，OCR 模型首次使用时加载并复用。用 Apifox 联调：

1. 在 `.env` 设置 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD` 后启动后端。Compose 环境已包含 Gotenberg 与 Tesseract，可解析 `.doc` 等格式和扫描件。需要更高的 OCR 准确率时在本机运行（`uv sync --locked --extra ocr` 后启动 `uvicorn`，见“运行”，并在 `.env` 设置 `GOTENBERG_URL=http://127.0.0.1:3000`），`auto` 会优先使用 RapidOCR。
2. 导入 `http://127.0.0.1:8001/openapi.json`（本机 `uvicorn` 为 8000 端口），文件字段会显示为文件类型。
3. 调用 `POST /auth/login` 获取管理员 `access_token`，在解析接口的 Auth 中选择 Bearer Token。
4. Body 选 form-data，`file` 选择文件，其他字段按需填写后发送。

直接请求 API 端口，不要经前端 8080 代理：Nginx 默认限制请求体 1 MB，读超时为 20 秒。

命令行不需要数据库和登录：

```sh
uv sync --locked --extra ocr
uv run python scripts/parse_document.py <文件> --output result.json
```

流程为文档解析 → 按需 OCR → 文本汇合清洗 → 切片：

- PDF 由 pdfplumber 按页读取文本层与带框表格。文本层少于 20 字且含图片的页按扫描页整页 OCR；文本页中面积不小于 10% 且无文本层覆盖的插图单独 OCR，识别结果按位置插回正文。
- `.docx` 按正文顺序读取标题、段落、表格和内嵌图片。`.doc`、`.rtf`、`.odt` 先转为 PDF 再按 PDF 解析，因此带有页码，但不保留 Word 标题级别，无边框表格按普通文本处理。配置 `GOTENBERG_URL` 时由 Gotenberg 转换（常驻服务，单份约 0.5 秒），否则使用本机 LibreOffice（`SOFFICE_PATH`，命令行也可用 `--soffice`）。转换前核对文件头：`.doc` 接受 Word 97–2003、RTF、ZIP 以及网站导出的 HTML/MHT/XML，`.rtf`、`.odt` 须为对应格式；LibreOffice 会把无法识别的内容当纯文本转换出乱码，这类文件直接返回 `DOCUMENT_INVALID`。
- 转换结果与原件的差异：分页取决于转换环境的字体（Gotenberg 使用 Noto CJK），页码可能与原件打印页码相差一页；实测 Gotenberg（LibreOffice 26.8）在避头标点换行处偶有丢失标点，样本 3581 字中丢失 1 个顿号，正文文字一致。
- OCR 有两种实现，`auto` 按顺序选择已安装的一种：RapidOCR（PaddleOCR 模型 + ONNX Runtime，可选依赖 `ocr`，开发机 CPU 约 2–3 秒/页，首次使用另需数秒加载模型）中文识别更准；Tesseract（系统安装 `tesseract-ocr` 与 `tesseract-ocr-chi-sim`，按单一文本块识别，约 0.3–0.6 秒/页）更轻量，API 镜像内置。测试样本上 RapidOCR 逐字正确，Tesseract 字符一致率约 96%，主要误差为全角括号识别成 `〈`、`)` 及个别错字。未安装或识别失败时结果状态为 `needs_ocr`，不会当作已完成。
- 清洗只对康熙部首、兼容汉字和全角字母数字做 NFKC，保留中文全角标点；去除汉字间排版空格、页码、过半页面重复的页眉页脚及重复内容。
- 切片默认自动选择：含两条以上“第X条”时按章 / 节 / 条，否则含两项以上“一、”时按一级序号，其余按标题和 800 字长度（100 字重叠）。行首“第七条规定的……”等引用不会被当作新条；超长条文按句拆分并以 `part` / `part_count` 标记。

输出包含文件 SHA-256、状态、切片（章、节、条号或序号、标题路径、PDF 页码范围、内容来源 text/ocr）与统计、警告；未要求文本块时 `blocks` 为 null。PDF 页码为 1 起算的物理页序号，Word 与纯文本没有页码。解析任务后台化、入库与向量化属于后续阶段。

```text
app/rag/parsing/
├── pipeline.py    parse_file：串联下列各阶段
├── options.py     ParseOptions：解析阈值与默认值
├── models.py      阶段间数据结构与类型
├── errors.py      全部错误码
├── text_utils.py  中文编号数字与空白处理（清洗、切片共用）
├── cleaning.py    文本汇合与清洗
├── converters/    ConverterProvider：gotenberg、libreoffice（旧格式转 PDF）
├── parsers/       ParserProvider：pdf、word、text、image
├── ocr/           OCRProvider：rapid_ocr、tesseract
└── chunkers/      ChunkerProvider：structured（条 / 一级序号）、length
```

三类 Provider 各有一个目录：`base.py` 定义基类，每个实现一个模块，目录的 `__init__.py` 登记可用实现并负责按名称创建。新增实现只需添加模块并登记一行：

- OCR：继承 `OCRProvider`，实现 `read_lines` 返回原始识别行（空行和低置信度行由基类过滤）；依赖可选时覆盖 `available`。加入 `OCR_PROVIDERS` 后即可用 `--ocr <名称>`，`auto` 按登记顺序选第一个已安装的实现。
- 文档格式：继承 `ParserProvider`，声明 `file_types` 并实现 `parse`，用 `ParsedDocument.add_text` / `add_image` 按阅读顺序输出，加入 `PARSERS`。
- 条文体例：继承 `StructuredChunker`，实现 `match_label` 识别行首标签，加入 `STRUCTURED_CHUNKERS`；章节、编号递增和超长拆分由基类处理。其他切法继承 `ChunkerProvider` 实现 `split`。

## 代码组织

- `api/`：请求模型绑定、认证依赖、响应和路由。
- `services/`：注册、登录、刷新、退出的业务规则及事务边界；`document_parsing.py` 为接口与命令行共用的解析服务。
- `repositories/`、`models/`、`db/`：数据库访问、表模型和连接生命周期。
- `security/`：线程池中的 Argon2 运算、JWT 签发和校验。
- `cache/`：失败计数、会话及令牌轮换的原子操作。
- `core/`、`middleware/`：配置、统一错误、JSON 日志和请求追踪。
- `rag/parsing/`：文档解析、OCR、清洗与切片；Parser、OCR、Chunker 各占一个目录，按基类实现并登记，结构见上文。

MySQL 保存用户；Redis 保存带有效期的会话状态，不保存原始令牌。会话创建、刷新轮换与退出分别通过 Lua 脚本原子执行。退出只撤销当前会话，不影响该账号的其他登录。

会话命名空间为 `finoracle:v2:auth:*`，会话按有效期自动过期。

## 验证

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

单元和接口测试不要求数据库、Redis或真实凭据。解析测试在运行时生成 PDF、Word 和图片样本；真实 OCR 用例仅在安装 `ocr` 可选依赖或 Tesseract 时运行，否则跳过。测试不读取本机 `backend/.env`，本地配置不影响结果。pytest 默认检查整个 `app` 的行覆盖率至少 80%，CI 同样执行此门槛。当前上游 Starlette/httpx 与 AnyIO 会发出弃用提示，不影响测试结果。

完整真实环境检查（先启动上述 Compose 环境）：

```sh
uv run python -m tests.integration
```

另含一次独立全新安装检查：

```sh
uv run python -m tests.integration --fresh-install
```

检查覆盖真实 HTTP 认证流程、事务回滚、Redis 回复丢失、刷新重放、并发轮换、退出登录与会话隔离。每项使用唯一测试数据并清理自己的记录。首次安装检查创建临时 Compose 项目和卷，只删除自己创建的临时资源。

Postman Collection 与空白凭据环境位于 [postman/](postman/)。仅在可丢弃本地环境显式开启后运行，默认管理员流程关闭；账号与令牌串联说明见 [API 交接](../docs/backend-handoff.md)。

性能探针需要本地 `.env` 与 API 使用相同数据库、Redis 和 JWT 配置，在创建本轮测试账号后自动清理：

```sh
uv run python scripts/benchmark.py --allow-disposable-users --samples 20 --warmup 3 --environment local-compose
```

可选管理员读写测量通过 `BENCHMARK_ADMIN_IDENTIFIER` 与 `BENCHMARK_ADMIN_PASSWORD` 环境变量启用，仅操作本轮生成用户。输出每接口样本数、P50/P95 和环境信息；500ms 为目标，实际性能以测量报告为准。添加 `--max-p95-ms 500` 可在超阈值时返回失败。

## 下一阶段

用户资料修改、管理员初始化与用户管理由本次后端交付覆盖；资料编辑及管理员页面由前端团队接入。文档解析已提供离线命令（见上文）；文档上传、入库、向量化、检索和 RAG 问答尚未实现。配套认证前端运行方式见[前端说明](../frontend/README.md)，后端契约见 [API 交接](../docs/backend-handoff.md)，阶段背景见[基础需求](../docs/superpowers/specs/2026-09-20-backend-foundation-prd.md)。

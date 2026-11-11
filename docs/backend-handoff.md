# 后端 API 交接（2026-10-28）

本次交付用户业务 API、测试与交接资料。当前前端已接入认证、本人资料查询与编辑，以及管理员用户管理页 `/admin/users`，包含搜索筛选、分页、详情和启用/禁用。金融文档、RAG、密码修改/找回和全部会话退出不在本次范围。

## 连接与认证

本地 Compose API 为 `http://127.0.0.1:8001/api/v1`，完整环境前端通过同源 `/api/v1` 代理。接口定义可在 API 的 `/docs`、`/openapi.json` 查看。默认 Access Token 30 分钟、Refresh Token 7 天；两者为 Bearer JWT。

| 方法与路径（均省略 `/api/v1`） | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /auth/register` | `username`, `email`, `password`, `confirm_password` | 201 UserResponse |
| `POST /auth/login` | `identifier`（用户名或邮箱）, `password` | 200 LoginResponse |
| `POST /auth/refresh` | `refresh_token` | 200 LoginResponse |
| `POST /auth/logout` | Bearer Access Token，无请求体 | 204，无响应体 |
| `GET /users/me` | Bearer Access Token | 200 UserResponse |
| `PATCH /users/me` | Bearer Access Token；下述资料字段 | 200 UserResponse |
| `GET /admin/users` | 管理员 Bearer Access Token；下述查询参数 | 200 分页对象 |
| `GET /admin/users/{user_id}` | 管理员 Bearer Access Token；UUID 用户 ID | 200 UserResponse |
| `PATCH /admin/users/{user_id}/status` | 管理员 Bearer Access Token；`{"status":"active"}` 或 `{"status":"disabled"}` | 200 UserResponse |

`UserResponse` 仅包含 `id`, `username`, `email`, `role`（`user` / `admin`）, `status`（`active` / `disabled`）, `last_login_at`（可为 null）, `created_at`, `updated_at`。ID 为 UUID 字符串，时间为 UTC ISO 8601。密码、密码摘要、内部认证版本不会返回。

`LoginResponse` 为 `{access_token, refresh_token, token_type:"bearer", expires_in, user:UserResponse}`；`expires_in` 单位为秒。登录与刷新响应禁止缓存。客户端必须串行刷新，并同时替换两枚令牌；重放旧 Refresh Token 会撤销整个对应会话。退出只撤销当前会话；退出后清除客户端令牌。刷新接口不要求 Access Token。

## 资料修改与管理员接口

`PATCH /users/me` 仅接受 `username`、`email`，至少提供一个，显式 null、空对象和额外字段（含 `role`、`status`、密码）均返回 422。用户名去首尾空白并转小写，长度 3–32，仅允许英文字母、数字、下划线；邮箱经格式校验并规范为小写。格式与注册一致，重复用户名/邮箱返回 409。修改成功后使用返回值更新本地资料；下次登录使用新标识，当前会话可继续使用。

管理员列表查询参数：`page=1`（最小 1）、`page_size=20`（1–100）、可选 `q`（用户名/邮箱模糊搜索）、`role=user|admin`、`status=active|disabled`。响应为 `{items:UserResponse[],total,page,page_size}`；空结果为 `items:[]`，排序按创建时间和 ID 稳定。查询条件变化时前端将页码重置为 1。

服务端按数据库中的当前角色校验管理员权限，前端可根据 `user.role` 控制入口。状态接口只管理普通用户，不允许禁用管理员；重复设置相同状态幂等。不存在的用户返回 404。

禁用立即阻止该账号登录、访问受保护接口和刷新。重新启用后必须重新登录；旧 Access/Refresh Token 仍然失效。实现使用数据库 `auth_version` 与 Redis 会话版本比较，Redis 命名空间保留 `finoracle:v2:auth:*`，旧会话缺少版本时按 0 处理。没有 `/auth/logout-all` 或管理员批量退出接口。

## 文档解析预览（2026-11-05 新增）

`POST /admin/documents/parse` 需要管理员 Bearer Access Token，请求体为 `multipart/form-data`：`file` 必填（PDF、`.docx` / `.doc`、RTF、ODT、TXT/Markdown、PNG/JPG/TIFF 等，最大 50 MB）；可选 `ocr`（`auto` / `rapidocr` / `tesseract` / `none`）、`chunker`（`auto` / `article` / `item` / `length`）、`max_chunk_chars`（100–4000，默认 800）、`chunk_overlap`（默认 100，须小于前者）、`include_blocks`（默认 false）。结果不入库，响应禁止缓存。

成功返回 200 `ParseResult`：`file_name`（上传时的原始文件名）、`file_type`、`sha256`、`size_bytes`、`page_count`（无分页格式为 null）、`status`、`chunker`、`ocr_provider`、`chunks`、`stats`、`warnings`，`converter`（`.doc` / `.rtf` / `.odt` 先转为 PDF 时为转换器名称，其余为 null），以及 `blocks`（未要求时为 null）。`status` 为 `completed`、`needs_ocr`（仍有图片未识别，例如服务端未安装 OCR）或 `empty`（没有可用文本）。每个切片含 `ordinal`、`chapter`、`section`、`article`（条号或“一、”序号）、`heading`、`part` / `part_count`、`content`、`page_start` / `page_end`（PDF 物理页序号）与 `sources`（`text` / `ocr`）。

同一服务进程一次只解析一个文档，扫描件每页约 0.3–0.6 秒（Tesseract）至 2–3 秒（RapidOCR），客户端应展示进行中状态并避免重复提交。错误码：`DOCUMENT_UNSUPPORTED_TYPE`（415）、`DOCUMENT_TOO_LARGE` / `DOCUMENT_TOO_MANY_PAGES`（413）、`DOCUMENT_INVALID`（422，空文件、损坏、加密、内容与扩展名不符或无法转换）、`DOCUMENT_CONVERSION_UNAVAILABLE` / `OCR_UNAVAILABLE`（503，Gotenberg 不可用或超时，或服务端缺少显式指定的 OCR）、`REQUEST_VALIDATION_ERROR`（422）。前端同源代理已对 `/api/v1/admin/` 路径放宽到请求体 55 MB、读超时 180 秒；大文件或扫描件的解析预览可能超过 180 秒，仍以 API 直连为准。

## 文档入库（2026-11-05 新增）

对应设计页 12–16（制度文档管理、导入、导入任务、解析校验、版本管理）。以下接口都需要管理员 Bearer Access Token，路径省略 `/api/v1`，响应禁止缓存，时间为 UTC。完整设计见[入库方案](design/2026-11-05-document-ingestion/README.md)。

**流程：**

1. 上传后接口立即返回解析任务（202），由后台 worker 解析。
2. 默认配置下，完整解析的版本自动批准，worker 接着创建索引任务做向量化，完成后版本为 `ready`（待发布）。含待 OCR 图片（`parse_status` 为 `needs_ocr`）的版本停在 `parsed`，需要人工批准或重新解析。
3. 管理员发布 `ready` 版本后，用户才能检索到它。

页面轮询 `GET /admin/versions/{version_id}`（建议 2–3 秒一次），直到 `status` 不再是 `uploaded` / `parsing` / `indexing`；`latest_job` 给出当前任务的阶段、进度与错误。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /admin/imports` | `multipart/form-data`：`file` 必填（格式同解析预览，最大 50 MB）。新建制度时 `title` 必填，`issuer` 可选；为已有制度上传新版本时传 `document_id`，不能再传 `title`、`issuer`。可选版本信息：`document_number`、`version_note`、`published_date`、`effective_date`（YYYY-MM-DD）、`validity_status`（`unknown` 默认 / `effective` / `not_yet_effective` / `amended` / `repealed`）、`validity_checked_at`、`source_url`、`download_url`（http/https）、`expected_articles`。可选解析参数：`ocr`、`chunker`、`max_chunk_chars`、`chunk_overlap`。空字符串按未填写处理 | 202 Job |
| `GET /admin/imports` | 查询参数 `page`、`page_size`（≤100）、`status`、`kind`、`document_id`、`version_id` | 200 分页 Job |
| `GET /admin/imports/{job_id}` | — | 200 Job |
| `POST /admin/imports/{job_id}/retry` | 仅 `status` 为 `failed` 的任务 | 202 Job |
| `GET /admin/documents` | `page`、`page_size`、`q`（标题、发文机构、外部 ID）、`published`（true/false） | 200 分页 Document |
| `GET` / `PATCH /admin/documents/{document_id}` | PATCH：`title`（不能为 null）、`issuer`（null 表示清除），至少一项 | 200 Document |
| `GET /admin/documents/{document_id}/versions` | — | 200 Version 数组，从新到旧 |
| `GET` / `PATCH /admin/versions/{version_id}` | PATCH：上述版本信息字段，至少一项；`validity_status` 不能为 null，其余传 null 表示清除。只改信息，不影响切片 | 200 Version |
| `GET /admin/versions/{version_id}/chunks` | `page`、`page_size`（≤100） | 200 分页 Chunk |
| `GET /admin/versions/{version_id}/file` | `variant=original`（默认，原件）或 `pdf`（可预览的 PDF） | 200 文件流 |
| `POST /admin/versions/{version_id}/reparse` | 可选 JSON 解析参数，不传沿用原参数；仅 `parsed`、`ready` 或 `failed` 状态 | 202 Job |
| `POST /admin/versions/{version_id}/approve` | 批准向量化；仅 `parsed`（未被自动批准的版本）或 `archived`（下架后重新上线） | 202 Job（`kind` 为 `index`） |
| `POST /admin/versions/{version_id}/publish` | 仅 `ready`；同一制度原来的发布版本自动变为 `archived` | 200 Version |
| `POST /admin/versions/{version_id}/withdraw` | 仅 `published`；下架后为 `archived`，可再批准、发布 | 200 Version |
| `DELETE /admin/versions/{version_id}` | 仅 `uploaded`、`parsed`、`ready`、`failed` 状态且没有进行中的任务；已发布、已归档的版本保留供历史引用 | 204 |

- **Job**：`id`、`version_id`、`document_id`、`document_title`、`file_name`、`kind`（目前为 `parse`）、`status`（`queued` / `running` / `succeeded` / `failed` / `cancelled`）、`stage`（`downloading` / `converting` / `parsing` / `ocr` / `chunking` / `saving`，未运行时为 null）、`progress_current` / `progress_total`（OCR 阶段为图片张数）、`attempts` / `max_attempts`、`run_after`、`error_code` / `error_message`、`created_at`、`started_at`、`finished_at`、`updated_at`。临时错误会自动重试：`status` 回到 `queued`，`error_code` 保留上次原因，`run_after` 为下次执行时间。
- **Document**：`id`、`external_id`、`title`、`issuer`、`version_count`、`published_version` 与 `latest_version`（版本摘要：`id`、`status`、`version_note`、`validity_status`、`published_at`、`created_at`，可为 null）、时间字段。
- **Version**：
  - 版本信息字段；
  - 文件信息：`file_name`、`file_type`、`size_bytes`、`sha256`；
  - `status`：`uploaded`（排队）/ `parsing` / `parsed`（待批准）/ `indexing`（向量化中）/ `ready`（待发布）/ `published`（用户可检索）/ `archived`（已下架或被替换）/ `failed`；
  - `index_model`、`indexed_at`：向量化所用模型与时间；`published_at`、`published_by`：发布信息；
  - `has_pdf`：为 true 时可用 `variant=pdf` 预览；
  - 解析结果：`parse_options`、`parser_version`、`parse_status`（`completed` / `needs_ocr`）、`page_count`、`chunk_count`、`parse_stats`（含实际采用的 `chunker`、`ocr_provider`）、`parse_warnings`；
  - `latest_job`：最近一次任务，同 Job，可为 null。
- **Chunk**：`id`、`ordinal`、`chapter`、`section`、`article`、`heading`、`part` / `part_count`、`content`、`page_start` / `page_end`（`variant=pdf` 那份 PDF 的物理页序号）、`sources`（`text` / `ocr`）、`external_id`。
- **原件下载**：PDF 与 PNG/JPEG 为 `inline`，可直接嵌入预览；其他格式为附件。文件名在 `Content-Disposition` 的 `filename*` 中，按 UTF-8 编码。
- **错误码**：
  - `DOCUMENT_NOT_FOUND` / `VERSION_NOT_FOUND` / `IMPORT_JOB_NOT_FOUND`（404）；
  - `FILE_NOT_AVAILABLE`（404，该版本没有 PDF）；
  - `DOCUMENT_VERSION_DUPLICATE`（409，该制度已有相同文件）；
  - `IMPORT_IN_PROGRESS`（409，处理中，等待任务结束）；
  - `VERSION_STATE_CONFLICT`（409，当前状态不允许该操作）；
  - `IMPORT_JOB_NOT_RETRYABLE`（409）；
  - `STORAGE_UNAVAILABLE`（503）；
  - 上传校验错误同解析预览。
- **任务失败时的 `error_code`**：
  - `DOCUMENT_INVALID`、`DOCUMENT_EMPTY`：建议提示"替换文件"；
  - `DOCUMENT_FILE_MISSING`、`PARSE_OPTIONS_INVALID`：同上，替换文件或调整参数；
  - `JOB_ATTEMPTS_EXHAUSTED`、`STORAGE_UNAVAILABLE`、`DOCUMENT_CONVERSION_UNAVAILABLE`、`EMBEDDING_UNAVAILABLE`、`VECTOR_STORE_UNAVAILABLE`：可重试；
  - `EMBEDDING_AUTH_FAILED`：向量化密钥无效，需运维更正配置后重试；`EMBEDDING_REJECTED`：内容被向量化服务拒绝。

## 条文检索（2026-11-05 新增）

对应设计页 07 全文检索，也是问答的检索基础。需要登录（普通用户即可），只检索已发布版本，响应禁止缓存。

`POST /search` 请求体：
- `query`：必填，1–1000 字，去首尾空白；
- `document_ids`：可选，最多 50 个制度 ID，只在这些制度中检索，对应问答首页的"选择制度范围"；
- `top_k`：默认 8，范围 1–20。

返回 `{query, results}`，`results` 按相关度从高到低。每项包含：
- **相关度**：`chunk_id`、`score`（融合分数，只用于排序）；`dense_rank` / `dense_score`、`keyword_rank` / `keyword_score`（两路各自的名次与分数，未进入该路时为 null）。
- `document`：`id`、`title`、`issuer`。
- `version`：`id`、`document_number`、`version_note`、`published_date`、`effective_date`、`validity_status`、`source_url`。`validity_status` 为 `unknown` 时不能显示为现行有效。
- **条文位置与内容**：`ordinal`、`chapter`、`section`、`article`、`heading`、`content`、`page_start` / `page_end`。

没有匹配或没有已发布制度时返回空数组。检索服务暂不可用时返回 503 `SEARCH_UNAVAILABLE`；参数不合法时返回 422。

## 错误与页面处理

所有错误响应均为 `{code,message,request_id}`，例如 `{"code":"REQUEST_VALIDATION_ERROR","message":"请求参数不合法","request_id":"..."}`。页面可显示 `message`，排障记录 `request_id`，不得记录密码、完整令牌或完整认证请求/响应。

| HTTP 状态 | 含义 / 前端处理 |
| --- | --- |
| 401 | 账号/密码或令牌无效；受保护请求可尝试一次串行刷新，刷新失败回登录页 |
| 403 | 禁用或权限不足；按错误 code 区分，管理员入口可提示无权限 |
| 404 | 用户或路由不存在 |
| 409 | 用户名或邮箱冲突，保留表单并提示修改 |
| 422 | 请求字段、格式或查询参数无效 |
| 429 | 登录失败限制，避免自动重复提交 |
| 503 | 数据库或 Redis 暂时不可用，提示稍后重试 |
| 500 | 统一服务端错误，以 request_id 排查 |

## 管理员初始化

通过私有环境变量同时设置 `ADMIN_USERNAME`、`ADMIN_EMAIL`、`ADMIN_PASSWORD`。三项均未设置则跳过；只设置部分会拒绝启动。不提供默认管理员或默认密码，`scripts/init_env.py` 不生成管理员凭据。管理员密码适用注册密码规则，用户名和邮箱遵循相同规范化规则。

迁移后启动初始化支持重复和并发启动。已有管理员时直接跳过创建，保留当前用户名、邮箱、密码和状态；管理员修改个人资料后，原初始化配置不会阻止重启。环境变量仅用于首次创建，不能用于覆盖已有账号；没有管理员时也不会把已存在的普通用户提升为管理员。

## 验证与联调工具

在 `backend/` 中执行 `uv sync --locked`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest -q`。pytest 默认统计整个 `app` 的行覆盖率，CI 同样要求至少 80%。真实 MySQL/Redis 和全新安装检查见 [后端说明](../backend/README.md)。

Postman 导入 [Collection](../backend/postman/FinOracle.postman_collection.json) 与 [本地环境](../backend/postman/local.postman_environment.json)。仅在可丢弃本地环境将 `allow_disposable_users` 改为字符串 `true`，从注册开始按顺序运行。每次生成独立普通账号；密码、ID、令牌使用本次运行的局部变量。可选管理员文件夹需另设 `run_admin=true` 与管理员私有当前值 `admin_identifier`、`admin_password`；所有状态操作只针对本轮生成并核验的用户 ID。不要导出填入秘密的环境、运行日志或认证响应。Postman 不提供数据库清理能力，生成的 `postman_*` 账号保留在本地测试库；管理员流程中途失败时仅检查本轮账号状态。无需管理员时可只运行第一个文件夹。

性能探针同样仅允许回环地址，并要求显式创建可丢弃账号。在 `backend/` 的本地 `.env` 指向同一 API 所用数据库、Redis 和 JWT 密钥后执行：

```sh
uv run python scripts/benchmark.py --allow-disposable-users --samples 20 --warmup 3 --environment local-compose
```

需要覆盖管理员查询、详情、禁用/启用时，安全注入 `BENCHMARK_ADMIN_IDENTIFIER` 与 `BENCHMARK_ADMIN_PASSWORD` 环境变量；两项必须同时设置。探针只借用管理员登录，所有状态和资料变更均针对本轮生成的普通用户，不修改现有账号凭据或状态。结束或出错时按生成的精确名称/ID 清理本轮数据库记录和 Redis 会话；清理失败显式报错。不要指向生产数据或与 API 不一致的本地配置。

输出 JSON 记录时间、系统/Python、环境名称、目标地址、并发数 1、预热数、每个接口样本数及最近秩 P50/P95；预热、管理员登录/退出和清理不计时。默认认证及个人资料接口必测，管理员接口仅在提供管理员凭据时测量。500ms 是目标值，实测值由当次输出给出；没有报告意味着尚未取得性能结论。可添加 `--max-p95-ms 500` 将任一接口超阈值设为退出码 1；运行/清理失败退出码 2。脚本不保存或打印密码、完整令牌或认证响应体。

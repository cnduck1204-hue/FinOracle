# 文档入库方案（2026-11-05）

状态：已确认（2026-11-05），按第 11 节的顺序实施；选型结论见第 12 节。

范围：画板 B 区（知识库构建）的入库部分，以及它与已实现的 A 区（解析 / OCR / 切片）的衔接。C 区（在线问答）只写它对入库的要求。

## 1 目标

- 管理员上传制度文件后，系统依次完成：存储原件、后台解析切片、人工校验、向量化、发布。发布后的版本才能被用户检索。
- 支撑前端设计的页面 12–16：制度文档管理、文档导入、导入任务、解析校验、发布与版本管理。
- 导入 feat/data-collection 交付的 release 数据，共 2 份文档、123 条。
- 本方案不包含问答接口、检索实现、切片在线编辑（第 7 节预留）和用户端制度库接口。

## 2 现状与约束

- 解析流水线 `app/rag/parsing` 是同步、CPU 密集的函数。扫描件每页 0.3–3 秒，500 页的文档需要 20 分钟以上。
- 现有 `POST /admin/documents/parse` 在 API 进程内加全局锁同步执行，只适合预览。
- 赛题要求首次响应 ≤2s，所以问答链路不能有排队。
- 数据契约要求：
  - 保留原始 doc_id、条号和 PDF 页码。
  - 时效未知的文档不能显示为现行有效。
  - 引用至少包含制度标题、版本说明、条号、来源和页码。
  - 不能仅凭"解析成功"就批准入库。
- 前端设计要求：
  - 上传、解析完成、索引准备、校验通过、发布是不同的状态。
  - 草稿不进入检索。
  - 修订时新建版本，不改写已发布版本。
  - 失败时显示失败阶段和页码；重试时复用原任务。

## 3 同步还是异步

| 链路 | 方式 | 原因 |
| --- | --- | --- |
| 入库：解析、OCR、向量化 | 后台任务。API 登记后返回 202，由 worker 进程执行 | 耗时以分钟计，CPU 密集，需要进度、重试和失败定位，不能占用 API 进程 |
| 问答：检索、重排、生成 | 请求内完成，全链路 async I/O，用 SSE 流式返回 | 首次响应 ≤2s，排队和轮询会耗尽这个时间预算 |

问答链路的要求（C 阶段实施）：

- Embedding API、向量检索、关键词检索、Rerank 和 LLM 都使用异步客户端。向量检索和关键词检索用 `asyncio.gather` 并发执行。
- 如果在 API 进程内运行本地模型（BGE-M3、BGE Reranker），推理是 CPU 计算，必须放到线程池或独立的模型服务中，否则会阻塞事件循环。
- 生成结果用 SSE 流式返回。写入会话、统计等副作用放到流结束之后执行。

## 4 总体结构

```
浏览器 ──multipart──▶ API
                      │ 1 校验类型和大小，计算 SHA-256
                      │ 2 原件写入 MinIO
                      │ 3 同一事务写入 document_versions 和 import_jobs(queued)
                      ▼
                    MySQL ◀── 认领任务（FOR UPDATE SKIP LOCKED）── worker
                                                                   │ 从 MinIO 读取原件
                                                                   │ 转换 → 解析 → OCR → 清洗 → 切片
                                                                   │ 转换后的 PDF 写回 MinIO
                                                                   │ 切片写入 MySQL
                                                                   │ 向量化 → 向量库
```

Compose 新增两个服务：

- `minio`：文件存储（第 1 步已加入）。
- `worker`（第 3 步已加入）：与 API 使用同一镜像，启动命令为 `python -m app.worker`，依赖 `api` 健康（迁移由 API 启动时执行）。

## 5 文件存储（MinIO）

**访问方式**

- 只通过 S3 API 访问。代码依赖 `app/storage` 的 `ObjectStorage` 接口（ensure_bucket / put_file / download_file / stat / delete），实现类 `S3ObjectStorage` 使用 boto3。供浏览器读取原文的流式读取接口在第 4 步补充。
- boto3 1.36 起默认开启 flexible checksum。需要把 `request_checksum_calculation` 和 `response_checksum_validation` 设为 `when_required`，以兼容不同的 S3 实现。
- 错误约定：对象不存在时抛出 `FileNotFoundError`；连接失败、凭据错误等情况返回 503 `STORAGE_UNAVAILABLE`，worker 据此重试。

**桶与对象键**

- 只用一个私有桶 `finoracle-documents`。第一次写入时发现桶不存在就自动创建；worker 启动时也会检查一次。
- 不在 API 启动时建桶，原因有两个：
  - 对象存储暂不可用时，不影响认证等其他接口。
  - 单元测试会运行 API 的启动流程，启动时建桶会让测试连接本机存储。
- 对象键按版本组织，不含原始文件名（原始文件名记在数据库）：
  - `documents/{document_id}/{version_id}/original.{ext}`：原件。
  - `documents/{document_id}/{version_id}/converted.pdf`：.doc / .rtf / .odt 的转换产物。这类文件的切片页码以这份 PDF 为准，所以原文预览也必须展示它。
  - `documents/{document_id}/{version_id}/blocks.json`：清洗后的文本块，供校验页对照原文。

**浏览器访问**

- 浏览器不直连 MinIO，由 API 鉴权后流式返回文件（单个不超过 50 MB）。
- 因此不需要对前端暴露 MinIO 端口，也不需要预签名 URL 和 CORS 配置。

**一致性**

- 先写对象，再提交数据库；数据库提交失败时删除刚写入的对象。
- 删除草稿时，由 cleanup 任务删除对应对象。
- 进程崩溃可能留下孤儿对象，后续用清理脚本删除没有对应版本的对象。

**镜像现状**

- MinIO 社区版仓库已于 2026-04 归档，Docker Hub 上的 `minio/minio` 从 2026-09 起无法拉取。
- 2026-11-05 实测：`quay.io/minio/minio` 也已需要登录才能访问，国内 Docker Hub 加速源同样拉不到。
- 因此 Compose 改用 MinIO 的维护分支 Silo，镜像为 `pgsty/silo:RELEASE.2026-10-26T00-00-00Z`。它的接口、`MINIO_ROOT_*` 环境变量和健康检查路径都与 MinIO 相同。
- 代码只依赖 S3 API，生产环境可以换成其他 S3 兼容实现，不需要改代码。

## 6 数据模型（MySQL）

制度（documents）和版本（document_versions）分为两张表：一个制度可以有多个版本，但最多只有一个已发布版本。文号、生效日期、时效状态随修订变化，所以放在版本表上。

第 2 步已按本节建表（迁移 `1793832fdc5b`，模型在 `app/models/document.py`、`app/models/import_job.py`）。所有 `created_by` 均可为空，供导入脚本、自动清理等系统操作使用。

### documents

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| external_id | VARCHAR(128) NULL UNIQUE | 外部数据集的 doc_id |
| title | VARCHAR(255) | 正式名称 |
| issuer | VARCHAR(255) NULL | 发文机构 |
| created_by | UUID FK users NULL | |
| created_at / updated_at | DATETIME(6) | UTC |

当前发布版本不在本表保存指针，而是由 document_versions 中 status 为 published 的那一行确定，避免指针与版本状态两处记录不一致。

### document_versions

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| document_id | UUID FK | |
| status | ENUM | 取值见第 7 节 |
| document_number | VARCHAR(255) NULL | 文号 |
| version_note | VARCHAR(255) NULL | 版本说明，如"2023年第二次修订" |
| published_date / effective_date | DATE NULL | |
| validity_status | ENUM | unknown（默认）/ effective / not_yet_effective / amended / repealed |
| validity_checked_at | DATE NULL | 时效核查日期 |
| source_url / download_url | VARCHAR(2048) NULL | |
| file_name / file_type | VARCHAR | 原始文件名和扩展名 |
| size_bytes / sha256 | BIGINT / CHAR(64) | |
| object_key / pdf_object_key | VARCHAR(512) | 原件和转换后 PDF 的对象键 |
| parse_options | JSON | ocr / chunker / max_chunk_chars / chunk_overlap |
| parser_version | VARCHAR(32) NULL | 产生当前切片的解析器版本 |
| parse_status | ENUM NULL | completed / needs_ocr / empty |
| page_count / chunk_count | INT | |
| expected_articles | INT NULL | 该版本应有的条数（来自登记信息）；与实际切出的条数不符时，在校验页提示 |
| parse_stats / parse_warnings | JSON NULL | 即 ParseResult 的 stats 和 warnings |
| index_model / indexed_at | VARCHAR(128) / DATETIME(6) NULL | 向量化使用的模型、维度和时间 |
| created_by / published_by | UUID FK users NULL | |
| created_at / updated_at / published_at | DATETIME(6) | |
| published_document_id | 生成列 | status 为 published 时等于 document_id，否则为 NULL；由数据库计算，应用不读写 |

约束：

- `UNIQUE(document_id, sha256)`：同一制度不能重复上传同一个文件。
- `UNIQUE(published_document_id)`：每个制度最多一个已发布版本，由数据库保证。发布时必须在同一事务中先归档原版本，再发布新版本。
- `UNIQUE(id, document_id)`：供切片的复合外键引用。
- `INDEX(status)`。

### document_chunks

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | BIGINT AUTO_INCREMENT PK | 同时作为向量库主键 |
| version_id | UUID | 与 document_id 组成复合外键，引用版本的 (id, document_id)，ON DELETE CASCADE |
| document_id | UUID | 冗余存储，便于过滤；复合外键保证与所属版本一致 |
| external_id | VARCHAR(128) NULL | 外部数据集的 chunk_id |
| ordinal | INT | `UNIQUE(version_id, ordinal)` |
| chapter / section / article | VARCHAR(255) | |
| heading | VARCHAR(512) | |
| part / part_count | SMALLINT | 同一条过长时被拆分的序号和总数 |
| content | MEDIUMTEXT | 外部导入的整条条文可能超过 TEXT 的 64 KB |
| content_sha256 | CHAR(64) | 相同内容复用向量时使用 |
| page_start / page_end | INT NULL | 物理页序号（有转换时指转换后的 PDF） |
| sources | JSON | text / ocr |

- 字段与 `ParseResult.Chunk` 一一对应，是 release_chunks.json 字段的超集。
- 表名为 document_chunks，与模型 `DocumentChunk` 对应，避免与解析模块的 `Chunk` 混淆。
- 写入时用批量 INSERT（不取回自增主键）。MySQL 没有 RETURNING，逐个写入 ORM 对象会每行一次往返。
- 主键用 BIGINT 自增的原因：
  - 切片数量远多于其他表，自增主键按插入顺序写入，效率更高。
  - 可以直接用作向量库的 INT64 主键。
  - 对外接口用 version_id + ordinal 定位切片。
- 关键词检索索引在 C 阶段随检索方案一起确定（MySQL ngram FULLTEXT 或 Milvus BM25），不影响本表结构。

### import_jobs

| 列 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID PK | |
| version_id | UUID FK ON DELETE CASCADE | |
| kind | ENUM | parse / index / cleanup |
| status | ENUM | queued / running / succeeded / failed / cancelled |
| stage | VARCHAR(32) NULL | downloading / converting / parsing / ocr / chunking / saving / embedding / indexing |
| progress_current / progress_total | INT / INT NULL | |
| attempts / max_attempts | INT | max_attempts 默认 3 |
| run_after | DATETIME(6) | 退避后最早可执行的时间 |
| locked_by / lease_expires_at | VARCHAR(64) / DATETIME(6) NULL | worker 标识和租约到期时间 |
| error_code / error_message | VARCHAR(64) / VARCHAR(1000) NULL | |
| created_by | UUID FK users | |
| created_at / started_at / finished_at / updated_at | DATETIME(6) | |

索引：`INDEX(status, run_after)`、`INDEX(version_id)`。

## 7 版本状态与任务

```
uploaded → parsing → parsed ─批准→ indexing → ready ─发布→ published ─下架/被替换→ archived
              ↘ failed（可重试）        ↘ failed（可重试）                    archived ─批准→ indexing
```

默认配置下（`AUTO_APPROVE_PARSED=true`），完整解析的版本由 worker 自动批准：解析任务成功时，在同一事务中把版本置为 indexing 并创建索引任务。

| 状态 | 含义 | 管理员可执行的操作 |
| --- | --- | --- |
| uploaded | 原件已存储，解析任务排队中 | — |
| parsing | 解析中 | — |
| parsed | 解析完成、未自动批准：含待 OCR 图片（needs_ocr），或关闭了自动批准 | 批准、换参数重新解析、删除 |
| indexing | 向量化写入中 | — |
| ready | 已索引，待发布 | 发布、重新解析、删除 |
| published | 用户可检索；每个制度最多一个 | 下架 |
| archived | 已被新版本替换或已下架；切片与原件保留 | 批准（重新上线） |
| failed | 最近一次任务失败，失败阶段和错误码记在任务上 | 重试、重新解析、删除 |

**可见性与发布**

- 只有 published 版本进入用户检索。uploaded 到 ready 之间的版本只有管理员可见。
- 发布在单个事务内完成：锁定制度行串行化并发发布，用锁定读取找到原发布版本，先将其置为 archived 并刷新，再把新版本置为 published。数据库唯一约束保证不会同时存在两个已发布版本。
- 发布不需要重写向量，检索时按已发布版本的 ID 过滤即可。
- 已发布版本不能修改切片或删除，只能下架。修订通过上传新版本完成。

**解析结果的处理**

- parse_status 为 empty 时按失败处理，错误码 `DOCUMENT_EMPTY`。
- parse_status 为 needs_ocr 时版本进入 parsed，不自动批准。管理员可以换 OCR 参数重新解析，也可以确认后手动批准。
- 预留的切片编辑功能（暂不实现）：只允许编辑 parsed 状态的切片。已索引的版本被修改后要退回 parsed，重新批准。

**可安全重试**

- 重新解析：在同一事务内删除该版本的旧切片，再整体写入新切片。ready 版本重新解析后旧向量暂留，下次索引时先删后写；未发布的版本不进入检索，不受影响。
- 索引任务：先按 version_id 删除向量，再分批写入。

**下架、替换与删除**

- 下架或被新版本替换时，版本置为 archived。提交后由 API 尽力删除该版本的向量（失败只留下孤儿向量，检索按已发布版本过滤，不受影响）。MySQL 中的切片和对象存储中的原件保留，历史答案的引用仍可回溯；需要重新上线时批准即可重新向量化。
- 删除未发布版本（uploaded、parsed、failed、ready，且没有进行中的任务）：先提交数据库删除，再尽力删除对象与向量。
- 因此没有使用单独的 cleanup 任务；`import_jobs.kind` 中的 cleanup 保留给以后的批量清理。

### 任务执行

**入队**

- 队列就是 import_jobs 表。API 在登记版本的同一事务中插入任务，不会出现"有版本无任务"或"有任务无版本"。

**worker 主循环**（第 3 步已实现，代码在 `app/worker/`）

1. 认领任务：先接手租约已过期的执行中任务，再取已到执行时间的排队任务，分两条查询：
   ```sql
   SELECT … WHERE status='running' AND lease_expires_at<now
   ORDER BY lease_expires_at, id LIMIT 1 FOR UPDATE SKIP LOCKED
   SELECT … WHERE status='queued' AND run_after<=now
   ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED
   ```
   将任务置为 running，attempts 加 1，租约设为当前时间加 60 秒，然后提交。实测确认的两个前提：
   - **排序必须能按索引顺序读取**：分别对应 `(status, run_after)` 和 `(status, lease_expires_at)` 两个索引（二级索引末尾隐含主键 id）。如果合成一条 OR 查询并按 created_at 排序，MySQL 会先排序，FOR UPDATE 锁住所有候选行，另一个 worker 用 SKIP LOCKED 时一行也取不到。
   - **worker 的数据库连接使用 READ COMMITTED**：默认的 REPEATABLE READ 下，锁定读会加间隙锁，阻塞其他 worker 更新各自任务的索引项，并发认领因此串行，甚至锁等待超时。只有 worker 的引擎改隔离级别，API 不变。
2. 执行期间每 5 秒续租一次，同时写入阶段和进度（stage、progress_current、progress_total）。续租发现任务已被其他 worker 接手时停止续租。
3. 处理器写入结果时，在同一事务中锁定任务并确认仍归自己所有，再写结果并标记成功。租约过期被接手后，迟到的结果不会覆盖新的执行。
4. 失败时按错误类型处理：
   - 业务错误（AppException 4xx，例如文件损坏、加密、不支持的类型、没有文本）：直接置为 failed，不重试，版本置为 failed。
   - 其他错误（AppException 5xx，例如存储或 Gotenberg 不可用；数据库或文件系统错误；未预期的异常）：第 1、2 次失败后分别等待 30 秒、2 分钟重试，版本保持 parsing；超过 max_attempts 后置为 failed。
5. 进程崩溃后，任务租约过期，会被其他 worker 重新认领；崩溃同样消耗尝试次数，用尽后直接置为 failed（`JOB_ATTEMPTS_EXHAUSTED`）。
6. 收到 SIGTERM 时不再认领新任务，当前任务完成后退出（Compose 中 `stop_grace_period` 为 30 秒）。

**解析任务**

- 依次为：下载原件；.doc、.rtf、.odt 先转换为 PDF，并保存为 `converted.pdf`；解析、OCR、切片；把清洗后的文本块保存为 `blocks.json`。最后在一个事务中替换切片、更新版本并标记任务成功。
- 版本上记录的信息：
  - `parse_stats`：ParseResult 的统计，另含 auto 时实际采用的 `chunker` 和 `ocr_provider`；
  - `parse_warnings`；
  - `parser_version`：取 `PARSER_VERSION`，解析输出规则变化时递增；
  - `page_count`；
  - `pdf_object_key`。
- 解析结果为 empty 时，任务以 `DOCUMENT_EMPTY` 失败。needs_ocr 仍进入 parsed，由批准环节拦截。
- 原件不存在时报 `DOCUMENT_FILE_MISSING`，保存的解析参数不合法时报 `PARSE_OPTIONS_INVALID`，两者都不重试。

**并发与进度**

- 每个 worker 进程同时只执行一个任务，因为 OCR 会占满 CPU。需要扩容时增加 worker 副本。
- 没有任务时每 2 秒轮询一次（`WORKER_POLL_SECONDS`），租约时长由 `JOB_LEASE_SECONDS` 配置（默认 60）。
- `parse_file` 有可选的 progress 回调，报告 converting、parsing、ocr（逐张图片）和 chunking 四个阶段，不改变它的返回值和 CLI 行为。worker 另外报告 downloading 和 saving 阶段。
- worker 只加载 `WorkerSettings`（数据库配置加调度参数）、`StorageSettings` 和 `ParsingSettings`，容器中没有 JWT 密钥和管理员凭据。

**为什么用 MySQL 表做队列，不用 Celery / arq**

- 任务量很小（只有管理员导入）。
- 前端本来就要读这张表的任务状态，用它做队列就不需要在 Redis 和数据库之间同步状态。
- 入队和登记版本在同一个事务里完成。
- MySQL 8 支持 SKIP LOCKED。

## 8 管理端 API

以下接口都需要管理员权限，路径省略 `/api/v1`。第 4 步已实现除批准、发布、下架以外的接口；这三个接口依赖向量化，随第 5 步实现。

| 方法与路径 | 作用 | 前端页 |
| --- | --- | --- |
| `POST /admin/imports` | multipart：file、可选 document_id（不传则新建制度，此时 title 必填；传了则不能再改制度信息）、版本元数据、解析参数。返回 202 和任务（含 document_id、version_id） | 13 |
| `GET /admin/imports` | 任务列表，按 status、kind、document_id、version_id 筛选，分页，从新到旧 | 14 |
| `GET /admin/imports/{job_id}` | 任务详情：阶段、进度、尝试次数、错误码和错误信息 | 15 |
| `POST /admin/imports/{job_id}/retry` | 把失败任务重新排队（复用原任务，尝试次数清零）；版本须仍为 failed | 14、15 |
| `GET /admin/documents` | 制度列表，按 q（标题、发文机构、外部 ID，按字面匹配）、是否已发布筛选；每项带版本数、发布版本和最新版本摘要 | 12 |
| `GET` / `PATCH /admin/documents/{id}` | 查看和修改制度标题、发文机构 | 12 |
| `GET /admin/documents/{id}/versions` | 版本列表，从新到旧，每项带最近一次任务 | 16 |
| `GET` / `PATCH /admin/versions/{id}` | 版本详情（含解析统计、警告、最近任务）；修改登记信息，不影响切片，发布后仍可更正与标记时效 | 15、16 |
| `GET /admin/versions/{id}/chunks` | 切片分页，按顺序 | 15 |
| `GET /admin/versions/{id}/file?variant=original\|pdf` | 鉴权后流式返回原件或可预览的 PDF；PDF 与常见图片 inline，其他格式以附件下载 | 15 |
| `POST /admin/versions/{id}/reparse` | 可选提交新解析参数，不提交则沿用原参数；仅 parsed 或 failed 状态 | 15 |
| `POST /admin/versions/{id}/approve` | 批准，并创建索引任务（第 5 步） | 15 |
| `POST /admin/versions/{id}/publish` | 发布，替换当前的发布版本（第 5 步） | 16 |
| `POST /admin/versions/{id}/withdraw` | 下架（第 5 步） | 16 |
| `DELETE /admin/versions/{id}` | 删除 uploaded、parsed、failed 状态的版本；先提交数据库删除，再删对象，失败只留下孤儿对象。第 5 步加入向量清理 | 16 |

- 同一版本已有 queued 或 running 任务时，重新解析、删除都返回 409 `IMPORT_IN_PROGRESS`。版本行加锁后再检查，并发提交不会重复建任务。
- 导入流程：
  1. 校验扩展名与大小。
  2. 把上传内容复制到临时文件，同时计算 SHA-256。
  3. 检查同一制度下是否已有相同文件。
  4. 写入对象存储。
  5. 在一个事务中登记制度、版本与任务。
  - 第 5 步失败时删除刚写入的对象；并发上传同一文件由唯一约束兜底，同样返回 409。
- 原件的 `Content-Type` 按扩展名设定，不采用客户端提交的值，文本文件不附带 charset（可能是 GB18030）。下载响应带 `X-Content-Type-Options: nosniff`。
- 现有的 `POST /admin/documents/parse` 保留，作为不入库的即时预览。
- 新增错误码：
  - 404：`DOCUMENT_NOT_FOUND`、`VERSION_NOT_FOUND`、`IMPORT_JOB_NOT_FOUND`、`FILE_NOT_AVAILABLE`（请求 PDF 但版本没有 PDF）
  - 409：`DOCUMENT_VERSION_DUPLICATE`、`VERSION_STATE_CONFLICT`、`IMPORT_IN_PROGRESS`、`IMPORT_JOB_NOT_RETRYABLE`
  - 422：`DOCUMENT_EMPTY`、`DOCUMENT_FILE_MISSING`、`PARSE_OPTIONS_INVALID`（任务错误码）
  - 503：`STORAGE_UNAVAILABLE`
- 前端 Nginx 已对 `admin/` 路径放宽到请求体 55 MB、读超时 180 秒（第 1 步）。

## 9 向量化接口（第 5 步已实现，代码在 `app/rag/indexing/`）

**EmbeddingProvider**（`DashScopeEmbedding`）

- 属性：`name`、`model`、`dimension`、`max_batch_size`；`model_id` 为 `模型:维度`，记录在版本的 index_model 上。
- 方法：`embed_documents(texts)`。查询向量化在 C 阶段补充。
- 默认实现走 DashScope 的 OpenAI 兼容接口 `/embeddings`，模型 `text-embedding-v4`，维度 1024，北京地域。单次最多 10 条，超过返回 400。请求带 `dimensions`，返回按 index 排序，并校验数量与维度。
- 错误处理：
  - 401/403：`EMBEDDING_AUTH_FAILED`，不重试。更正密钥并重启 worker 后用重试接口重新执行。
  - 429、5xx、超时、无法识别的结果：`EMBEDDING_UNAVAILABLE`，按次数重试。
  - 其他 4xx：`EMBEDDING_REJECTED`，不重试。
  - 日志只记录状态码，不记录响应正文（可能回显输入文本）。

**VectorStore**（`MilvusVectorStore`）

- 方法：`ensure_collection()`、`upsert(records)`、`delete_by_version(version_id)`，以及供校验用的 `count_by_version`。`search` 在 C 阶段补充。
- 集合字段：
  - `chunk_id`：INT64 主键，即 document_chunks.id；
  - `document_id`、`version_id`；
  - `text`：向量化所用文本，VARCHAR，开启中文分词；
  - `dense`：FLOAT_VECTOR；
  - `sparse`：由 BM25 函数从 `text` 生成。
- 索引：dense 为 AUTOINDEX/COSINE，sparse 为 SPARSE_INVERTED_INDEX/BM25，version_id 为 INVERTED。C 阶段的混合检索直接使用，无需重建索引。
- 方法是同步调用：worker 用 `asyncio.to_thread`，API 用线程池。pymilvus 导入时会调用 `load_dotenv()`，把 `backend/.env` 写入进程环境变量；模块在导入前设置 `PYTHON_DOTENV_DISABLED=1` 关闭这一行为，应用配置仍由 pydantic-settings 读取。

**约定**

- 集合名由前缀、模型与维度组成，例如 `finoracle_chunks_text_embedding_v4_1024`。
- 更换模型的步骤：配置新模型后，对需要的版本重新批准（写入新集合），切换后删除旧集合。
- 向量化文本由 `index_text` 生成：制度标题 + 章 + 节 + 条号（无条号时为标题路径）+ 换行 + 正文，截断到 6000 字（模型单条上限 8192 token）。修改这个规则等同于更换模型，已入库的版本要重新向量化。
- 按条文切分的文档，第一个条文之前的公文标题也加入每一条的向量化文本（`index_entries`）：条文正文常常不含制度主题，主题只写在标题里。登记标题与公文标题互相包含时只保留较长的一个。
- 只有公文标题、没有正文也没有文号的切片不写入向量库，避免占据检索结果；它仍保留在 document_chunks 中供校验。
- 修改制度标题不会自动更新向量。需要标题生效时，重新解析该版本，或下架后重新批准。
- 离线入库和在线查询使用同一个 EmbeddingProvider 配置（画板 B、C 区的约定）。
- 密钥：worker 用于入库向量化，API 用于检索时向量化问题（第 7 步起），两者都持有 `DASHSCOPE_API_KEY`。

## 9A 混合检索（2026-11-05 实现，代码在 `app/rag/retrieval/`，接口 `POST /search`）

C 阶段的第一部分，到检索为止，尚未包含重排与大模型生成。

- **检索范围**：在一个短事务中取已发布版本的 ID（可按 `document_ids` 限定制度），没有已发布版本时直接返回空，不调用外部服务。之后检索期间不占用数据库连接。
- **两路检索**：
  - 稠密向量：问题经 `embed_query` 向量化，按余弦相似度检索；
  - BM25 关键词：问题原文经 Milvus 中文分词检索。
  - 两路都用 `version_id in [...]` 只在给定版本中检索，每路取 30 个候选。关键词检索不依赖查询向量，与向量化同时进行。
- **融合**：在应用中做倒数排名融合（RRF，k=60），而不用 Milvus 内置的 hybrid_search。这样每个结果都保留各路的名次与原始分数，供评测、调参以及后续判断依据是否充分。同分时取更好的名次，再按切片 ID 排序。
- **补全引用信息**：按切片 ID 联表取切片、版本与制度，并再次限定已发布；检索过程中被下架的版本不会出现在结果里。结果带制度标题、发文机构、文号、版本说明、日期、时效状态、来源、章节、条号、页码与正文。
- **一致性**：Milvus 检索使用强一致读取，刚发布的版本立即可检索；单机实测比默认的有界一致只多几毫秒。
- **查询与文档的区分**：DashScope 兼容接口虽然接受 `text_type`，但实测查询与文档的向量完全相同（余弦 1.0），因此不区分，已入库的向量无需重建。
- **错误**：向量化服务不可用、密钥被拒、向量库不可用时，统一返回 503 `SEARCH_UNAVAILABLE`，具体原因只记日志；日志只记录耗时与数量，不记录用户的问题。
- **实测**：在真实 DashScope 与 Milvus 上，用切片原文查询，两路都把该切片排在第一，经 HTTP 的完整请求中位数约 330 ms。
- **尚未实现**：重排（Rerank）、依据是否充分的判断、大模型生成与引用标注、会话保存。

## 9B 可组合的检索方案（2026-11-10，代码在 `app/rag/pipelines/`）

一个 Pipeline 是从入库到检索的完整流程，由两部分组合而成，便于用户选择和横向评测：

- **入库部分**（`IngestPipeline`）= 一个解析器 + 若干索引器。切片、向量集合和索引状态都按入库部分的名字隔离，两个 Pipeline 可以共用一份入库结果。
- **检索部分**（`RetrievalPipeline`）声明需要的通道（dense、keyword、visual）。组合时校验这些通道都由入库部分提供，组合错误在启动时暴露。

| Pipeline | 入库部分 | 解析 | 通道 | 引用 |
| --- | --- | --- | --- | --- |
| `structured-hybrid`（默认） | `structured` | 多格式、OCR、按条文切片 | dense + BM25 | 条号、页码 |
| `pdf-block-visual` | `pdf-block` | PyMuPDF 文本块与表格，不做 OCR | dense + BM25 + 页面视觉 | 页码、bbox |

- **默认方案**走版本自身的生命周期，行为不变。**附加方案**由 `POST /admin/versions/{id}/pipelines/{name}` 按需建立（版本须为 ready 或 published），状态记在 `version_indexes`，失败不影响版本和默认方案。任务类型为 `pipeline`，可用原重试接口重试。
- **检索**：`POST /search` 的 `pipeline` 字段选择方案，只检索已发布且该方案索引就绪的版本。`GET /search/pipelines` 列出方案。
- **视觉通道**：模型（默认 ColSmol-500M）运行在独立的视觉编码服务（`backend/visual_service/`，Compose 的 `visual` profile），API 和 worker 不依赖 torch；未设置 `VISUAL_SERVICE_URL` 时该方案显示为不可用。页面多向量存在 Milvus 的结构体数组字段，按 MAX_SIM_COSINE 检索，命中的页展开为该页上的切片后参与 RRF。没有切片的页（如扫描页）不会出现在结果中。
- **另一套实现中未移植或已替换的部分**：Qdrant 换成 Milvus，TF-IDF 换成 Milvus BM25，文件登记换成 MySQL，向量化与默认方案共用同一 EmbeddingProvider（评测时只比较方案差异），文本块的向量化文本加上制度标题。重排和答案生成不在“入库到检索”的范围内，暂未移植。
- 增加方案：在 `registry.py` 的 `build_pipelines` 中组合入库和检索两部分；新的入库部分用新的名字。

## 10 外部数据导入

脚本 `scripts/import_release.py <交付目录>`：

1. 先确认 `processed/quality_report.json` 的 status 为 completed，只导入 `approved_for_integration=true` 的文档。
2. 对每份文档：按 `external_id=doc_id` 查找制度，不存在就新建。
3. 按 file_path 读取 PDF，核对 sha256 后上传到 MinIO，新建版本。版本元数据从 release_documents.json 映射。
4. 切片直接写入 document_chunks：external_id 取 chunk_id，ordinal 按原顺序。parser_version 记为 `release:{schema_version}`，状态置为 parsed。
5. 带 `--approve` 参数时，直接为这些版本创建索引任务。

- 重复运行是幂等的：同一制度下已有相同 sha256 的版本就跳过。
- 不合并 feat/data-collection 分支（它与 main 没有共同历史），只读取它的交付文件。

## 11 实施顺序

1. **基础设施**（2026-11-05 已完成）：Compose 增加 minio 服务；实现 ObjectStorage（首次写入时建桶）；增加 `S3_*` 配置项；为前端 Nginx 的 `admin/` 路径放宽上传限制。worker 服务移到第 3 步。
2. **数据表**（2026-11-05 已完成）：迁移、模型和 repository。repository 只提供基本读写；任务认领与续租放在第 3 步，管理端列表筛选放在第 4 步。
3. **任务框架与解析任务**（2026-11-05 已完成）：认领、续租、重试；实现 parse 任务和 progress 回调；转换后的 PDF 写入对象存储；Compose 增加 worker 服务。另加迁移 `e43a1cf1d3d5`，为执行中任务的租约建索引。
4. **管理 API**（2026-11-05 已完成）：导入、任务、制度、版本、切片、原文、重试、重新解析和删除。批准、发布、下架移到第 5 步。
5. **向量化与发布**（2026-11-05 已完成）：DashScope 与 Milvus 两个 Provider；解析后自动批准（可关闭）、批准、发布、下架；下架、替换与删除时清理向量。Compose 增加 Milvus（单容器，内嵌 etcd、本地存储）。
6. **release 数据导入脚本**。

第 1–4 步不依赖向量库和 Embedding；第 5 步按第 12 节的选型实现。

测试方式：

- 单元测试用内存假实现替代存储、向量库和 Embedding。
- 集成测试沿用 `tests/integration` 的容器方式，加入 MinIO，覆盖崩溃后租约回收、重复认领和发布切换。

## 12 选型结论（2026-11-05 确认）

1. **向量库：Milvus standalone，使用 v2.6.23（2.6 系列仍在维护；3.0 于 2026-07 发布，暂不采用）。**
   - 画板中已经列出。
   - 开发环境按官方 standalone_embed 方式单容器运行：内嵌 etcd，数据存本地卷，不依赖对象存储。需要备份或扩展时再改为独立 etcd 加对象存储的部署。
   - 2.5 版起原生支持 BM25 全文检索，混合检索可以在一处完成。
   - 镜像约 1.4 GB，内存占用较高，部署前要按服务器配置评估。
   - 镜像固定为 `milvusdb/milvus:v2.6.23`，Python 客户端为 pymilvus 2.6。
2. **Embedding：先用 Qwen text-embedding API（DashScope）。**
   - 与画板中的 LLM 选型同源，不占用服务器算力。
   - 采用 `text-embedding-v4`、1024 维（可通过 `EMBEDDING_MODEL`、`EMBEDDING_DIMENSION` 调整）；查询延迟需要实测。
   - BGE-M3 本地部署保留为可选 Provider。
3. **批准门槛：默认自动批准（2026-11-05 改为此决定）。**
   - 原定解析后须管理员批准才向量化；现改为完整解析的版本由 worker 自动批准，`AUTO_APPROVE_PARSED=false` 可恢复人工批准。
   - 含待 OCR 图片（needs_ocr）的版本仍需人工批准，与数据契约"不能把未识别内容当作完成"一致。
   - 发布仍需管理员操作。
4. **对象存储镜像：开发环境用 `pgsty/silo:RELEASE.2026-10-26T00-00-00Z`（MinIO 维护分支）。**
   - 原定的 Quay 镜像已无法匿名拉取，见第 5 节。
   - 代码只依赖 S3 API。
   - 生产环境到部署时再定：沿用 Silo，或换成其他 S3 兼容实现。

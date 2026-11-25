# 文档入库与检索架构

本文总结 FinOracle 的文档入库、对象存储、核心数据表与状态机设计。接口契约见 [API 说明](api.md)，运行与配置见 [后端说明](../backend/README.md)。

## 总体链路

入库（解析、OCR、向量化）为 CPU / I/O 密集型长任务，由后台 `worker` 异步处理；检索与问答在 API 进程内全链路异步执行。

```text
浏览器 ──multipart──▶ API
                      │ 1 校验类型与大小，计算 SHA-256
                      │ 2 原件写入 S3 对象存储（Silo）
                      │ 3 同事务写入 document_versions 与 import_jobs(queued)
                      ▼
                    MySQL ◀── 认领任务（FOR UPDATE SKIP LOCKED）── worker
                                                                   │ 下载原件 → 转换 → 解析/OCR → 清洗 → 切片
                                                                   │ 转换后的 PDF 写回对象存储
                                                                   │ 切片写入 MySQL
                                                                   │ 向量化写入 Milvus
```

## 对象存储（S3 API）

通过 `app/storage/` 的 `ObjectStorage` 接口访问 S3 兼容存储（本地 Compose 使用 MinIO 维护分支 `pgsty/silo`），单桶 `finoracle-documents`，首次写入时自动建桶。对象键规则定义在 `app/storage/keys.py`：

- `documents/{document_id}/{version_id}/original.{ext}`：原始上传文件。
- `documents/{document_id}/{version_id}/converted.pdf`：`.doc` / `.rtf` / `.odt` 转换后的 PDF（切片页码以此为准）。
- `documents/{document_id}/{version_id}/pipelines/{ingest}/p{page:05d}.png`：可选页图检索方案的渲染页图。

浏览器不直连对象存储，由 API 鉴权后流式返回原件或预览 PDF。

## 核心表结构（MySQL）

- `documents`：制度主表，记录标题、发文机构、分类、标签（JSON）与外部 ID。
- `document_versions`：制度版本，记录文件元数据（文件名、类型、大小、SHA-256）、文号、生效/发布日期、时效状态（`unknown` / `effective` / `not_yet_effective` / `amended` / `repealed`）、解析结果摘要、向量化信息与发布状态。同一制度最多一个 `published` 版本，且不允许上传相同 SHA-256 的重复原件。
- `document_chunks`：解析后的条文切片，记录序号、章/节/条号、标题、分段（`part` / `part_count`）、正文、PDF 页码范围与内容来源（`text` / `ocr`）。
- `import_jobs`：后台任务表，记录任务类型（`parse` / `index` / `pipeline`）、状态（`queued` / `running` / `succeeded` / `failed` / `cancelled`）、阶段、进度、租约时间（`lease_expires_at`）、重试次数与错误码。

## 版本状态与任务流转

版本状态机：

```text
uploaded ──▶ parsing ──▶ parsed ──(批准/自动批准)──▶ indexing ──▶ ready ──(发布)──▶ published
                │           ▲                                       │                  │
                ▼           │                                       │                  ▼ (替换/下架)
              failed ───────┴──(重解析)─────────────────────────────┘               archived
```

- **解析（`parse`）**：完整解析且 `AUTO_APPROVE_PARSED=true` 时自动入队索引任务；含待 OCR 图片（`needs_ocr`）时停留在 `parsed`，等待人工校验后批准或重新解析。
- **索引（`index`）**：使用 `EmbeddingProvider`（默认 DashScope `text-embedding-v4`）向量化并写入 Milvus（含 BM25 稀疏向量），成功后版本进入 `ready`。
- **发布 / 下架**：发布 `ready` 版本会将同制度原 `published` 版本自动置为 `archived` 并清理其向量；`archived` 版本保留原件和切片供历史引用，可重新批准上线。
- **租约与重试**：worker 以 `READ COMMITTED` + `FOR UPDATE SKIP LOCKED` 认领任务，执行期间定期续租；瞬时故障（存储、转换、向量服务不可用）按退避重试，解析内容错误直接失败。

## 检索方案

`app/rag/pipelines/` 组合解析、索引与检索策略：

- `structured-hybrid`（默认）：条文结构化切片 + Milvus 稠密向量与 BM25 混合检索（RRF 融合）。
- `pdf-block-visual` / `page-image-visual`（可选）：配置视觉服务后启用，支持按 PDF 版面块或整页图像检索。

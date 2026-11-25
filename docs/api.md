# 后端 API 说明

本文概括 FinOracle 后端接口约定，完整字段与示例以运行实例的 `/docs` 或 `/openapi.json` 为准。默认前缀 `/api/v1`（本地 Compose 为 `http://127.0.0.1:8001/api/v1`，前端通过同源 `/api/v1` 代理）。

## 认证、账号与用户管理

默认 Access Token 30 分钟、Refresh Token 7 天（Bearer JWT）。客户端须串行刷新并同时替换双令牌；重放旧 Refresh Token 会撤销对应会话。禁用账号立即使全部旧会话失效，重新启用须重新登录。

| 接口 | 说明 |
| --- | --- |
| `POST /auth/register` | 注册普通用户（`username`, `email`, `password`, `confirm_password`） |
| `POST /auth/login` | 用户名或邮箱登录，返回双令牌与 `UserResponse` |
| `POST /auth/refresh` | 使用 `refresh_token` 轮换双令牌 |
| `POST /auth/logout` | 撤销当前会话（204） |
| `GET` / `PATCH /users/me` | 查询或修改当前用户的 `username` / `email` |
| `GET /admin/users` | 管理员分页查询用户（`q`, `role`, `status`, `page`, `page_size`） |
| `GET /admin/users/{user_id}` | 管理员查询用户详情 |
| `PATCH /admin/users/{user_id}/status` | 管理员启用/禁用普通用户（不可操作管理员） |

## 制度库与个人收藏

登录用户可浏览已发布制度并管理个人收藏夹与收藏项（制度、条文切片或问答轮次）。

| 接口 | 说明 |
| --- | --- |
| `GET /library/documents` | 分页浏览已发布制度（支持搜索、分类、标签、时效筛选与分面统计） |
| `GET /library/documents/{document_id}` | 读取已发布制度详情及当前发布版本 |
| `GET /library/documents/{document_id}/chunks` | 分页阅读已发布版本的条文切片 |
| `GET /library/documents/{document_id}/file` | 流式读取已发布版本原件或预览 PDF |
| `GET` / `POST /favorites/folders` | 查询收藏夹树 / 创建收藏夹 |
| `PATCH` / `DELETE /favorites/folders/{folder_id}` | 重命名、移动或删除收藏夹 |
| `GET` / `POST /favorites` | 分页查询收藏列表 / 新增收藏 |
| `PATCH` / `DELETE /favorites/{favorite_id}` | 更新收藏备注/目录/标签或取消收藏 |

## 文档入库与版本管理（管理员）

以下接口均需管理员 Bearer Access Token。上传后返回 202 `ImportJob`，由后台 worker 解析并按配置自动进入向量化；详情见 [文档入库架构](document-ingestion.md)。

| 接口 | 说明 |
| --- | --- |
| `POST /admin/documents/parse` | 同步解析预览（`multipart/form-data`，不入库，最大 50 MB） |
| `POST /admin/imports` | 上传文件并创建导入任务（新建制度传 `title`，追加版本传 `document_id`） |
| `GET /admin/imports` / `GET /admin/imports/{job_id}` | 分页查询或查看导入任务进度与错误 |
| `POST /admin/imports/{job_id}/retry` | 重试 `failed` 状态的导入任务 |
| `GET /admin/documents` | 分页查询制度列表（支持 `q`、`published`、`category`、`tag`） |
| `GET` / `PATCH /admin/documents/{document_id}` | 查看或更新制度元信息（标题、机构、分类、标签） |
| `GET /admin/documents/{document_id}/versions` | 查看制度的全部版本（从新到旧） |
| `GET` / `PATCH` / `DELETE /admin/versions/{version_id}` | 查询版本、更新元信息或删除未发布/非归档草稿版本 |
| `GET /admin/versions/{version_id}/chunks` | 分页读取版本切片 |
| `GET /admin/versions/{version_id}/file` | 流式读取原件（`variant=original`）或转换后的 PDF（`variant=pdf`） |
| `POST /admin/versions/{version_id}/reparse` | 重新解析（`parsed` / `ready` / `failed`） |
| `POST /admin/versions/{version_id}/approve` | 批准向量化（`parsed` / `archived`） |
| `POST /admin/versions/{version_id}/publish` | 发布 `ready` 版本（原发布版本自动归档） |
| `POST /admin/versions/{version_id}/withdraw` | 下架 `published` 版本为 `archived` |
| `GET` / `POST /admin/versions/{version_id}/pipelines/{name}` | 查看或触发附加检索方案索引 |

## 条文检索

| 接口 | 说明 |
| --- | --- |
| `GET /search/pipelines` | 列出检索方案及可用状态（默认 `structured-hybrid`） |
| `POST /search` | 在已发布制度中检索（`query`, 可选 `document_ids`, `top_k`, `pipeline`） |
| `GET /search/images/{chunk_id}` | 读取页图检索结果对应的 PNG 图像 |

## 制度问答与会话

| 接口 | 说明 |
| --- | --- |
| `POST /chat/stream` | SSE 流式问答（`question`, 可选 `session_id`, `document_ids`, `request_id`） |
| `GET` / `POST /chat/sessions` | 查询会话列表（支持 `q`、`pinned`、`archived` 与游标分页）/ 新建会话 |
| `GET` / `PATCH` / `DELETE /chat/sessions/{session_id}` | 查看轮次详情、重命名/置顶/归档或删除会话 |
| `GET /chat/references/{kind}/{id}` | 打开条文或版本引用详情（`kind` 为 `chunk` / `version`，下架后返回 404） |

`/chat/stream` 事件依次包含 `turn`、`model`、`tool`、`progress`、`answer_delta`、`paragraph`、`done`（或 `error`）。架构细节见 [LLM 架构](llm-architecture.md)。

## 统一错误格式

错误响应统一为 `{code, message, request_id}`。常见状态码：

| HTTP | 含义 |
| --- | --- |
| 401 | 凭据或令牌无效（客户端可尝试一次串行刷新） |
| 403 | 账号被禁用或无管理员权限 |
| 404 | 资源不存在或已下架不可见 |
| 409 | 唯一键冲突、重复文件或版本/任务状态冲突 |
| 413 / 415 | 文件过大、页数超限或格式不支持 |
| 422 | 参数校验失败或文件损坏/为空 |
| 429 | 登录失败次数超限 |
| 503 | 数据库、Redis、存储、检索或问答服务暂不可用 |

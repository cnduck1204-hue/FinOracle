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

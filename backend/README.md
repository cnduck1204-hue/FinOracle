# FinOracle 后端

Python 3.11+、FastAPI、SQLAlchemy AsyncSession、MySQL 8.4、Redis 7.4。以下命令均在 `backend/` 目录执行。

## 运行

完整开发环境：

```sh
python3 scripts/init_env.py
docker compose up --build -d --wait
```

默认 API 端口为 8001，MySQL 为 3306，Redis 为 6379；都仅绑定本机。API 启动时运行 `alembic upgrade head`。可用 `docker compose logs api` 查看启动情况。

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
| POST | `/auth/logout-all` | 退出该用户全部会话 | 204 |
| GET | `/users/me` | 查询自己的公开资料 | 200 |

注册提交 `username`、`email`、`password`、`confirm_password`；登录提交 `identifier`、`password`；刷新提交 `refresh_token`。后三个受保护接口（退出、全部退出、个人信息）使用 `Authorization: Bearer <access_token>`。刷新接口不要求 Access Token。

Access Token 默认 30 分钟，Refresh Token 默认 7 天。刷新沿用同一会话，每次更换令牌标识，并将会话过期时间更新为新 Refresh Token 的过期时间。旧刷新令牌重放会撤销整个会话；客户端须串行刷新并替换保存的双令牌。退出会同时阻止该会话的 Access Token 和 Refresh Token。

登录标识统一去首尾空白并转小写，密码保持原样；数据库重音或不可见字符的近似匹配不会被当成同一登录标识。默认连续 5 次失败后限制该标识 15 分钟；密码验证成功后清除失败计数。受保护请求同时检查令牌、Redis 会话及数据库中的最新账号状态；Redis 不可用时拒绝认证并返回 503。

错误 JSON 固定包含 `code`、`message`、`request_id`。通常 401 表示凭据或令牌无效、403 表示账号禁用、409 表示账号冲突、422 表示输入无效、429 表示登录受限、503 表示依赖不可用。时间统一输出 UTC。

## 配置

`.env.example` 列出完整默认项；真实 `.env` 不入库。`scripts/init_env.py` 为 MySQL 应用账号、root 账号和 JWT 分别生成随机值，已有 `.env` 会原样保留。

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

## 代码组织

- `api/`：请求模型绑定、认证依赖、响应和路由。
- `services/`：注册、登录、刷新、退出的业务规则及事务边界。
- `repositories/`、`models/`、`db/`：数据库访问、表模型和连接生命周期。
- `security/`：线程池中的 Argon2 运算、JWT 签发和校验。
- `cache/`：失败计数、会话及用户会话索引的原子操作。
- `core/`、`middleware/`：配置、统一错误、JSON 日志和请求追踪。

MySQL 保存用户；Redis 保存会话状态和到期分数索引，不保存原始令牌。新建会话和索引一起写入，刷新同步更新索引过期时间，全部退出通过一个 Lua 脚本撤销索引中的会话。当前实现面向独立 Redis 实例；Redis Cluster 需要另行设计键的分片布局。

会话命名空间已升级为 `finoracle:v2:auth:*`，未建立用户索引的旧会话不再接受，升级前的用户需重新登录。索引与会话自动过期。

## 验证

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

单元和接口测试不要求数据库、Redis或真实凭据。当前上游 Starlette/httpx 与 AnyIO 会发出两条弃用提示，不影响测试结果。

完整真实环境检查（先启动上述 Compose 环境）：

```sh
uv run python -m tests.integration
```

另含一次独立全新安装检查：

```sh
uv run python -m tests.integration --fresh-install
```

检查覆盖真实 HTTP 认证流程、事务回滚、Redis 回复丢失、刷新重放、并发轮换、会话索引、当前与全部退出。每项使用唯一测试数据并清理自己的记录。首次安装检查创建临时 Compose 项目和卷，只删除自己创建的临时资源。

## 下一阶段

资料修改、管理员初始化与用户管理尚未实现；金融文档解析、检索、RAG 和前端也未实现。完整目标参见[基础需求](../docs/superpowers/specs/2026-09-20-backend-foundation-prd.md)。

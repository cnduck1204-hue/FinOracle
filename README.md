# FinOracle

基于 RAG 的金融制度知识问答项目。目前已具备认证、制度文档入库（解析、向量化、发布）、混合检索，以及由 DeepSeek Harness 驱动的流式问答与条文溯源。

后端范围：注册、用户名或邮箱登录、Argon2 密码摘要、JWT 双令牌、刷新轮换与重放撤销、登录失败限制、个人信息查询与修改、当前会话退出、可选管理员初始化、管理员用户查询与启用/禁用；提供 MySQL 迁移、Redis 持久化、就绪检查及 Docker 开发环境。管理员禁用后旧会话失效，重新启用需要重新登录。

## 前端与完整环境

前端以问答界面为首页，访客可以先输入问题，使用需要账号的功能时在弹窗中登录或注册。工作台自动保存当前标签页的会话草稿，支持连续追问、查找和归档历史会话。回答通过流式接口逐段显示，支持停止生成、打开条文原文和查看制度版本时效。全文检索、制度文档管理、文档导入、导入任务与解析校验已接入后端。

从仓库根目录初始化开发配置：

```sh
python3 backend/scripts/init_env.py
```

在 `backend/.env` 填写 `DEEPSEEK_API_KEY` 和检索所需的 `DASHSCOPE_API_KEY`。Compose 随 API 启动 `dsh` 执行模型和工具调用。FastAPI 从 MySQL 读取近期已交付的问答，为每轮提供有界上下文。默认模型为 `deepseek-flash`，问答依据来自已发布且完成索引的制度。 [dsh 配置与升级](backend/dsh/README.md)

启动或升级完整环境：

```sh
docker compose --env-file backend/.env up --build -d --wait
```

前端入口：[http://127.0.0.1:8080](http://127.0.0.1:8080)。首次生成开发配置后，可以用用户名 `admin` 和 `backend/.env` 中的随机 `ADMIN_PASSWORD` 登录。本机开发和浏览器测试见[前端说明](frontend/README.md)。

## 单独启动后端

需要 Python 3.11+、Docker 与 Docker Compose。首次运行：

```sh
cd backend
python3 scripts/init_env.py
docker compose up --build -d --wait
```

初始化脚本为新环境生成独立随机凭据，也会为已有环境补齐缺失或留空的 `CHAT_DSH_TOKEN`。默认入口：

- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- 存活检查：[http://127.0.0.1:8001/api/v1/health](http://127.0.0.1:8001/api/v1/health)
- 依赖就绪：[http://127.0.0.1:8001/api/v1/ready](http://127.0.0.1:8001/api/v1/ready)

API 启动时自动执行数据库迁移。数据库、Redis 和 `dsh` 会话日志使用持久卷，普通停止或重建容器会保留数据。

## 项目结构

```text
backend/
  app/                 API、业务服务、仓储、安全与基础设施
  migrations/          Alembic 数据库迁移
  scripts/init_env.py   首次运行配置初始化
  tests/               单元与接口测试
    integration/       真实 MySQL、Redis、容器与完整认证流程检查
  dsh/                 DeepSeek Harness 问答运行时
  compose.yaml         本地开发环境
frontend/              React / TypeScript 前端、浏览器测试和 Nginx
compose.yaml           前后端完整开发环境
```

[后端说明](backend/README.md) 包含接口、配置、运行和验证命令。[后端 API 交接](docs/backend-handoff.md) 包含接口契约、Postman 与性能探针说明。[问答架构](docs/superpowers/specs/2026-11-19-llm-architecture-design.md) 说明运行时、会话和引用如何协作。

Redis 会话使用 `finoracle:v2:auth:*` 命名空间。Compose 仅供本地开发，不作为生产部署配置。

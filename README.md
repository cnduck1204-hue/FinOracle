# FinOracle

基于 RAG 的金融制度知识问答项目。目前已具备**认证基础后端与配套前端**，RAG 与制度数据功能仍在后续建设。

已实现：注册、用户名或邮箱登录、Argon2 密码摘要、JWT 双令牌、刷新轮换与重放撤销、登录失败限制、个人信息查询、当前会话退出和全部会话退出；提供 MySQL 迁移、Redis 持久化、就绪检查及 Docker 开发环境。

## 前端与完整环境

前端按浅灰、黑黄工业风实现，已接入注册、登录、个人资料、自动续期及两种退出；工作台可在当前标签页保存问题草稿。制度库、检索、历史和收藏明确显示为准备中。

在仓库根目录启动完整环境：

```sh
python3 backend/scripts/init_env.py
docker compose --env-file backend/.env up --build -d --wait
```

前端入口：[http://127.0.0.1:8080](http://127.0.0.1:8080)。本机开发和浏览器测试见[前端说明](frontend/README.md)。

## 单独启动后端

需要 Python 3.11+、Docker 与 Docker Compose。首次运行：

```sh
cd backend
python3 scripts/init_env.py
docker compose up --build -d --wait
```

初始化脚本只在 `.env` 不存在时生成独立随机凭据，不覆盖已有配置。默认入口：

- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- 存活检查：[http://127.0.0.1:8001/api/v1/health](http://127.0.0.1:8001/api/v1/health)
- 依赖就绪：[http://127.0.0.1:8001/api/v1/ready](http://127.0.0.1:8001/api/v1/ready)

API 启动时自动执行数据库迁移。数据库与 Redis 使用持久卷，普通停止、重建容器会保留数据。

## 项目结构

```text
backend/
  app/                 API、业务服务、仓储、安全与基础设施
  migrations/          Alembic 数据库迁移
  scripts/init_env.py   首次运行配置初始化
  tests/               单元与接口测试
    integration/       真实 MySQL、Redis、容器与完整认证流程检查
  compose.yaml         本地开发环境
frontend/              React / TypeScript 前端、浏览器测试和 Nginx
compose.yaml           前后端完整开发环境
```

[后端说明](backend/README.md) 包含接口、配置、运行和验证命令；[后端基础需求](docs/superpowers/specs/2026-09-20-backend-foundation-prd.md) 保留后续建设目标。当前范围对应 M1、M2，并提前完成了个人信息查询；资料修改、管理员权限管理和 RAG 留待下一阶段。前端现已接入上述认证接口。

本次会话索引升级使用新的 Redis 会话命名空间，升级前签发的令牌需要重新登录。Compose 仅供本地开发，不作为生产部署配置。

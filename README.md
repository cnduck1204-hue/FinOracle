# FinOracle

基于 RAG 的金融制度知识问答与合规溯源工作台，包含认证与权限管理、制度文档入库（解析、OCR、向量化、版本发布）、制度库浏览、个人收藏、混合检索，以及由 DeepSeek Harness 驱动的流式问答与条文溯源。

## 快速启动

需要 Python 3.11+、Docker 与 Docker Compose。在仓库根目录初始化本地配置：

```sh
python3 backend/scripts/init_env.py
```

在 `backend/.env` 中填写 `DEEPSEEK_API_KEY`（问答）与 `DASHSCOPE_API_KEY`（向量化与检索），随后启动完整环境：

```sh
docker compose --env-file backend/.env up --build -d --wait
```

- 前端入口：[http://127.0.0.1:8080](http://127.0.0.1:8080)（首次初始化后可用用户名 `admin` 与 `backend/.env` 中的随机 `ADMIN_PASSWORD` 登录）
- API 文档：[http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)
- 健康检查：`GET /api/v1/health`（存活）· `GET /api/v1/ready`（MySQL 与 Redis 就绪）

API 启动时自动执行数据库迁移；数据库、Redis、对象存储与 `dsh` 日志使用持久卷，普通重启保留数据。Compose 仅用于本地开发，不作为生产部署配置。

## 项目结构

```text
backend/        FastAPI 后端、文档解析与 RAG、后台 worker、Alembic 迁移与测试
  dsh/          DeepSeek Harness 问答运行时桥接
frontend/       React + TypeScript + Vite 前端、Playwright E2E 与 Nginx
docs/           API、入库架构、LLM 架构与前端视觉规范
compose.yaml    前后端完整本地开发环境
```

## 文档索引

- [后端说明](backend/README.md)：配置项、本机调试、文档解析与测试命令
- [前端说明](frontend/README.md)：前端功能、本地开发、认证约定与测试
- [dsh 问答运行时](backend/dsh/README.md)：问答模型配置、本机调试与独立测试
- [后端 API 说明](docs/api.md) · [文档入库架构](docs/document-ingestion.md) · [LLM 架构](docs/llm-architecture.md) · [前端风格](docs/frontend-style.md)

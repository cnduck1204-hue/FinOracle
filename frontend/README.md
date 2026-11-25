# FinOracle 前端

React 19 + TypeScript + Vite 构建的金融制度研究与合规溯源工作台。视觉规范见 [前端风格](../docs/frontend-style.md)。

## 功能概览

- **智能问答（`/chat`）**：首页即问答工作台，访客可先输入问题，触发需账号的操作时弹窗登录或注册。支持流式回答、停止生成、条文原文侧栏核对、单标签页草稿保存，以及侧栏会话置顶、重命名、搜索、归档与分页（`/history`）。
- **全文检索（`/search`）**：面向登录用户的已发布制度检索与来源查看。
- **制度库（`/library`）**：按分类、标签、时效与关键词浏览已发布制度，查看正文切片与原件预览；管理员可直接跳转版本管理或发起导入。
- **我的收藏（`/favorites`）**：多级收藏夹管理，支持收藏制度、条文切片与问答轮次，附备注与自定义标签。
- **账号与管理**：个人资料查询与修改（`/account`）；管理员可使用文档导入（`/admin/imports/new`）、导入任务与解析校验（`/admin/imports`、`/admin/imports/:jobId`）、版本管理（`/admin/documents/:id/versions`）及用户管理（`/admin/users`）。

## 本地开发

需要 Node.js 22.12+（建议 24）。先按 [后端说明](../backend/README.md) 启动后端（Compose 默认端口 `8001`，本机 Uvicorn 为 `8000`），再运行：

```sh
cd frontend
npm ci
npm run dev
```

访问 `http://127.0.0.1:5173`。Vite 默认将 `/api/v1` 同源代理至 `127.0.0.1:8001`；如需修改端口或前缀，复制 `.env.example` 为 `.env.local` 并调整 `API_PROXY_TARGET` 与 `VITE_API_BASE_URL`。

## 验证

```sh
npm test
npm run format:check
npm run build
```

真实浏览器 E2E 测试（需后端已运行）：

```sh
npx playwright install chromium
npm run test:e2e
```

可通过 `E2E_BASE_URL` 和 `E2E_API_URL` 指定目标地址；测试仅使用随机 `e2e_` 账号并在结束后精确清理。

## 目录与认证约定

- `src/lib/`：API 客户端与令牌串行刷新、SSE 流解析、入库辅助、类型与校验
- `src/auth/`：登录恢复、单标签页租约与账号状态上下文
- `src/components/`：布局外壳、认证弹窗、问答渲染、会话目录、收藏弹窗与入库表单
- `src/pages/`：问答、检索、制度库、收藏、账号与管理页面
- `src/styles/`：按 `tokens.css`、`base.css`、`layout.css`、`motion.css`、`pages.css` 分层维护

Access Token 仅存内存，Refresh Token 存当前标签页 `sessionStorage` 并通过 Web Locks 保持单标签页持有；并发 401 复用同一次刷新 Promise 并最多重试一次。

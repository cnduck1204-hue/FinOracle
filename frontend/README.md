# FinOracle 前端

React + TypeScript + Vite，参考根目录 `frontend-style.md` 与 `docs/design/2026-10-27-frontend/` 的布局。采用浅灰纸色、黑色结构线、单一黄绿色强调色、切角操作与原创 SVG 几何图形，没有使用游戏素材。

## 已接入的功能

- 注册与用户名 / 邮箱登录，表单校验和服务端错误反馈。
- 登录恢复、串行自动续期、受保护页面与账号禁用 / 会话失效处理。
- 当前用户资料查询、退出当前会话、带确认的全部退出。
- 响应式工作台、按账号隔离的当前标签页问题草稿。

制度库、全文检索、历史与收藏显示「准备中」，问答暂不请求不存在的接口，也不生成演示答案。账号资料保持只读，与现有后端能力一致。

## 开发启动

需要 Node.js 22.12+（建议 24）、npm；后端完整 Compose 环境默认 API 位于 `127.0.0.1:8001`；先按 `backend/README.md` 启动该环境。本机直接运行 Uvicorn 时使用端口 8000，此时将下述 `API_PROXY_TARGET` 改为 `http://127.0.0.1:8000`。

```sh
cd frontend
npm ci
npm run dev
```

打开 `http://127.0.0.1:5173`，可注册自己的账号体验。Vite 默认将 `/api/v1` 代理给后端，同源访问，不需要放宽后端跨域策略。若接口端口或前缀不同，复制 `.env.example` 为 `.env.local` 后调整 `API_PROXY_TARGET`、`VITE_API_BASE_URL`；两端前缀需一致，例如 `/api/v1` 或 `/v2`。Vite 和 Docker/Nginx 会使用相同的非根路径前缀。

完整 Docker 环境在仓库根目录运行：

```sh
python3 backend/scripts/init_env.py
docker compose --env-file backend/.env up --build -d --wait
```

访问 `http://127.0.0.1:8080`。前端使用非 root Nginx 提供构建产物，默认将 `/api/v1` 转发给 API 服务；深层路径直接刷新会回到单页应用。可通过 `FRONTEND_PORT` 修改宿主端口。

## 验证

```sh
npm test
npm run format:check
npm run build
```

`build` 先进行严格类型检查。真实浏览器回归检查要求后端已运行：

```sh
npx playwright install chromium
npm run test:e2e
```

检查覆盖注册、登录、刷新页面时的单次续期、本地草稿、资料查询、两种退出、错误反馈和移动端布局。测试只创建随机 `e2e_` 前缀账号，结束后通过本地后端环境清理这些明确命名的账号与其会话，不会清空数据库或 Redis。部署版可通过 `E2E_BASE_URL=http://127.0.0.1:8080` 验证；后端直连地址由 `E2E_API_URL` 指定。

## 组织与认证约定

- `src/lib/api.ts`：API 错误、令牌生命周期、并发刷新与一次重试。
- `src/auth/`：登录恢复、账号状态和页面共享会话。
- `src/components/`：布局、反馈、品牌与原创图形。
- `src/pages/`：认证、问题工作台、账号与待开放页面。
- `src/styles.css`：集中维护视觉变量、响应式布局和减少动效支持。

Access Token 仅保存在内存，Refresh Token 保存在当前标签页的 `sessionStorage`；退出成功时清除凭据。浏览器阻止存储或不支持 Web Locks 时退化为内存会话。支持 Web Locks 时，同一登录会话由一个标签页持有；复制标签页会提示在新页重新登录，不会拿复制的旧 Refresh Token 进行轮换。关闭或恢复标签页的存储行为由浏览器决定，不会主动撤销服务端会话；公共设备上应主动退出。当前后端以 JSON 返回令牌，未来若要使用 HttpOnly Cookie，需要前后端共同调整认证协议。

同一客户端的刷新请求复用一个 Promise，延迟返回的旧令牌 401 会先重用已更新的 Access Token，最多重试一次。网络或 503 错误不会伪装成退出成功；旧异步结果不能恢复已放弃的登录。所有 UI 用户资料均以服务端响应为准。

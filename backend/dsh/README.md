# DeepSeek Harness 问答运行时

`dsh` 是 FinOracle 的问答运行时，随默认 Compose 环境启动。从仓库根目录运行初始化命令，在 `backend/.env` 填写 `DEEPSEEK_API_KEY` 和 `DASHSCOPE_API_KEY`，再启动服务。

```sh
python3 backend/scripts/init_env.py
docker compose --env-file backend/.env up --build -d --wait
```

| 配置 | 用途 |
| --- | --- |
| `CHAT_DSH_TOKEN` | API 与 `dsh` 的共享令牌。初始化脚本自动生成，也可为现有 `.env` 补齐。 |
| `CHAT_DSH_URL` | FastAPI 的内部桥接地址。Compose 默认使用 `http://dsh:8091`，可按实际网络覆盖。 |
| `CHAT_DSH_CONTEXT_WINDOW` | 模型可用上下文窗口，默认 32768 tokens。切换模型时按其容量调整。 |
| `CHAT_MODEL`、`CHAT_BASE_URL` | 模型名称与提供方地址，默认 `deepseek-flash` 和 `https://api.deepseek.com`。 |
| `DEEPSEEK_API_KEY` | 模型凭据，由 `dsh` 使用。留空时可以启动本地环境，问答会返回服务不可用。 |

FastAPI 负责账户隔离、制度工具和引用校验。`dsh` 运行 Agent Loop，通过私有 HTTP 传送调用状态、工具请求、正文增量和完成文本。默认 Compose 只在容器网络内开放 `dsh`。运行记录保存在 `dsh_state` 卷，官方模型请求的会话日志上传已关闭。

历史列表和已交付问答保存在 MySQL。每个轮次独立创建运行会话，用当前业务会话的已交付历史恢复上下文。历史制度引用要通过工具重新核对当前可见性，再签发本轮标记。浏览器断线或停止生成后可恢复已保存的正文。

升级已有环境也使用上述命令，一次重建 `api` 和 `dsh`，继续使用原来的数据库和 `dsh_state` 卷。

## 自定义

FastAPI 每轮将提示词、工具 schema 和模型调用上限交给 `dsh`，并随工具结果返回 `remaining_tool_calls`。查询额度用完后，`dsh` 后续模型调用只使用已取得的依据。FastAPI 继续校验已经发出的查询。 [提示词](../app/agent/context.py) · [工具定义](../app/agent/tools.py)

模型协议选项和插件范围由 Harness 配置管理。跨轮上下文长度由 FastAPI 的历史投影控制。 [profile.yml](profile.yml)

## 本机调试

调试宿主机上的 FastAPI 时，在 `backend/` 下启动仅绑定回环地址的桥接服务。

```sh
docker compose -f compose.yaml -f - up --build -d --wait dsh <<'YAML'
services:
  dsh:
    ports:
      - "127.0.0.1:8091:8091"
YAML
```

本机 FastAPI 默认连接 `http://127.0.0.1:8091`。如果已有 `.env` 中的 `CHAT_DSH_URL` 指向容器地址，以 `CHAT_DSH_URL=http://127.0.0.1:8091` 覆盖本机 FastAPI 的启动环境。

## 验证独立运行时

需要 Node.js 24.9 或更高版本。测试用真实 `dsh` 和本机模拟模型服务验证制度工具、正文增量、重启后的历史恢复和取消。

```sh
dsh_test_root=$(mktemp -d)
npm install --prefix "$dsh_test_root" --no-audit --no-fund --ignore-scripts @deepseek-ai/dsh@0.2.2-rc.5
cp backend/dsh/{bridge.mjs,bridge.test.mjs,profile.yml} "$dsh_test_root/"
node --test "$dsh_test_root/bridge.test.mjs"
```

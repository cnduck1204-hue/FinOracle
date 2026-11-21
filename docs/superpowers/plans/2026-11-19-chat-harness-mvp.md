# FinOracle 问答 Harness 实现计划

此文记录最初的接入和验收。现行实现已迁移到 DeepSeek Harness，原 MVP 的 `/api/v1/chat` 和 Python 直连执行循环已移除。 [现行架构](../specs/2026-11-19-llm-architecture-design.md) · [接口契约](../../backend-handoff.md#制度问答与会话)

**目标**　普通账号在 `/chat` 提问，获得带编号的回答，并能点击查看对应的已发布条文。

**实现范围**　执行架构设计的第一阶段。React 工作台通过 FastAPI 调用请求内 Harness，模型通过一个 `search_knowledge` 工具使用 `structured-hybrid`。后端根据本轮实际提供的证据生成引用。 [架构设计](../specs/2026-11-19-llm-architecture-design.md)

## 1. 后端问答与引用

- [x] 验证 `/api/v1/chat` 要求登录，且通过现有检索逻辑与模拟模型完成回答。覆盖伪造引用、资料下架和模型超时。
- [x] 在 `app/agent` 实现模型适配、上下文预算、工具分发、最多三次模型调用和引用校验。第三次调用关闭工具，保留已获取的证据用于收尾。
- [x] 注册 `POST /api/v1/chat` 和 `GET /api/v1/chat/references/chunk/{chunk_id}`。请求使用 `question` 和可选 `document_ids`。回答返回 `status`、`answer`、`citations`，条文详情重新检查发布状态。
- [x] 问答使用 `DEEPSEEK_API_KEY`，默认连接 `https://api.deepseek.com` 的 `deepseek-flash`，关闭思考模式。请求总超时默认为 10 秒，可通过 `CHAT_TIMEOUT_SECONDS` 调整到最多 15 秒。模型和地址可通过 `CHAT_MODEL`、`CHAT_BASE_URL` 覆盖。

## 2. 工作台接入

- [x] 验证鉴权客户端发出问答与条文详情请求。
- [x] 接入提交、等待、结果、依据不足和重试状态。答案按纯文本渲染，从已验证的引用生成可点击编号。点击时加载条文、版本、时效和来源。
- [x] 保留已有的标签页草稿。

## 3. 验证

- [x] 运行后端 `pytest -q` 和 Ruff，前端 `npm test` 和 `npm run build`。后端 864 项通过、1 项跳过，前端 57 项通过。
- [x] 移动版 Chromium 使用模拟 API 核对草稿、失败重试、点击引用和来源下架提示。使用真实 DeepSeek API 与合成制度样例核对模型选工具、检索、回答和引用原文。

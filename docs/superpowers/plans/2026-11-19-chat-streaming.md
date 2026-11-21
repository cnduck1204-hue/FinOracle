# FinOracle 流式问答实现

本文记录此前按 JSON 段落交付的方案。当前 SSE 契约见 [接口交接](../../backend-handoff.md#制度问答与会话)。

让工作台在请求期间显示检索和生成状态，并逐段显示已经校验的回答与出处。DeepSeek 使用流式生成，FastAPI 通过 `POST /api/v1/chat/stream` 输出 SSE，`done` 事件包含完整结果。 [LLM 架构](../specs/2026-11-19-llm-architecture-design.md)

- `dsh` 执行单 Agent 循环，通过私有桥接把工具调用和文本增量送给 FastAPI。FastAPI 对完整段落校验本轮证据和发布状态，引用编号在同一回答内保持稳定。字段换序时，在完整 JSON 对象抵达后校验和发送段落。SSE 使用 `turn`、`progress`、`tool`、`paragraph`、`done` 和 `error`，每条响应内的事件 `id` 从 `1` 递增。`turn` 同时携带 `session_id` 和 `turn_id`。
- 前端使用带鉴权的 Fetch 读取 SSE，跨分片解码中文和事件，并检查同一响应内的事件序号。每一轮在回答上方展示工具步骤及状态，同一个回答节点接收已核验段落和落库后的 `done`，保留已打开的引用面板。正文渲染 Markdown。停止或断流后读取会话记录，等待服务端收尾时仍显示已收到的段落。FastAPI 用 `X-Accel-Buffering` 响应头控制 Nginx 转发缓冲。
- 验证首段在模型流结束前送达，伪造引用不会进入流，前端能从拆分的 SSE 帧收到段落和完成状态。用真实 DeepSeek 和合成制度样本做一次联调。

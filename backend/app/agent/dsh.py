import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import cast, get_args
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import SYSTEM_PROMPT, ChatContext, history_hints
from app.agent.delivery import TurnEvidence, deliver, split_stream_text
from app.agent.tools import (
    MAX_TOOL_EXECUTIONS,
    TOOL_DEFINITIONS,
    ChatTools,
    parse_tool_arguments,
    tool_summary,
    tool_target,
)
from app.core.config import ChatSettings
from app.core.exceptions import AppException
from app.rag.pipelines import PipelineRegistry
from app.schemas.chat import (
    ChatAnswerDelta,
    ChatModelStep,
    ChatParagraph,
    ChatProgress,
    ChatRequest,
    ChatResponse,
    ChatToolName,
    ChatToolStep,
)
from app.services.chat_sessions import StartedTurn

logger = logging.getLogger(__name__)
_ADMISSION_ATTEMPTS = 6
MAX_MODEL_CALLS = 3
PUBLIC_TOOLS = frozenset(get_args(ChatToolName))
ChatUpdate = (
    ChatProgress
    | ChatModelStep
    | ChatToolStep
    | ChatAnswerDelta
    | ChatParagraph
    | ChatResponse
)


def chat_unavailable() -> AppException:
    return AppException(
        code="CHAT_UNAVAILABLE",
        message="问答服务暂时不可用，请稍后重试",
        status_code=503,
    )


async def _frames(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], payload: dict
) -> AsyncIterator[dict]:
    for attempt in range(_ADMISSION_ATTEMPTS):
        async with client.stream(
            "POST", url, headers=headers, json=payload
        ) as response:
            if response.status_code != 409 or attempt == _ADMISSION_ATTEMPTS - 1:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield json.loads(line)
                return
        # The preceding turn may still be disposing after its HTTP reader closed.
        await asyncio.sleep(0.05)


async def run_updates(
    request: ChatRequest,
    session: AsyncSession,
    pipelines: PipelineRegistry,
    settings: ChatSettings,
    request_id: str,
    turn: StartedTurn,
) -> AsyncIterator[ChatUpdate]:
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + settings.chat_timeout_seconds
    model_calls = tool_calls = 0
    try:
        async with (
            httpx.AsyncClient(timeout=None, trust_env=False) as client,
            aclosing(
                _run_turn(
                    client, request, session, pipelines, settings, turn, request_id
                )
            ) as updates,
        ):
            while True:
                if loop.time() >= deadline:
                    raise TimeoutError
                async with asyncio.timeout_at(deadline):
                    update = await anext(updates, None)
                if update is None:
                    return
                if isinstance(update, ChatModelStep) and update.state == "running":
                    model_calls += 1
                if isinstance(update, ChatToolStep) and update.state == "running":
                    tool_calls += 1
                if isinstance(update, ChatResponse):
                    logger.info(
                        "chat_candidate_delivered turn_id=%s status=%s agent_model_calls=%d tool_calls=%d sources=%d",
                        turn.id,
                        update.status,
                        model_calls,
                        tool_calls,
                        len(update.citations),
                        extra={
                            "request_id": request_id,
                            "elapsed_ms": round((loop.time() - started) * 1000, 2),
                        },
                    )
                yield update
                if isinstance(update, ChatResponse):
                    return
    except (TimeoutError, httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        logger.warning(
            "chat_dsh_failed turn_id=%s reason=%s agent_model_calls=%d tool_calls=%d",
            turn.id,
            type(error).__name__,
            model_calls,
            tool_calls,
            extra={"request_id": request_id},
        )
        raise chat_unavailable() from error


async def _run_turn(
    client: httpx.AsyncClient,
    request: ChatRequest,
    session: AsyncSession,
    pipelines: PipelineRegistry,
    settings: ChatSettings,
    turn: StartedTurn,
    request_id: str,
) -> AsyncIterator[ChatUpdate]:
    secret = settings.chat_dsh_token
    if secret is None or not secret.get_secret_value():
        raise chat_unavailable()
    base_url = str(settings.chat_dsh_url).rstrip("/")
    headers = {"Authorization": f"Bearer {secret.get_secret_value()}"}
    context = ChatContext()
    evidence = TurnEvidence()
    history = history_hints(turn.history)
    history_ids = {hint["turn_id"] for hint in history}
    approved = {item.id: item for item in turn.history if str(item.id) in history_ids}
    tools = ChatTools(request, session, pipelines, approved)

    async with aclosing(
        _frames(
            client,
            f"{base_url}/turns",
            headers,
            {
                "session_id": str(turn.session_id),
                "turn_id": str(turn.id),
                "question": request.question,
                "contract": {
                    "system_prompt": (
                        f"{SYSTEM_PROMPT}\n本轮最多查询 {MAX_TOOL_EXECUTIONS} 次。"
                        "工具结果的 remaining_tool_calls 表示剩余查询次数。"
                    ),
                    "tools": TOOL_DEFINITIONS,
                    "max_model_calls": MAX_MODEL_CALLS,
                },
                "history": history,
            },
        )
    ) as frames:
        for_tools = False
        pending_markup = ""
        async for frame in frames:
            event = frame["type"]
            if event == "tool":
                name = frame["name"]
                if name not in PUBLIC_TOOLS:
                    raise ValueError("Unexpected DSH tool")
                if not for_tools:
                    yield ChatProgress(stage="retrieving")
                    for_tools = True
                args = parse_tool_arguments(name, frame["arguments"])
                step = ChatToolStep(
                    id=frame["id"],
                    tool=cast(ChatToolName, name),
                    state="running",
                    target=tool_target(args),
                )
                yield step
                result = await tools.execute(name, args, context)
                receipt = await client.post(
                    f"{base_url}/turns/{turn.id}/tools/{quote(step.id, safe='')}",
                    headers=headers,
                    json={
                        **evidence.record(result),
                        "remaining_tool_calls": tools.remaining_calls,
                    },
                )
                receipt.raise_for_status()
                yield step.model_copy(
                    update={
                        "state": "failed" if result.get("error") else "completed",
                        "summary": tool_summary(name, result),
                    }
                )
            elif event == "start":
                for_tools = False
                pending_markup = ""
                if "attempt" in frame:
                    yield ChatModelStep(attempt=frame["attempt"], state="running")
                yield ChatProgress(stage="generating")
            elif event == "end":
                yield ChatModelStep(attempt=frame["attempt"], state=frame["state"])
            elif event == "text":
                text, pending_markup = split_stream_text(pending_markup + frame["text"])
                if text:
                    yield ChatAnswerDelta(text=text)
            elif event == "done":
                paragraphs, response = await deliver(
                    frame["text"],
                    request,
                    context,
                    evidence,
                    session,
                )
                for paragraph in paragraphs:
                    yield paragraph
                yield response
                return
            else:
                raise ValueError("DSH turn did not complete")
    raise chat_unavailable()

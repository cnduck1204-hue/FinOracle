import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import dsh
from app.agent.citations import get_reference, get_version_reference
from app.api.dependencies import require_current_user
from app.core.config import ChatSettings
from app.core.exceptions import AppException, get_request_id
from app.db.session import get_db
from app.models.user import User
from app.rag.pipelines import PipelineRegistry, get_pipeline_registry
from app.schemas.chat import (
    ChatAnswerDelta,
    ChatExecutionStep,
    ChatModelStep,
    ChatParagraph,
    ChatProgress,
    ChatReference,
    ChatRequest,
    ChatResponse,
    ChatSessionCreated,
    ChatSessionHistory,
    ChatSessionList,
    ChatSessionSummary,
    ChatSessionUpdate,
    ChatToolStep,
    ChatTurnEnd,
    ChatVersionReference,
)
from app.services import chat_sessions

router = APIRouter(dependencies=[Depends(require_current_user)])
logger = logging.getLogger(__name__)


@dataclass
class PreparedStream:
    payload: ChatRequest
    turn: chat_sessions.StartedTurn
    paragraphs: list[ChatParagraph] = field(default_factory=list)
    execution: dict[int | str, ChatExecutionStep] = field(default_factory=dict)
    finished: bool = False
    end_state: ChatTurnEnd = "interrupted"
    revision: int = 0

    def frame(self, event: str, data: object) -> ServerSentEvent:
        self.revision += 1
        return ServerSentEvent(event=event, data=data, id=str(self.revision))

    def partial_result(self) -> ChatResponse | None:
        if not self.paragraphs:
            return None
        return ChatResponse(
            status="partial",
            answer="\n\n".join(paragraph.text for paragraph in self.paragraphs),
            citations=[
                citation
                for paragraph in self.paragraphs
                for citation in paragraph.citations
            ],
        )

    def settled_steps(self) -> list[ChatExecutionStep]:
        return [
            step.model_copy(update={"state": "interrupted"})
            if step.state == "running"
            else step
            for step in self.execution.values()
        ]


async def finish_incomplete(prepared: PreparedStream, session: AsyncSession) -> None:
    if prepared.finished:
        return
    # The producer can be cancelled midway through a read on the request session.
    with anyio.move_on_after(4, shield=True):
        try:
            async with AsyncSession(bind=session.bind) as cleanup:
                await chat_sessions.finish_turn(
                    cleanup,
                    prepared.turn,
                    prepared.end_state,
                    prepared.partial_result(),
                    execution=prepared.settled_steps(),
                )
                prepared.finished = True
        except SQLAlchemyError:
            logger.warning("chat_turn_terminal_write_failed")


async def prepare_stream(
    payload: ChatRequest,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AsyncIterator[PreparedStream]:
    prepared = None
    try:
        with anyio.CancelScope(shield=True):
            turn = await chat_sessions.start_turn(
                session,
                user.id,
                payload.session_id,
                payload.question,
                request_id=payload.request_id,
                document_ids=payload.document_ids,
            )
            prepared = PreparedStream(payload, turn, finished=turn.replay is not None)
        yield prepared
    finally:
        if prepared is not None:
            await finish_incomplete(prepared, session)


def stream_cache_control(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, no-transform"


@router.post(
    "/stream",
    response_class=EventSourceResponse,
    dependencies=[Depends(stream_cache_control)],
    summary="流式生成制度问答",
)
async def ask_stream(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
    pipelines: Annotated[PipelineRegistry, Depends(get_pipeline_registry)],
    prepared: Annotated[PreparedStream, Depends(prepare_stream)],
) -> AsyncIterator[ServerSentEvent]:
    request_id = get_request_id(request)
    turn = prepared.turn
    try:
        yield prepared.frame(
            "turn", {"session_id": str(turn.session_id), "turn_id": str(turn.id)}
        )
        if turn.replay is not None:
            if turn.replay.state == "completed" and turn.replay.result is not None:
                yield prepared.frame(
                    "done",
                    {
                        **turn.replay.result.model_dump(mode="json"),
                        "turn_id": str(turn.id),
                    },
                )
            else:
                yield prepared.frame(
                    "error",
                    {
                        "code": "CHAT_TURN_ALREADY_SETTLED",
                        "message": "本轮已结束，可在会话里查看结果",
                        "request_id": request_id,
                    },
                )
            return
        updates = dsh.run_updates(
            prepared.payload, session, pipelines, ChatSettings(), request_id, turn
        )
        async with aclosing(updates):
            async for update in updates:
                data = update
                if isinstance(update, ChatProgress):
                    event = "progress"
                elif isinstance(update, ChatModelStep):
                    event = "model"
                    prepared.execution[update.attempt] = update
                elif isinstance(update, ChatToolStep):
                    event = "tool"
                    prepared.execution[update.id] = update
                elif isinstance(update, ChatAnswerDelta):
                    event = "answer_delta"
                elif isinstance(update, ChatParagraph):
                    event = "paragraph"
                    prepared.paragraphs.append(update)
                    result = prepared.partial_result()
                    assert result is not None
                    if not await chat_sessions.checkpoint_turn(
                        session, turn, result, tuple(prepared.execution.values())
                    ):
                        raise AppException(
                            code="CHAT_TURN_EXPIRED",
                            message="当前轮次已结束，请重新提问",
                            status_code=409,
                        )
                else:
                    event = "done"
                    prepared.finished = await chat_sessions.finish_turn(
                        session,
                        turn,
                        "completed",
                        update,
                        execution=prepared.settled_steps(),
                    )
                    if not prepared.finished:
                        raise AppException(
                            code="CHAT_TURN_EXPIRED",
                            message="当前轮次已结束，请重新提问",
                            status_code=409,
                        )
                    data = {**update.model_dump(mode="json"), "turn_id": str(turn.id)}
                yield prepared.frame(event, data)
    except Exception as error:
        if isinstance(error, AppException):
            report = error
        else:
            logger.error(
                "chat_stream_failed",
                extra={"request_id": request_id, "error_type": type(error).__name__},
            )
            report = dsh.chat_unavailable()
        prepared.end_state = "failed"
        await finish_incomplete(prepared, session)
        yield prepared.frame(
            "error",
            {
                "code": report.code,
                "message": report.message,
                "request_id": request_id,
            },
        )


@router.post("/sessions", status_code=201, summary="创建本人问答会话")
async def create_chat_session(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionCreated:
    response.headers["Cache-Control"] = "no-store"
    return await chat_sessions.create(session, user.id)


@router.get("/sessions/{session_id}", summary="读取本人问答会话")
async def read_chat_session(
    session_id: UUID,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before_seq: Annotated[int | None, Query(ge=0)] = None,
    through_seq: Annotated[int | None, Query(ge=-1)] = None,
) -> ChatSessionHistory:
    response.headers["Cache-Control"] = "no-store"
    return await chat_sessions.read(
        session,
        user.id,
        session_id,
        limit=limit,
        before_seq=before_seq,
        through_seq=through_seq,
    )


@router.get("/sessions", summary="列出或搜索本人问答会话")
async def list_chat_sessions(
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    archived: bool = False,
    q: Annotated[str, Query(max_length=100)] = "",
    cursor: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    pinned: bool | None = None,
) -> ChatSessionList:
    response.headers["Cache-Control"] = "no-store"
    return await chat_sessions.list_sessions(
        session,
        user.id,
        archived=archived,
        query=q.strip(),
        cursor=cursor,
        limit=limit,
        pinned=pinned,
    )


@router.patch("/sessions/{session_id}", summary="重命名、置顶或归档本人问答会话")
async def update_chat_session(
    session_id: UUID,
    payload: ChatSessionUpdate,
    response: Response,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChatSessionSummary:
    response.headers["Cache-Control"] = "no-store"
    return await chat_sessions.modify(session, user.id, session_id, payload)


@router.delete("/sessions/{session_id}", status_code=204, summary="删除本人问答会话")
async def delete_chat_session(
    session_id: UUID,
    user: Annotated[User, Depends(require_current_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    await chat_sessions.delete(session, user.id, session_id)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/references/chunk/{chunk_id}", summary="读取问答引用的条文")
async def reference(
    chunk_id: Annotated[int, Path(gt=0)],
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChatReference:
    response.headers["Cache-Control"] = "no-store"
    return await get_reference(chunk_id, session)


@router.get("/references/version/{version_id}", summary="读取问答引用的版本时效")
async def version_reference(
    version_id: UUID,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> ChatVersionReference:
    response.headers["Cache-Control"] = "no-store"
    return await get_version_reference(version_id, session)

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4, uuid5

from pydantic import TypeAdapter
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.base import utc_now
from app.models.chat_session import ChatSession, ChatSessionEvent
from app.schemas.chat import (
    ChatExecutionStep,
    ChatResponse,
    ChatSessionCreated,
    ChatSessionHistory,
    ChatSessionList,
    ChatSessionSummary,
    ChatSessionUpdate,
    ChatTurn,
    ChatTurnEnd,
)

# A crashed request expires without holding a database connection during generation.
_TURN_LEASE = timedelta(seconds=60)
_CONTEXT_TURNS = 12
_execution_steps = TypeAdapter(list[ChatExecutionStep])


@dataclass(frozen=True)
class StartedTurn:
    session_id: UUID
    id: UUID
    history: list[ChatTurn]
    replay: ChatTurn | None = None


def _not_found() -> AppException:
    return AppException(
        code="CHAT_SESSION_NOT_FOUND", message="找不到此会话", status_code=404
    )


def _busy() -> AppException:
    return AppException(
        code="CHAT_SESSION_BUSY", message="这段会话还有一轮正在生成", status_code=409
    )


def _owned(user_id: UUID):
    return select(ChatSession).where(ChatSession.user_id == user_id)


def _summary(conversation: ChatSession) -> ChatSessionSummary:
    return ChatSessionSummary(
        id=conversation.id,
        title=conversation.title or "新会话",
        archived=conversation.archived_at is not None,
        pinned=conversation.pinned,
        updated_at=conversation.updated_at.replace(tzinfo=UTC),
    )


async def create(session: AsyncSession, user_id: UUID) -> ChatSessionCreated:
    conversation = ChatSession(user_id=user_id)
    async with session.begin():
        session.add(conversation)
        await session.flush()
    return ChatSessionCreated(id=conversation.id)


async def list_sessions(
    session: AsyncSession,
    user_id: UUID,
    *,
    archived: bool,
    query: str,
    cursor: str | None,
    limit: int,
    pinned: bool | None = None,
) -> ChatSessionList:
    statement = _owned(user_id).where(
        ChatSession.archived_at.is_not(None)
        if archived
        else ChatSession.archived_at.is_(None)
    )
    if pinned is not None:
        statement = statement.where(ChatSession.pinned == pinned)
    if cursor is not None:
        try:
            pinned_text, time_text, id_text = cursor.split(",", 2)
            timestamp = datetime.fromisoformat(time_text)
            if timestamp.tzinfo is not None or pinned_text not in {"0", "1"}:
                raise ValueError
            cursor_pinned = pinned_text == "1"
            cursor_id = UUID(id_text)
        except ValueError as error:
            raise AppException(
                code="CHAT_INVALID_CURSOR", message="会话分页位置无效", status_code=422
            ) from error
        after_cursor = and_(
            ChatSession.pinned == cursor_pinned,
            or_(
                ChatSession.updated_at < timestamp,
                and_(ChatSession.updated_at == timestamp, ChatSession.id < cursor_id),
            ),
        )
        statement = statement.where(
            or_(ChatSession.pinned.is_(False), after_cursor)
            if cursor_pinned
            else after_cursor
        )
    if query:
        payload = ChatSessionEvent.payload
        matches = (
            select(ChatSessionEvent.seq)
            .where(
                ChatSessionEvent.session_id == ChatSession.id,
                or_(
                    payload["question"].as_string().contains(query, autoescape=True),
                    payload["result"]["answer"]
                    .as_string()
                    .contains(query, autoescape=True),
                ),
            )
            .exists()
        )
        statement = statement.where(
            or_(ChatSession.title.contains(query, autoescape=True), matches)
        )
    async with session.begin():
        records = (
            await session.scalars(
                statement.order_by(
                    ChatSession.pinned.desc(),
                    ChatSession.updated_at.desc(),
                    ChatSession.id.desc(),
                ).limit(limit + 1)
            )
        ).all()
    items = [_summary(record) for record in records[:limit]]
    if len(records) <= limit:
        return ChatSessionList(items=items)
    last = records[limit - 1]
    return ChatSessionList(
        items=items,
        next_cursor=f"{int(last.pinned)},{last.updated_at.isoformat()},{last.id}",
    )


async def _lock_owned(
    session: AsyncSession, user_id: UUID, session_id: UUID
) -> ChatSession:
    conversation = await session.scalar(
        _owned(user_id).where(ChatSession.id == session_id).with_for_update()
    )
    if conversation is None:
        raise _not_found()
    return conversation


def _require_idle(conversation: ChatSession, now: datetime) -> None:
    if conversation.active_until and conversation.active_until > now:
        raise _busy()


async def modify(
    session: AsyncSession, user_id: UUID, session_id: UUID, changes: ChatSessionUpdate
) -> ChatSessionSummary:
    async with session.begin():
        conversation = await _lock_owned(session, user_id, session_id)
        now = utc_now()
        if changes.archived:
            _require_idle(conversation, now)
        if changes.title is not None:
            conversation.title = changes.title
        if changes.archived is not None:
            conversation.archived_at = (
                (conversation.archived_at or now) if changes.archived else None
            )
        if changes.pinned is not None:
            conversation.pinned = changes.pinned
        if changes.title is not None or changes.archived is not None:
            conversation.updated_at = now
        return _summary(conversation)


async def delete(session: AsyncSession, user_id: UUID, session_id: UUID) -> None:
    async with session.begin():
        conversation = await _lock_owned(session, user_id, session_id)
        _require_idle(conversation, utc_now())
        await session.delete(conversation)


def _events(session_id: UUID):
    return select(ChatSessionEvent).where(ChatSessionEvent.session_id == session_id)


def _project(
    events: list[ChatSessionEvent], active_turn: UUID | None = None
) -> list[ChatTurn]:
    turns: dict[UUID, ChatTurn] = {}
    for event in events:
        if event.kind == "question":
            turns[event.turn_id] = ChatTurn(
                id=event.turn_id,
                question=event.payload["question"],
                state="running" if event.turn_id == active_turn else "interrupted",
            )
        else:
            turn = turns[event.turn_id]
            if event.kind != "paragraph":
                turn.state = cast(ChatTurnEnd, event.kind)
            payload = event.payload.get("result")
            turn.result = (
                ChatResponse.model_validate(payload) if payload is not None else None
            )
            turn.execution = _execution_steps.validate_python(
                event.payload.get("execution", event.payload.get("tool_steps", []))
            )
    return list(turns.values())


async def read(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    *,
    limit: int = 20,
    before_seq: int | None = None,
    through_seq: int | None = None,
) -> ChatSessionHistory:
    async with session.begin():
        conversation = await session.scalar(
            _owned(user_id).where(ChatSession.id == session_id)
        )
        if conversation is None:
            raise _not_found()
        tail = conversation.next_seq - 1
        cut = tail if through_seq is None else min(through_seq, tail)
        first_excluded = min(cut + 1, before_seq) if before_seq is not None else cut + 1
        questions = (
            await session.scalars(
                select(ChatSessionEvent.seq)
                .where(
                    ChatSessionEvent.session_id == session_id,
                    ChatSessionEvent.kind == "question",
                    ChatSessionEvent.seq < first_excluded,
                )
                .order_by(ChatSessionEvent.seq.desc())
                .limit(limit + 1)
            )
        ).all()
        oldest = (
            questions[min(limit, len(questions)) - 1] if questions else first_excluded
        )
        events = (
            await session.scalars(
                _events(session_id)
                .where(
                    ChatSessionEvent.seq >= oldest,
                    ChatSessionEvent.seq < first_excluded,
                )
                .order_by(ChatSessionEvent.seq)
            )
        ).all()
        active_turn = (
            conversation.active_turn_id
            if conversation.active_until and conversation.active_until > utc_now()
            else None
        )
        return ChatSessionHistory(
            **_summary(conversation).model_dump(),
            turns=_project(list(events), active_turn),
            through_seq=cut,
            next_before_seq=oldest if len(questions) > limit else None,
        )


def _append(
    session: AsyncSession,
    conversation: ChatSession,
    turn_id: UUID,
    kind: str,
    payload: dict,
) -> None:
    session.add(
        ChatSessionEvent(
            session_id=conversation.id,
            seq=conversation.next_seq,
            turn_id=turn_id,
            kind=kind,
            payload=payload,
        )
    )
    conversation.next_seq += 1
    conversation.updated_at = utc_now()


async def _recent_turns(session: AsyncSession, session_id: UUID) -> list[ChatTurn]:
    completed = list(
        await session.scalars(
            _events(session_id)
            .where(ChatSessionEvent.kind == "completed")
            .order_by(ChatSessionEvent.seq.desc())
            .limit(_CONTEXT_TURNS)
        )
    )
    if not completed:
        return []
    questions = list(
        await session.scalars(
            _events(session_id)
            .where(
                ChatSessionEvent.kind == "question",
                ChatSessionEvent.turn_id.in_([event.turn_id for event in completed]),
            )
            .order_by(ChatSessionEvent.seq)
        )
    )
    return _project(questions + completed)


async def start_turn(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID | None,
    question: str,
    *,
    request_id: UUID | None = None,
    document_ids: Sequence[UUID] | None = None,
) -> StartedTurn:
    new_session = session_id is None
    session_id = session_id or uuid4()
    turn_id = uuid5(session_id, request_id.hex) if request_id else uuid4()
    scope = {
        "documents": sorted(str(item) for item in document_ids)
        if document_ids
        else None,
    }
    now = utc_now()
    ownership = (ChatSession.id == session_id, ChatSession.user_id == user_id)
    async with session.begin():
        if new_session:
            session.add(ChatSession(id=session_id, user_id=user_id))
            await session.flush()
        claimed = await session.execute(
            update(ChatSession)
            .where(
                *ownership,
                ChatSession.archived_at.is_(None),
                or_(
                    ChatSession.active_turn_id.is_(None),
                    ChatSession.active_until <= now,
                ),
            )
            .values(active_turn_id=turn_id, active_until=now + _TURN_LEASE)
        )
        if claimed.rowcount != 1:
            found = await session.scalar(select(ChatSession).where(*ownership))
            if found is None:
                raise _not_found()
            if found.archived_at is not None:
                raise AppException(
                    code="CHAT_SESSION_ARCHIVED",
                    message="恢复会话后可继续提问",
                    status_code=409,
                )
            raise _busy()
        conversation = await session.get(
            ChatSession, session_id, populate_existing=True
        )
        assert conversation is not None
        last = await session.get(
            ChatSessionEvent, (session_id, conversation.next_seq - 1)
        )
        if last is not None and last.kind in {"question", "paragraph"}:
            _append(session, conversation, last.turn_id, "interrupted", last.payload)
            await session.flush()
        if request_id is not None:
            previous = (
                await session.scalars(
                    _events(session_id)
                    .where(ChatSessionEvent.turn_id == turn_id)
                    .order_by(ChatSessionEvent.seq)
                )
            ).all()
            if previous:
                submitted = previous[0].payload
                if (
                    submitted["question"] != question
                    or submitted.get("scope", {}).get("documents") != scope["documents"]
                ):
                    raise AppException(
                        code="CHAT_REQUEST_CONFLICT",
                        message="此请求标识已用于其他提问",
                        status_code=409,
                    )
                conversation.active_turn_id = None
                conversation.active_until = None
                (replay,) = _project(list(previous))
                return StartedTurn(
                    session_id=session_id, id=turn_id, history=[], replay=replay
                )
        history = await _recent_turns(session, session_id)
        if conversation.title is None:
            conversation.title = question[:80]
        payload: dict = {"question": question}
        if request_id is not None:
            payload["scope"] = scope
        _append(session, conversation, turn_id, "question", payload)
    return StartedTurn(session_id=session_id, id=turn_id, history=history)


def _result_payload(
    result: ChatResponse | None, execution: Sequence[ChatExecutionStep]
) -> dict:
    return {
        "result": result.model_dump(mode="json") if result else None,
        "execution": [step.model_dump(mode="json") for step in execution],
    }


async def checkpoint_turn(
    session: AsyncSession,
    turn: StartedTurn,
    result: ChatResponse,
    execution: Sequence[ChatExecutionStep],
) -> bool:
    async with session.begin():
        conversation = await session.scalar(
            select(ChatSession)
            .where(
                ChatSession.id == turn.session_id, ChatSession.active_turn_id == turn.id
            )
            .with_for_update()
        )
        if conversation is None:
            return False
        _append(
            session,
            conversation,
            turn.id,
            "paragraph",
            _result_payload(result, execution),
        )
    return True


async def finish_turn(
    session: AsyncSession,
    turn: StartedTurn,
    state: ChatTurnEnd,
    result: ChatResponse | None,
    *,
    execution: Sequence[ChatExecutionStep] = (),
) -> bool:
    async with session.begin():
        released = await session.execute(
            update(ChatSession)
            .where(
                ChatSession.id == turn.session_id, ChatSession.active_turn_id == turn.id
            )
            .values(active_turn_id=None, active_until=None)
        )
        if released.rowcount != 1:
            return False
        conversation = await session.get(
            ChatSession, turn.session_id, populate_existing=True
        )
        assert conversation is not None
        _append(
            session,
            conversation,
            turn.id,
            state,
            _result_payload(result, execution),
        )
    return True

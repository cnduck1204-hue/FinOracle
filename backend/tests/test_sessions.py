import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.cache import sessions
from app.schemas.session import SessionRecord


@pytest.fixture
def session_context(monkeypatch):
    payload = {
        "id": str(uuid4()),
        "user_id": str(uuid4()),
        "refresh_jti": str(uuid4()),
        "created_at": 100,
        "expires_at": 200,
        "revoked": False,
    }
    cache = SimpleNamespace(
        set=AsyncMock(return_value=True),
        get=AsyncMock(return_value=json.dumps(payload)),
        eval=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(sessions, "time", lambda: 150)
    return SimpleNamespace(
        payload=payload, cache=cache, store=sessions.SessionStore(cache)
    )


def test_session_record_uses_the_expiry_contract_and_round_trips_json(session_context):
    record = SessionRecord.model_validate(session_context.payload)

    assert record.expires_at == 200
    assert record.expires_at > record.created_at
    assert record.revoked is False
    assert SessionRecord.model_validate_json(record.model_dump_json()) == record
    assert "expired_at" not in record.model_dump()


@pytest.mark.parametrize("expires_at", [99, 100])
def test_session_expiry_must_be_strictly_later_than_creation(
    session_context, expires_at
):
    with pytest.raises(ValidationError) as caught:
        SessionRecord.model_validate(
            {**session_context.payload, "expires_at": expires_at}
        )

    assert any(
        error["type"] == "value_error" and error["loc"] == ()
        for error in caught.value.errors(include_input=False)
    ), "The lifetime validator must reject earlier and equal expiry times"


@pytest.mark.parametrize("field", ["created_at", "expires_at"])
@pytest.mark.parametrize("value", [True, "150", 150.5])
def test_session_timestamps_require_real_integers(session_context, field, value):
    with pytest.raises(ValidationError) as caught:
        SessionRecord.model_validate({**session_context.payload, field: value})

    assert any(
        error["type"] == "int_type" and error["loc"] == (field,)
        for error in caught.value.errors(include_input=False)
    )


def test_session_revocation_flag_requires_a_boolean(session_context):
    with pytest.raises(ValidationError) as caught:
        SessionRecord.model_validate({**session_context.payload, "revoked": "false"})

    assert any(
        error["type"] == "bool_type" and error["loc"] == ("revoked",)
        for error in caught.value.errors(include_input=False)
    )


def test_create_preserves_expiry_and_does_not_overwrite_existing_sessions(
    session_context,
):
    context = session_context
    record = SessionRecord.model_validate(context.payload)

    assert asyncio.run(context.store.create(record)) is None
    context.cache.eval.assert_awaited_once()
    call = context.cache.eval.await_args
    assert call.args[:4] == (
        sessions._CREATE_SCRIPT,
        2,
        f"finoracle:v2:auth:session:{record.id}",
        f"finoracle:v2:auth:user-sessions:{record.user_id}",
    )
    assert json.loads(call.args[4]) == context.payload
    assert call.args[5:] == (200, str(record.id))


def test_duplicate_session_id_is_not_silently_accepted(session_context):
    context = session_context
    record = SessionRecord.model_validate(context.payload)
    context.cache.eval.return_value = 0

    with pytest.raises(ValueError, match="会话 ID 已存在"):
        asyncio.run(context.store.create(record))


def test_expired_session_is_rejected_before_redis_write(session_context):
    context = session_context
    record = SessionRecord.model_validate({**context.payload, "expires_at": 150})

    with pytest.raises(ValueError, match="不能创建已过期的会话"):
        asyncio.run(context.store.create(record))

    context.cache.set.assert_not_awaited()


@pytest.mark.parametrize("revoked", [False, True])
def test_get_returns_the_complete_record_including_revocation_state(
    session_context, revoked
):
    context = session_context
    context.cache.get.return_value = json.dumps({**context.payload, "revoked": revoked})
    session_id = UUID(context.payload["id"])

    record = asyncio.run(context.store.get(session_id))

    assert isinstance(record, SessionRecord), (
        "get must return the parsed session record"
    )
    assert record.id == session_id
    assert record.expires_at == 200
    assert record.revoked is revoked
    context.cache.get.assert_awaited_once_with(
        f"finoracle:v2:auth:session:{session_id}"
    )


def test_missing_session_returns_none(session_context):
    context = session_context
    context.cache.get.return_value = None

    assert asyncio.run(context.store.get(uuid4())) is None


def test_stale_session_record_is_not_returned_after_expiry(session_context):
    context = session_context
    context.cache.get.return_value = json.dumps({**context.payload, "expires_at": 150})
    session_id = UUID(context.payload["id"])

    assert asyncio.run(context.store.get(session_id)) is None


def test_get_rejects_a_record_belonging_to_a_different_session(session_context):
    with pytest.raises(ValueError, match="会话数据与 ID 不匹配"):
        asyncio.run(session_context.store.get(uuid4()))


@pytest.mark.parametrize(("redis_result", "expected"), [(0, False), (1, True)])
def test_revoke_waits_for_redis_and_returns_whether_a_session_exists(
    session_context, redis_result, expected
):
    context = session_context
    context.cache.eval.return_value = redis_result

    assert asyncio.run(context.store.revoke(uuid4())) is expected
    context.cache.eval.assert_awaited_once()


@pytest.mark.parametrize("operation", ["get", "revoke"])
def test_session_read_and_revoke_propagate_redis_failures(session_context, operation):
    context = session_context
    error = RedisError("Redis unavailable")
    context.cache.get.side_effect = error
    context.cache.eval.side_effect = error

    with pytest.raises(RedisError) as caught:
        asyncio.run(getattr(context.store, operation)(uuid4()))

    assert caught.value is error


def rotation_arguments(context, **changes):
    return {
        "session_id": UUID(context.payload["id"]),
        "user_id": UUID(context.payload["user_id"]),
        "expected_refresh_jti": UUID(context.payload["refresh_jti"]),
        "new_refresh_jti": uuid4(),
        "expires_at": 300,
        **changes,
    }


@pytest.mark.parametrize(
    ("redis_result", "expected"),
    [(1, "rotated"), (0, "invalid"), (-1, "replayed")],
)
def test_rotate_uses_one_script_with_bound_identity_and_absolute_expiry(
    session_context, redis_result, expected
):
    context = session_context
    context.cache.eval.return_value = redis_result
    arguments = rotation_arguments(context)

    result = asyncio.run(context.store.rotate(**arguments))

    assert result == expected
    context.cache.eval.assert_awaited_once_with(
        sessions._ROTATE_SCRIPT,
        2,
        f"finoracle:v2:auth:session:{arguments['session_id']}",
        f"finoracle:v2:auth:user-sessions:{arguments['user_id']}",
        str(arguments["session_id"]),
        str(arguments["user_id"]),
        str(arguments["expected_refresh_jti"]),
        str(arguments["new_refresh_jti"]),
        300,
    )
    context.cache.get.assert_not_awaited()
    context.cache.set.assert_not_awaited()


@pytest.mark.parametrize("expires_at", [149, 150])
def test_rotate_rejects_expired_replacement_before_redis(session_context, expires_at):
    context = session_context
    with pytest.raises(ValueError, match="过期时间必须晚于当前时间"):
        asyncio.run(
            context.store.rotate(**rotation_arguments(context, expires_at=expires_at))
        )
    context.cache.eval.assert_not_awaited()


def test_rotate_requires_a_fresh_token_identifier(session_context):
    context = session_context
    old_jti = UUID(context.payload["refresh_jti"])
    with pytest.raises(ValueError, match="必须使用新的 jti"):
        asyncio.run(
            context.store.rotate(**rotation_arguments(context, new_refresh_jti=old_jti))
        )
    context.cache.eval.assert_not_awaited()


def test_rotate_does_not_treat_unknown_script_result_as_success(session_context):
    context = session_context
    context.cache.eval.return_value = 2
    with pytest.raises(ValueError, match="未知的会话轮换结果"):
        asyncio.run(context.store.rotate(**rotation_arguments(context)))


def test_rotate_preserves_redis_failure_for_service_layer(session_context):
    context = session_context
    error = RedisError("Redis unavailable")
    context.cache.eval.side_effect = error
    with pytest.raises(RedisError) as caught:
        asyncio.run(context.store.rotate(**rotation_arguments(context)))
    assert caught.value is error

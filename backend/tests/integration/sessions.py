"""Run explicitly with `uv run python -m tests.integration.sessions` from backend/."""

import asyncio
import sys
from time import time
from uuid import uuid4

from redis.exceptions import RedisError

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.schemas.session import SessionRecord


async def check_sessions():
    store = SessionStore(redis_client)
    session_id, missing_id, race_id, expired_id = [uuid4() for _ in range(4)]
    keys = [
        f"finoracle:v2:auth:session:{identifier}"
        for identifier in [session_id, missing_id, race_id, expired_id]
    ]
    now = int(time())
    record = SessionRecord(
        id=session_id,
        user_id=uuid4(),
        refresh_jti=uuid4(),
        created_at=now,
        expires_at=now + 30,
    )

    try:
        assert await store.create(record) is None
        assert await store.get(session_id) == record
        assert 0 < await redis_client.ttl(keys[0]) <= 30
        assert await redis_client.expiretime(keys[0]) == record.expires_at
        print("PASS: Session round-trip and exact absolute expiry")

        replacement = record.model_copy(update={"refresh_jti": uuid4()})
        try:
            await store.create(replacement)
        except ValueError:
            pass
        else:
            raise AssertionError("Duplicate creation overwrote an existing session")
        assert await store.get(session_id) == record
        print("PASS: Duplicate creation preserves the original session")

        before = await redis_client.pttl(keys[0])
        assert await store.revoke(session_id) is True
        revoked = await store.get(session_id)
        assert revoked == record.model_copy(update={"revoked": True})
        assert 0 < await redis_client.pttl(keys[0]) <= before
        assert await store.revoke(session_id) is True
        assert await store.revoke(missing_id) is False
        assert await store.get(missing_id) is None
        assert not await redis_client.exists(keys[1])
        print(
            "PASS: Revocation preserves record fields and TTL; missing sessions stay absent"
        )

        contenders = [
            record.model_copy(update={"id": race_id, "refresh_jti": uuid4()})
            for _ in range(12)
        ]
        outcomes = await asyncio.gather(
            *(store.create(candidate) for candidate in contenders),
            return_exceptions=True,
        )
        assert sum(outcome is None for outcome in outcomes) == 1
        assert sum(isinstance(outcome, ValueError) for outcome in outcomes) == 11
        winner = contenders[
            next(i for i, outcome in enumerate(outcomes) if outcome is None)
        ]
        assert await store.get(race_id) == winner
        print("PASS: Concurrent creation commits exactly one complete session")

        await redis_client.expire(keys[0], 1)
        deadline = asyncio.get_running_loop().time() + 5
        while await redis_client.exists(keys[0]):
            assert asyncio.get_running_loop().time() < deadline, (
                "Session did not expire"
            )
            await asyncio.sleep(0.05)
        assert await store.get(session_id) is None
        assert await store.revoke(session_id) is False
        expired = record.model_copy(
            update={"id": expired_id, "created_at": now - 10, "expires_at": now - 1}
        )
        try:
            await store.create(expired)
        except ValueError:
            pass
        else:
            raise AssertionError("Expired session creation was accepted")
        assert not await redis_client.exists(keys[3])
        print("PASS: Expired sessions cannot be read or recreated")
    finally:
        try:
            await redis_client.delete(*keys, store._user_key(record.user_id))
            assert await redis_client.exists(*keys) == 0
        finally:
            await redis_client.aclose()
        print("PASS: Only this run's temporary session keys were deleted")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(check_sessions(), timeout=20))
    except (AssertionError, RedisError, ValueError, TimeoutError) as error:
        print(f"FAIL: {type(error).__name__}")
        sys.exit(1)

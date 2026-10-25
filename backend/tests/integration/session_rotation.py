"""Run explicitly with `uv run python -m tests.integration.session_rotation` from backend/."""

import asyncio
import sys
from time import time
from uuid import uuid4

from redis.exceptions import RedisError

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.schemas.session import SessionRecord


async def check_rotation():
    store = SessionStore(redis_client)
    now = int(time())
    keys = []
    index_keys = set()

    async def create_record(**changes):
        record = SessionRecord(
            **{
                "id": uuid4(),
                "user_id": uuid4(),
                "refresh_jti": uuid4(),
                "created_at": now,
                "expires_at": now + 60,
                **changes,
            }
        )
        keys.append(f"finoracle:v2:auth:session:{record.id}")
        index_keys.add(store._user_key(record.user_id))
        await store.create(record)
        return record

    async def rotate(record, **changes):
        return await store.rotate(
            **{
                "session_id": record.id,
                "user_id": record.user_id,
                "expected_refresh_jti": record.refresh_jti,
                "new_refresh_jti": uuid4(),
                "expires_at": now + 120,
                **changes,
            }
        )

    try:
        original = await create_record()
        other_session = await create_record(user_id=original.user_id)
        new_jti = uuid4()
        assert await rotate(original, new_refresh_jti=new_jti) == "rotated"
        current = await store.get(original.id)
        assert current == original.model_copy(
            update={
                "refresh_jti": new_jti,
                "expires_at": now + 120,
            }
        )
        assert await redis_client.expiretime(keys[0]) == now + 120
        second_jti = uuid4()
        assert (
            await rotate(current, new_refresh_jti=second_jti, expires_at=now + 180)
            == "rotated"
        )
        latest = await store.get(original.id)
        assert latest.created_at == original.created_at
        assert latest.refresh_jti == second_jti
        assert latest.expires_at == now + 180
        print(
            "PASS: Consecutive rotations update the refresh JTI and exact expiry while preserving session identity and creation time"
        )

        assert await rotate(original) == "replayed"
        revoked = await store.get(original.id)
        assert revoked == latest.model_copy(update={"revoked": True})
        assert await redis_client.expiretime(keys[0]) == now + 180
        assert await rotate(latest) == "invalid"
        assert await store.get(original.id) == revoked
        assert await store.get(other_session.id) == other_session
        print(
            "PASS: Replaying an old JTI revokes only its session, preserves TTL and cannot resurrect the session"
        )

        missing = original.model_copy(update={"id": uuid4()})
        missing_key = f"finoracle:v2:auth:session:{missing.id}"
        keys.append(missing_key)
        assert await rotate(missing) == "invalid"
        assert await redis_client.exists(missing_key) == 0
        original_raw = await redis_client.get(keys[1])
        assert await rotate(other_session, user_id=uuid4()) == "invalid"
        assert await redis_client.get(keys[1]) == original_raw
        assert await redis_client.expiretime(keys[1]) == other_session.expires_at
        wrong_id = other_session.model_copy(update={"id": uuid4()})
        await redis_client.set(keys[1], wrong_id.model_dump_json(), keepttl=True)
        assert await rotate(other_session) == "invalid"
        stale = other_session.model_copy(
            update={"created_at": now - 60, "expires_at": now - 1}
        )
        await redis_client.set(keys[1], stale.model_dump_json(), keepttl=True)
        assert await rotate(other_session) == "invalid"
        assert await redis_client.get(keys[1]) == stale.model_dump_json()
        print(
            "PASS: Missing, expired and mismatched session records are rejected without creating or taking over a session"
        )

        race = await create_record()
        candidates = [uuid4() for _ in range(12)]
        outcomes = await asyncio.gather(
            *(rotate(race, new_refresh_jti=candidate) for candidate in candidates)
        )
        assert outcomes.count("rotated") == 1
        assert outcomes.count("replayed") == 1
        assert outcomes.count("invalid") == 10
        winner = candidates[outcomes.index("rotated")]
        saved = await store.get(race.id)
        assert saved.refresh_jti == winner and saved.revoked
        assert saved.created_at == race.created_at
        assert saved.expires_at == now + 120
        assert (
            await redis_client.expiretime(f"finoracle:v2:auth:session:{race.id}")
            == now + 120
        )
        print(
            "PASS: Twelve concurrent attempts with one old JTI produce exactly one rotation, then revoke the session"
        )

        concurrent = await create_record()
        outcomes = await asyncio.gather(rotate(concurrent), store.revoke(concurrent.id))
        assert outcomes[0] in {"rotated", "invalid"} and outcomes[1] is True
        assert (await store.get(concurrent.id)).revoked
        print(
            "PASS: Concurrent revocation and refresh always leave the session revoked"
        )

        corrupt = await create_record()
        corrupt_key = f"finoracle:v2:auth:session:{corrupt.id}"
        await redis_client.set(corrupt_key, "{", keepttl=True)
        try:
            await rotate(corrupt)
        except RedisError:
            pass
        else:
            raise AssertionError("Corrupt Redis data was accepted")
        assert await redis_client.get(corrupt_key) == "{"
        assert await redis_client.expiretime(corrupt_key) == corrupt.expires_at
        print(
            "PASS: Malformed stored JSON raises a storage error without a partial write"
        )
    finally:
        try:
            await redis_client.delete(*keys, *index_keys)
            assert await redis_client.exists(*keys) == 0
        finally:
            await redis_client.aclose()
        print("PASS: Only this run's temporary session records were deleted")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(check_rotation(), timeout=20))
    except (AssertionError, RedisError, ValueError, TimeoutError) as error:
        print(f"FAIL: {type(error).__name__}")
        sys.exit(1)

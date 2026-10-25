"""Run explicitly with `uv run python -m tests.integration.login_failures` from backend/."""

import asyncio
import sys
from uuid import uuid4

from redis.exceptions import RedisError

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client


async def check_login_failures():
    identifier = f"failure_{uuid4().hex}@example.com"
    other = f"other_{uuid4().hex}@example.com"
    store = LoginFailureStore(redis_client, max_failures=5, lock_seconds=30)
    key = store._key(identifier)
    other_key = store._key(other)
    results = []
    try:
        assert await store.retry_after(identifier) == 0
        for expected in range(1, 5):
            returned = await store.record_failure(identifier)
            results.append((returned, expected))
            assert int(await redis_client.get(key)) == expected
            assert 0 < await redis_client.ttl(key) <= 30
            assert await store.retry_after(identifier) == 0
        assert await store.retry_after(other) == 0
        print("PASS: Failures have expiry and different identifiers remain independent")

        await redis_client.expire(key, 5)
        results.append((await store.record_failure(identifier), 5))
        assert 29 <= await store.retry_after(identifier) <= 30
        await redis_client.expire(key, 10)
        results.append((await store.record_failure(identifier), 6))
        assert 0 < await store.retry_after(identifier) <= 10
        print("PASS: Fifth failure starts the lock; later failures do not extend it")

        await store.clear(identifier)
        assert not await redis_client.exists(key)
        assert await store.retry_after(identifier) == 0
        concurrent = await asyncio.gather(
            *(store.record_failure(identifier) for _ in range(12))
        )
        assert int(await redis_client.get(key)) == 12
        assert 0 < await store.retry_after(identifier) <= 30
        results.append(
            (
                sorted(concurrent)
                if all(isinstance(x, int) for x in concurrent)
                else concurrent,
                list(range(1, 13)),
            )
        )
        print("PASS: Concurrent failures are counted without losing updates")

        await redis_client.expire(key, 1)
        deadline = asyncio.get_running_loop().time() + 5
        while await redis_client.exists(key):
            assert asyncio.get_running_loop().time() < deadline, "Lock did not expire"
            await asyncio.sleep(0.05)
        assert await store.retry_after(identifier) == 0
        results.append((await store.record_failure(identifier), 1))
        assert int(await redis_client.get(key)) == 1
        await store.clear(identifier)
        print("PASS: Expired locks reset the counter and clearing removes the key")

        assert all(actual == expected for actual, expected in results), (
            "record_failure must return the actual failure count"
        )
        print("PASS: record_failure returns the actual failure count")
    finally:
        try:
            await redis_client.delete(key, other_key)
            assert await redis_client.exists(key, other_key) == 0
        finally:
            await redis_client.aclose()
        print("PASS: Only this run's temporary keys were deleted")


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(check_login_failures(), timeout=20))
    except (AssertionError, RedisError, TimeoutError) as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        sys.exit(1)

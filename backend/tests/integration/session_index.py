"""Real Redis checks: python -m tests.integration.session_index."""

import asyncio
from time import time
from uuid import uuid4

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.schemas.session import SessionRecord


async def check_index():
    store = SessionStore(redis_client)
    now = int(time())
    owner, other_owner = uuid4(), uuid4()
    records = []
    indexes = [
        f"finoracle:v2:auth:user-sessions:{user}" for user in [owner, other_owner]
    ]

    async def create(user_id=owner):
        record = SessionRecord(
            id=uuid4(),
            user_id=user_id,
            refresh_jti=uuid4(),
            created_at=now,
            expires_at=now + 60,
        )
        records.append(record)
        await store.create(record)
        return record

    try:
        first, second, unrelated = (
            await create(),
            await create(),
            await create(other_owner),
        )
        assert await redis_client.zscore(indexes[0], str(first.id)) == first.expires_at
        assert await redis_client.expiretime(indexes[0]) == first.expires_at
        assert (
            await store.rotate(
                session_id=first.id,
                user_id=owner,
                expected_refresh_jti=first.refresh_jti,
                new_refresh_jti=uuid4(),
                expires_at=now + 120,
            )
            == "rotated"
        )
        assert await redis_client.zscore(indexes[0], str(first.id)) == now + 120
        assert await redis_client.expiretime(indexes[0]) == now + 120
        assert await store.revoke_all(owner) == 2
        assert (await store.get(first.id)).revoked
        assert (await store.get(second.id)).revoked
        assert not (await store.get(unrelated.id)).revoked
        assert await store.revoke_all(owner) == 0
        assert not await redis_client.exists(indexes[0])
        assert (
            await store.rotate(
                session_id=first.id,
                user_id=owner,
                expected_refresh_jti=first.refresh_jti,
                new_refresh_jti=uuid4(),
                expires_at=now + 180,
            )
            == "invalid"
        )
        print(
            "PASS: User index tracks expiry; logout-all revokes exactly one user's sessions and refresh cannot revive them"
        )

        # A login racing with logout-all is either revoked or registered for the next logout-all.
        for _ in range(8):
            candidate, _ = await asyncio.gather(create(), store.revoke_all(owner))
            saved = await store.get(candidate.id)
            if not saved.revoked:
                assert (
                    await redis_client.zscore(indexes[0], str(candidate.id))
                    == candidate.expires_at
                )
            await store.revoke_all(owner)
            assert (await store.get(candidate.id)).revoked
        print(
            "PASS: Concurrent login and logout-all never leave an active unindexed session"
        )

        candidate = await create()
        outcomes = await asyncio.gather(
            store.rotate(
                session_id=candidate.id,
                user_id=owner,
                expected_refresh_jti=candidate.refresh_jti,
                new_refresh_jti=uuid4(),
                expires_at=now + 180,
            ),
            store.revoke_all(owner),
        )
        assert outcomes[0] in {"rotated", "invalid"}
        assert (await store.get(candidate.id)).revoked
        assert not await redis_client.exists(indexes[0])
        print("PASS: Concurrent refresh and logout-all always end revoked")

        legacy = SessionRecord(
            id=uuid4(),
            user_id=owner,
            refresh_jti=uuid4(),
            created_at=now,
            expires_at=now + 60,
        )
        legacy_key = f"finoracle:v1:auth:session:{legacy.id}"
        records.append(legacy)
        await redis_client.set(legacy_key, legacy.model_dump_json(), ex=60)
        try:
            assert await store.get(legacy.id) is None
        finally:
            await redis_client.delete(legacy_key)
        print("PASS: Unindexed pre-upgrade sessions cannot authenticate")
    finally:
        keys = [store._key(record.id) for record in records] + indexes
        await redis_client.delete(*keys)
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(check_index(), timeout=20))

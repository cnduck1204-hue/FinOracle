"""Remove only the explicitly named disposable local frontend test accounts."""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from sqlalchemy import delete, select

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.db.session import SessionFactory, engine
from app.models.user import User


async def main():
    names = sys.argv[1:]
    assert names and all(re.fullmatch(r"e2e_[a-f0-9]{16}", name) for name in names)
    try:
        async with SessionFactory() as session:
            ids = set(await session.scalars(select(User.id).where(User.username.in_(names))))
        keys = [SessionStore._user_key(user_id) for user_id in ids]
        owners = {str(user_id) for user_id in ids}
        async for key in redis_client.scan_iter(match="finoracle:v2:auth:session:*", count=100):
            raw = await redis_client.get(key)
            try:
                if raw and json.loads(raw).get("user_id") in owners:
                    keys.append(key)
            except (ValueError, AttributeError):
                pass
        if keys:
            await redis_client.delete(*keys)
        # Counter cleanup is scoped to exactly these generated identifiers.
        from app.cache.login_failures import LoginFailureStore

        limiter = LoginFailureStore(redis_client, max_failures=5, lock_seconds=900)
        await redis_client.delete(*(limiter._key(value) for name in names for value in (name, f"{name}@example.com")))
        async with SessionFactory() as session:
            await session.execute(delete(User).where(User.username.in_(names)))
            await session.commit()
        print("Disposable frontend test accounts and sessions removed.")
    finally:
        await redis_client.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

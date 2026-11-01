"""Create one explicitly named disposable local administrator for browser tests."""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.core.config import get_settings
from app.db.session import SessionFactory, engine
from app.models.user import User
from app.security.passwords import hash_password


async def main():
    payload = json.load(sys.stdin)
    name = payload["username"]
    assert re.fullmatch(r"e2e_[a-f0-9]{16}", name)
    assert get_settings().mysql_host in {"127.0.0.1", "localhost", "::1"}
    try:
        digest = await hash_password(payload["password"])
        async with SessionFactory() as session:
            async with session.begin():
                user = User(
                    username=name,
                    email=f"{name}@example.com",
                    password_hash=digest,
                    role="admin",
                    status="active",
                )
                session.add(user)
                await session.flush()
                user_id = str(user.id)
        print(json.dumps({"id": user_id, "username": name}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

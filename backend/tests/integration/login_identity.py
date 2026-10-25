"""Ensure MySQL collation aliases cannot bypass per-identifier login limits."""

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.cache.login_failures import LoginFailureStore
from app.cache.redis import redis_client
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.db import session as database
from app.models.user import User
from app.schemas.auth import LoginRequest, RegisterRequest
from app.services.auth import authenticate_with_login_limit, register_user


async def check_identity():
    name = f"alice_{uuid4().hex[:12]}"
    email = f"{name}@example.com"
    aliases = [
        name.replace("a", "á", 1),
        email.replace("a", "á", 1),
        email.replace("example", "éxample"),
    ]
    settings = get_settings()
    limiter = LoginFailureStore(
        redis_client,
        max_failures=settings.login_max_failures,
        lock_seconds=settings.login_lock_seconds,
    )

    async def attempt(identifier, password, status):
        async with database.SessionFactory() as session:
            try:
                await authenticate_with_login_limit(
                    LoginRequest(identifier=identifier, password=password),
                    session,
                    redis_client,
                )
            except AppException as error:
                assert error.status_code == status
            else:
                raise AssertionError(
                    "A database collation alias authenticated the target account"
                )

    try:
        async with database.SessionFactory() as session:
            user = await register_user(
                RegisterRequest(
                    username=name,
                    email=email,
                    password="Sample123",
                    confirm_password="Sample123",
                ),
                session,
            )
        for alias in aliases:
            async with database.SessionFactory() as session:
                column = User.email if "@" in alias else User.username
                candidate = await session.scalar(select(User).where(column == alias))
                assert candidate is not None and candidate.id == user.id
            await attempt(alias, "Sample123", 401)
        for _ in range(settings.login_max_failures):
            await attempt(name, "Wrong123", 401)
        await attempt(name, "Sample123", 429)
        for alias in aliases:
            await attempt(alias, "Sample123", 401)
        await attempt(name, "Sample123", 429)
        print(
            "PASS: Real MySQL accent aliases never authenticate and cannot bypass or clear the canonical lockout"
        )
    finally:
        try:
            await redis_client.delete(
                *(limiter._key(identifier) for identifier in [name, email, *aliases])
            )
        finally:
            async with database.SessionFactory() as session:
                await session.execute(delete(User).where(User.username == name))
                await session.commit()
            await redis_client.aclose()
            await database.engine.dispose()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(check_identity(), timeout=30))

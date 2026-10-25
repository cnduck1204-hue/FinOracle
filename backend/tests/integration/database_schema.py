"""Run explicitly with `uv run python -m tests.integration.database_schema` from backend/."""

import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.db import session as database
from app.models import User


async def check_users():
    token = uuid4().hex
    username = f"smoke_{token[:20]}"
    email = f"smoke_{token}@example.test"
    raw_id = uuid4()
    created_ids = [raw_id]
    password_hash = "smoke-test-only-not-a-real-password-hash"

    async def check_conflict(duplicate_username, duplicate_email):
        async with database.SessionFactory() as session:
            session.add(
                User(
                    username=duplicate_username,
                    email=duplicate_email,
                    password_hash=password_hash,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                assert error.orig.args[0] == 1062, "Expected MySQL duplicate key error"
                await session.rollback()
            else:
                raise AssertionError("Duplicate identifier was accepted")

    try:
        before = datetime.now(UTC).replace(tzinfo=None)
        async with database.SessionFactory() as session:
            user = User(username=username, email=email, password_hash=password_hash)
            session.add(user)
            await session.flush()
            user_id = user.id
            created_ids.append(user_id)
            after = datetime.now(UTC).replace(tzinfo=None)
            assert isinstance(user_id, UUID)
            assert user.role == "user"
            assert user.status == "active"
            assert user.last_login_at is None
            assert before <= user.created_at <= after
            assert before <= user.updated_at <= after
            created_at = user.created_at
            updated_at = user.updated_at
            await session.commit()
        print("PASS: UUID, role, status and UTC timestamp defaults")

        async with database.SessionFactory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            assert user.username == username
            assert user.email == email
            assert user.password_hash == password_hash
            assert user.created_at == created_at
            assert user.updated_at == updated_at
        print("PASS: Committed user can be read from a new session")

        await check_conflict(username, f"other_{token}@example.test")
        print("PASS: Database rejects duplicate usernames")

        await check_conflict(f"other_{token[:20]}", email)
        print("PASS: Database rejects duplicate email addresses")

        async with database.SessionFactory() as session:
            user = await session.get(User, user_id)
            user.status = "disabled"
            await session.commit()
        async with database.SessionFactory() as session:
            user = await session.get(User, user_id)
            assert user.status == "disabled"
            assert user.created_at == created_at
            assert user.updated_at > updated_at
        print("PASS: ORM update advances updated_at and preserves created_at")

        async with database.SessionFactory() as session:
            now = datetime.now(UTC).replace(tzinfo=None)
            await session.execute(
                text(
                    "INSERT INTO users "
                    "(id, username, email, password_hash, created_at, updated_at) "
                    "VALUES (:id, :username, :email, :password_hash, :now, :now)"
                ),
                {
                    "id": raw_id.hex,
                    "username": f"raw_{token[:20]}",
                    "email": f"raw_{token}@example.test",
                    "password_hash": password_hash,
                    "now": now,
                },
            )
            await session.commit()
        async with database.SessionFactory() as session:
            raw_user = await session.get(User, raw_id)
            assert raw_user is not None
            assert raw_user.role == "user"
            assert raw_user.status == "active"
        print("PASS: Database server defaults apply without ORM defaults")
    finally:
        async with database.SessionFactory() as session:
            await session.execute(delete(User).where(User.id.in_(created_ids)))
            await session.commit()
        async with database.SessionFactory() as session:
            for user_id in created_ids:
                assert await session.get(User, user_id) is None
        print("PASS: Test users cleaned up")


async def main():
    try:
        await asyncio.wait_for(check_users(), timeout=30)
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, TimeoutError) as error:
        original = getattr(error, "orig", error)
        args = getattr(original, "args", ())
        code = args[0] if args and isinstance(args[0], int) else "unavailable"
        print(f"FAIL: {type(error).__name__}; database error code={code}")
        sys.exit(1)

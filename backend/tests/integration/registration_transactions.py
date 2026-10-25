"""Run explicitly with `uv run python -m tests.integration.registration_transactions` from backend/."""

import asyncio
import sys
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.exceptions import AppException
from app.db import session as database
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import RegisterRequest
from app.security.passwords import verify_password
from app.services.auth import register_user


async def check_registration():
    token = uuid4().hex[:16]
    prefix = f"reg_{token}"
    names = {
        f"{prefix}_{suffix}" for suffix in ["main", "email", "undo", "race1", "race2"]
    }
    password = " FinOracle密码123 "

    def payload(suffix, email=None):
        return RegisterRequest(
            username=f"{prefix}_{suffix}",
            email=email or f"{prefix}_{suffix}@example.com",
            password=password,
            confirm_password=password,
        )

    async def count_users(username=None):
        async with database.SessionFactory() as session:
            statement = select(func.count()).select_from(User)
            statement = statement.where(
                User.username == username if username else User.username.in_(names)
            )
            return await session.scalar(statement)

    try:
        request = payload("main")
        async with database.SessionFactory() as session:
            user = await register_user(request, session)
            user_id = user.id
            assert user.role == "user" and user.status == "active"
            assert user.created_at is not None and user.updated_at is not None
            assert not session.in_transaction()
        async with database.SessionFactory() as session:
            stored = await session.get(User, user_id)
            assert stored is not None
            assert stored.password_hash.startswith("$argon2id$")
            assert stored.password_hash != password
            assert await verify_password(password, stored.password_hash)
        print("PASS: Real registration commits a regular user with an Argon2id hash")

        for duplicate, expected_code in [
            (request, "USER_USERNAME_EXISTS"),
            (payload("email", str(request.email)), "USER_EMAIL_EXISTS"),
        ]:
            async with database.SessionFactory() as session:
                try:
                    await register_user(duplicate, session)
                except AppException as error:
                    assert error.code == expected_code
                    assert error.status_code == 409
                    assert not session.in_transaction()
                else:
                    raise AssertionError("Duplicate registration was accepted")
        assert await count_users() == 1
        print("PASS: Duplicate username and email return 409 without inserting users")

        original_add = UserRepository.add
        for error_code in [1062, 1452]:
            injected = IntegrityError(
                "test-only", {}, Exception(error_code, "test-only")
            )

            async def insert_then_fail(repository, new_user, failure=injected):
                await original_add(repository, new_user)
                assert new_user.id is not None
                raise failure

            with patch.object(UserRepository, "add", insert_then_fail):
                async with database.SessionFactory() as session:
                    try:
                        await register_user(payload("undo"), session)
                    except AppException as error:
                        assert error_code == 1062
                        assert error.code == "USER_ALREADY_EXISTS"
                    except IntegrityError as error:
                        assert error_code == 1452 and error is injected
                    else:
                        raise AssertionError("Injected failure was swallowed")
                    assert not session.in_transaction()
            assert await count_users(f"{prefix}_undo") == 0
        print(
            "PASS: Failure after a real INSERT rolls back; unrelated errors propagate"
        )

        barrier = asyncio.Barrier(2)
        race_email = f"{prefix}_race@example.com"
        original_lookup = UserRepository.get_by_email

        async def synchronized_lookup(repository, email):
            existing = await original_lookup(repository, email)
            if email == race_email:
                assert existing is None
                await asyncio.wait_for(barrier.wait(), timeout=10)
            return existing

        async def race_register(suffix):
            async with database.SessionFactory() as session:
                try:
                    await register_user(payload(suffix, race_email), session)
                except AppException as error:
                    assert error.code == "USER_ALREADY_EXISTS"
                    assert error.status_code == 409
                    result = "conflict"
                else:
                    result = "created"
                assert not session.in_transaction()
                return result

        with patch.object(UserRepository, "get_by_email", synchronized_lookup):
            outcomes = await asyncio.gather(
                race_register("race1"), race_register("race2")
            )
        assert sorted(outcomes) == ["conflict", "created"]
        assert await count_users() == 2
        print("PASS: Concurrent registrations produce one committed user and one 409")
    finally:
        async with database.SessionFactory() as session:
            await session.execute(delete(User).where(User.username.in_(names)))
            await session.commit()
        assert await count_users() == 0
        print("PASS: Only this run's test users were deleted")


async def main():
    try:
        await asyncio.wait_for(check_registration(), timeout=45)
    finally:
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, AppException, TimeoutError) as error:
        print(f"FAIL: {type(error).__name__}")
        sys.exit(1)

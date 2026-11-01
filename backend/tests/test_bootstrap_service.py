import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.models.user import User
from app.services import bootstrap


def admin_settings(**changes):
    values = {
        "admin_username": "root_01",
        "admin_email": "root@example.com",
        "admin_password": " PrivateBootstrap123 ",
    }
    values.update(changes)
    return Settings(
        _env_file=None,
        mysql_password="test-only",
        jwt_secret_key="d98f15c0e2b64aa7b91d0438f5c6e270a18b3f9d42e65c07b6a1d8e34f9205ac",
        **values,
    )


@pytest.fixture
def bootstrap_context(monkeypatch):
    session = AsyncSession(expire_on_commit=False)
    session.scalar = AsyncMock(return_value=None)
    transactions = []
    event.listen(
        session.sync_session, "after_commit", lambda _: transactions.append("commit")
    )
    event.listen(
        session.sync_session,
        "after_rollback",
        lambda _: transactions.append("rollback"),
    )
    repository = SimpleNamespace(
        get_by_username=AsyncMock(return_value=None),
        get_by_email=AsyncMock(return_value=None),
        add=AsyncMock(),
    )
    factory = Mock(return_value=repository)
    hasher = AsyncMock(return_value="encoded-admin-hash")
    monkeypatch.setattr(bootstrap, "UserRepository", factory)
    monkeypatch.setattr(bootstrap, "hash_password", hasher)
    yield SimpleNamespace(
        settings=admin_settings(),
        session=session,
        repository=repository,
        factory=factory,
        hasher=hasher,
        transactions=transactions,
    )
    asyncio.run(session.close())


def test_bootstrap_creates_active_admin_with_hashed_exact_password(bootstrap_context):
    context = bootstrap_context
    asyncio.run(bootstrap.bootstrap_admin(context.settings, context.session))

    user = context.repository.add.await_args.args[0]
    assert isinstance(user, User)
    assert (user.username, user.email) == ("root_01", "root@example.com")
    assert (user.role, user.status, user.auth_version) == ("admin", "active", 0)
    assert user.password_hash == "encoded-admin-hash"
    assert user.password_hash != context.settings.admin_password.get_secret_value()
    context.hasher.assert_awaited_once_with(" PrivateBootstrap123 ")
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


def test_existing_matching_admin_preserves_password_status_and_version(
    bootstrap_context,
):
    context = bootstrap_context
    admin = User(
        username=context.settings.admin_username,
        email=context.settings.admin_email,
        password_hash="existing-admin-hash",
        role="admin",
        status="disabled",
        auth_version=4,
    )
    context.session.scalar.return_value = admin
    asyncio.run(bootstrap.bootstrap_admin(context.settings, context.session))

    assert (admin.password_hash, admin.role, admin.status, admin.auth_version) == (
        "existing-admin-hash",
        "admin",
        "disabled",
        4,
    )
    context.factory.assert_not_called()
    context.hasher.assert_not_awaited()
    context.repository.add.assert_not_awaited()
    assert context.transactions == ["commit"]


@pytest.mark.parametrize("changed_field", ["username", "email"])
def test_existing_renamed_admin_is_preserved_despite_old_initialization_config(
    bootstrap_context, changed_field
):
    context = bootstrap_context
    admin = User(
        username=context.settings.admin_username,
        email=context.settings.admin_email,
        password_hash="existing-admin-hash",
        role="admin",
        status="active",
        auth_version=0,
    )
    setattr(
        admin,
        changed_field,
        "different" if changed_field == "username" else "different@example.com",
    )
    context.session.scalar.return_value = admin
    original_identity = (admin.username, admin.email)
    asyncio.run(bootstrap.bootstrap_admin(context.settings, context.session))
    assert (admin.username, admin.email) == original_identity
    assert admin.password_hash == "existing-admin-hash"
    context.factory.assert_not_called()
    context.hasher.assert_not_awaited()
    context.repository.add.assert_not_awaited()
    assert context.transactions == ["commit"]
    assert not context.session.in_transaction()


@pytest.mark.parametrize("field", ["username", "email"])
def test_regular_account_collision_is_never_promoted(bootstrap_context, field):
    context = bootstrap_context
    regular = User(
        username="root_01",
        email="root@example.com",
        password_hash="regular-hash",
        role="user",
        status="active",
        auth_version=2,
    )
    getattr(context.repository, f"get_by_{field}").return_value = regular
    with pytest.raises(ValueError, match="初始化管理员账号与已有账号冲突"):
        asyncio.run(bootstrap.bootstrap_admin(context.settings, context.session))
    assert (regular.role, regular.password_hash, regular.auth_version) == (
        "user",
        "regular-hash",
        2,
    )
    context.repository.add.assert_not_awaited()
    context.hasher.assert_not_awaited()
    assert context.transactions == ["rollback"]


@pytest.mark.parametrize("operation", ["lookup", "hash", "add", "commit"])
def test_bootstrap_failure_rolls_back_and_propagates(bootstrap_context, operation):
    context = bootstrap_context
    error = OperationalError(
        "test-only", {}, Exception(2006, "private-bootstrap-marker")
    )
    if operation == "lookup":
        context.session.scalar.side_effect = error
    elif operation == "hash":
        context.hasher.side_effect = error
    elif operation == "add":
        context.repository.add.side_effect = error
    else:

        def fail_commit(_):
            raise error

        event.listen(context.session.sync_session, "before_commit", fail_commit)
    with pytest.raises(OperationalError) as caught:
        asyncio.run(bootstrap.bootstrap_admin(context.settings, context.session))
    assert caught.value is error
    assert context.transactions == ["rollback"]
    assert not context.session.in_transaction()


@pytest.fixture
def initialization_context(monkeypatch):
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=1), commit=AsyncMock(), execute=AsyncMock()
    )
    connection_manager = AsyncMock()
    connection_manager.__aenter__.return_value = connection
    engine = Mock(spec=AsyncEngine)
    engine.connect.return_value = connection_manager
    session = object()
    session_manager = AsyncMock()
    session_manager.__aenter__.return_value = session
    session_factory = Mock(return_value=session_manager)
    operation = AsyncMock()
    monkeypatch.setattr(bootstrap, "AsyncSession", session_factory)
    monkeypatch.setattr(bootstrap, "bootstrap_admin", operation)
    return SimpleNamespace(
        settings=admin_settings(),
        engine=engine,
        connection=connection,
        connection_manager=connection_manager,
        session=session,
        session_manager=session_manager,
        session_factory=session_factory,
        operation=operation,
    )


def test_initialize_without_admin_config_does_not_connect(initialization_context):
    context = initialization_context
    settings = admin_settings(
        admin_username=None, admin_email=None, admin_password=None
    )
    asyncio.run(bootstrap.initialize_admin(settings, context.engine))
    context.engine.connect.assert_not_called()
    context.session_factory.assert_not_called()
    context.operation.assert_not_awaited()


def test_initialize_holds_connection_until_after_lock_release(initialization_context):
    context = initialization_context
    actions = []

    async def scalar(statement):
        actions.append(str(statement))
        return 1

    async def commit():
        actions.append("commit")

    async def initialize(settings, session):
        actions.append("initialize")

    async def execute(statement):
        actions.append(str(statement))

    async def close_connection(*args):
        actions.append("close")

    context.connection.scalar.side_effect = scalar
    context.connection.commit.side_effect = commit
    context.connection.execute.side_effect = execute
    context.operation.side_effect = initialize
    context.connection_manager.__aexit__.side_effect = close_connection
    asyncio.run(bootstrap.initialize_admin(context.settings, context.engine))
    assert actions == [
        "SELECT GET_LOCK('finoracle:bootstrap-admin', 10)",
        "commit",
        "initialize",
        "SELECT RELEASE_LOCK('finoracle:bootstrap-admin')",
        "commit",
        "close",
    ]
    context.session_factory.assert_called_once_with(
        bind=context.connection, expire_on_commit=False
    )
    context.operation.assert_awaited_once_with(context.settings, context.session)
    context.session_manager.__aexit__.assert_awaited_once()


@pytest.mark.parametrize("lock_result", [0, None])
def test_initialize_lock_timeout_never_bootstraps(initialization_context, lock_result):
    context = initialization_context
    context.connection.scalar.return_value = lock_result
    with pytest.raises(RuntimeError, match="无法取得管理员初始化锁"):
        asyncio.run(bootstrap.initialize_admin(context.settings, context.engine))
    context.operation.assert_not_awaited()
    context.session_factory.assert_not_called()
    context.connection.execute.assert_not_awaited()
    context.connection.commit.assert_awaited_once()
    context.connection_manager.__aexit__.assert_awaited_once()


def test_initialize_releases_lock_when_bootstrap_fails(initialization_context):
    context = initialization_context
    error = ValueError("configuration conflict")
    context.operation.side_effect = error
    with pytest.raises(ValueError) as caught:
        asyncio.run(bootstrap.initialize_admin(context.settings, context.engine))
    assert caught.value is error
    assert (
        str(context.connection.execute.await_args.args[0])
        == "SELECT RELEASE_LOCK('finoracle:bootstrap-admin')"
    )
    assert context.connection.commit.await_count == 2
    context.session_manager.__aexit__.assert_awaited_once()
    context.connection_manager.__aexit__.assert_awaited_once()


def test_initialize_releases_lock_when_session_creation_fails(initialization_context):
    context = initialization_context
    error = RuntimeError("session unavailable")
    context.session_factory.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(bootstrap.initialize_admin(context.settings, context.engine))
    assert caught.value is error
    assert (
        str(context.connection.execute.await_args.args[0])
        == "SELECT RELEASE_LOCK('finoracle:bootstrap-admin')"
    )
    assert context.connection.commit.await_count == 2
    context.operation.assert_not_awaited()
    context.connection_manager.__aexit__.assert_awaited_once()

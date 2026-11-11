"""Create a disposable administrator and an authenticated client for API checks."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.config import get_settings
from app.db import session as database
from app.models import Document, DocumentVersion, User
from app.security.passwords import hash_password
from app.security.tokens import decode_token
from app.storage import S3ObjectStorage
from app.storage.keys import version_prefix
from tests.integration.containers import compose


@asynccontextmanager
async def admin_api(prefix: str) -> AsyncIterator[httpx.AsyncClient]:
    """登录本轮创建的管理员；退出时删除该账号与会话。调用方须先删除自己创建的制度。"""
    name = f"{prefix}_{uuid4().hex[:12]}"
    password = "AdminCheck9" + secrets.token_hex(12)
    address = compose("port", "api", "8000").splitlines()[0]
    base_url = f"http://{address}{get_settings().api_v1_prefix.rstrip('/')}"
    keys: list[str] = []
    async with database.SessionFactory() as session, session.begin():
        admin = User(
            username=name,
            email=f"{name}@example.com",
            password_hash=await hash_password(password),
            role="admin",
            status="active",
            auth_version=0,
        )
        session.add(admin)
        await session.flush()
        admin_id = admin.id
    try:
        async with httpx.AsyncClient(
            base_url=base_url, timeout=30, trust_env=False
        ) as client:
            login = await client.post(
                "/auth/login", json={"identifier": name, "password": password}
            )
            assert login.status_code == 200, login.text
            token = login.json()["access_token"]
            keys.append(
                SessionStore._key(decode_token(token, expected_type="access").sid)
            )
            client.headers["Authorization"] = f"Bearer {token}"
            yield client
    finally:
        async with database.SessionFactory() as session, session.begin():
            await session.execute(delete(User).where(User.id == admin_id))
        if keys:
            await redis_client.delete(*keys)


async def wait_for_version(
    client: httpx.AsyncClient, version_id: str, timeout: float = 180
) -> dict:
    """等待版本离开处理中状态（uploaded / parsing / indexing）。"""
    for _ in range(int(timeout)):
        version = (await client.get(f"/admin/versions/{version_id}")).json()
        if version["status"] not in ("uploaded", "parsing", "indexing"):
            return version
        await asyncio.sleep(1)
    raise AssertionError(f"Version {version_id} still {version['status']}")


async def remove_documents(
    document_ids: list[UUID], storage: S3ObjectStorage, store=None
) -> None:
    """删除本轮创建的制度、版本、对象与向量（包括接口不允许删除的已归档版本）。"""
    async with database.SessionFactory() as session, session.begin():
        versions = (
            await session.execute(
                select(DocumentVersion.document_id, DocumentVersion.id).where(
                    DocumentVersion.document_id.in_(document_ids)
                )
            )
        ).all()
        await session.execute(
            delete(DocumentVersion).where(DocumentVersion.document_id.in_(document_ids))
        )
        await session.execute(delete(Document).where(Document.id.in_(document_ids)))
    for document_id, version_id in versions:
        listing = await asyncio.to_thread(
            storage._client.list_objects_v2,
            Bucket=storage.bucket,
            Prefix=version_prefix(document_id, version_id) + "/",
        )
        for item in listing.get("Contents", []):
            await asyncio.to_thread(storage.delete, item["Key"])
        if store is not None:
            await asyncio.to_thread(store.delete_by_version, version_id)


SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "parsing"

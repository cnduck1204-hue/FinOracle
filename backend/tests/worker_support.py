"""worker 测试共用：内存对象存储、异步 SQLite 数据库、可控时钟与种子数据。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.db.engine import create_session_factory
from app.models import Document, DocumentVersion, ImportJob
from app.storage import ObjectInfo, ObjectStorage, check_key
from app.storage.errors import storage_unavailable
from app.storage.keys import original_key

T0 = datetime(2026, 11, 5, 8, 0, 0)
NO_OCR_OPTIONS = {
    "ocr": "none",
    "chunker": "auto",
    "max_chunk_chars": 800,
    "chunk_overlap": 100,
}


class MemoryStorage(ObjectStorage):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str | None] = {}
        self.unavailable = False

    def _check(self) -> None:
        if self.unavailable:
            raise storage_unavailable()

    def ensure_bucket(self) -> None:
        self._check()

    def put_file(
        self, key: str, path: Path, *, content_type: str | None = None
    ) -> None:
        self._check()
        self.objects[check_key(key)] = path.read_bytes()
        self.content_types[key] = content_type

    def download_file(self, key: str, path: Path) -> None:
        self._check()
        if check_key(key) not in self.objects:
            raise FileNotFoundError(key)
        path.write_bytes(self.objects[key])

    def stat(self, key: str) -> ObjectInfo | None:
        self._check()
        data = self.objects.get(check_key(key))
        if data is None:
            return None
        return ObjectInfo(key, len(data), self.content_types.get(key))

    def delete(self, key: str) -> None:
        self._check()
        self.objects.pop(check_key(key), None)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def run_with_database(
    tmp_path: Path, scenario: Callable[[SimpleNamespace], Awaitable[Any]]
) -> Any:
    """在新的事件循环中创建文件型 SQLite（多个会话可并发连接）并执行场景。"""

    async def main() -> Any:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}")

        @event.listens_for(engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        env = SimpleNamespace(
            sessions=create_session_factory(engine),
            storage=MemoryStorage(),
            clock=Clock(),
        )
        try:
            return await scenario(env)
        finally:
            await engine.dispose()

    return asyncio.run(main())


async def seed(
    env: SimpleNamespace,
    *,
    file_name: str = "办法.txt",
    content: bytes | None = "第一条 内容。".encode(),
    parse_options: dict[str, Any] | None = None,
    version_status: str = "uploaded",
    job: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """登记制度、版本与一个任务；content 为 None 时不上传原件。"""
    file_type = file_name.rsplit(".", 1)[-1]
    async with env.sessions() as session, session.begin():
        document = Document(title="金融机构管理办法")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            status=version_status,
            file_name=file_name,
            file_type=file_type,
            size_bytes=len(content or b""),
            sha256="0" * 64,
            object_key="pending",
            parse_options=parse_options or NO_OCR_OPTIONS,
        )
        session.add(version)
        await session.flush()
        version.object_key = original_key(document.id, version.id, file_type)
        values = {"kind": "parse", "run_after": env.clock() - timedelta(seconds=1)}
        record = ImportJob(version_id=version.id, **(values | (job or {})))
        session.add(record)
        await session.flush()
        ids = SimpleNamespace(
            document_id=document.id,
            version_id=version.id,
            job_id=record.id,
            key=version.object_key,
        )
    if content is not None:
        env.storage.objects[ids.key] = content
    return ids


async def load(env: SimpleNamespace, model, identifier):
    async with env.sessions() as session:
        return await session.get(model, identifier)

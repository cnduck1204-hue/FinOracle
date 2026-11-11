"""Import a real document through the API and the Compose worker, then manage it."""

import asyncio
import secrets
import sys
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.cache.redis import redis_client
from app.cache.sessions import SessionStore
from app.core.config import get_settings
from app.db import session as database
from app.models import Document, DocumentVersion, User
from app.security.passwords import hash_password
from app.security.tokens import decode_token
from app.storage.keys import blocks_key, original_key
from tests.integration.api_admin import wait_for_version
from tests.integration.containers import compose
from tests.integration.worker import host_storage

SAMPLE = Path(__file__).resolve().parents[2] / "samples" / "parsing" / "04_旧版规定.doc"


async def check_imports() -> None:
    name = f"imptest_{uuid4().hex[:12]}"
    password = "ImportTest9" + secrets.token_hex(12)
    address = compose("port", "api", "8000").splitlines()[0]
    base_url = f"http://{address}{get_settings().api_v1_prefix.rstrip('/')}"
    storage = host_storage()
    admin_id = None
    session_keys: list[str] = []
    document_ids: list[UUID] = []

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
            session_keys.append(
                SessionStore._key(decode_token(token, expected_type="access").sid)
            )
            client.headers["Authorization"] = f"Bearer {token}"

            async def wait(job_id: str) -> dict:
                for _ in range(120):
                    job = (await client.get(f"/admin/imports/{job_id}")).json()
                    if job["status"] in ("succeeded", "failed"):
                        return job
                    await asyncio.sleep(1)
                raise AssertionError(f"Job {job_id} still {job['status']}")

            content = SAMPLE.read_bytes()
            response = await client.post(
                "/admin/imports",
                files={"file": (SAMPLE.name, content, "application/msword")},
                data={
                    "title": f"集成测试 {name}",
                    "issuer": "测试机构",
                    "version_note": "集成测试版本",
                    "published_date": "2026-11-05",
                    "ocr": "none",
                },
            )
            assert response.status_code == 202, response.text
            job = response.json()
            document_ids.append(UUID(job["document_id"]))
            version_id = job["version_id"]
            assert (job["status"], job["file_name"]) == ("queued", SAMPLE.name)
            print("PASS: Upload is stored and a parse job is queued immediately")

            done = await wait(job["id"])
            assert (done["status"], done["error_code"]) == ("succeeded", None), done
            # 默认自动批准，解析后版本转入向量化；等它离开处理中状态再继续
            version = await wait_for_version(client, version_id)
            settled = version["status"]
            assert settled in ("parsed", "ready", "failed"), version
            assert (version["parse_status"], version["has_pdf"]) == ("completed", True)
            assert version["chunk_count"] > 0 and version["page_count"] >= 1
            chunks = (
                await client.get(
                    f"/admin/versions/{version_id}/chunks", params={"page_size": 100}
                )
            ).json()
            assert chunks["total"] == version["chunk_count"] == len(chunks["items"])
            assert all(item["content"] for item in chunks["items"])
            print(
                f"PASS: Compose worker parsed the upload into {chunks['total']} "
                "chunks visible through the API"
            )

            original = await client.get(f"/admin/versions/{version_id}/file")
            assert original.content == content
            assert original.headers["content-type"] == "application/msword"
            assert original.headers["content-disposition"].startswith("attachment;")
            pdf = await client.get(
                f"/admin/versions/{version_id}/file", params={"variant": "pdf"}
            )
            assert pdf.content.startswith(b"%PDF")
            assert pdf.headers["content-type"] == "application/pdf"
            assert pdf.headers["content-disposition"].startswith("inline;")
            print("PASS: Original and converted PDF stream from object storage")

            duplicate = await client.post(
                "/admin/imports",
                files={"file": (SAMPLE.name, content)},
                data={"document_id": job["document_id"]},
            )
            assert duplicate.json()["code"] == "DOCUMENT_VERSION_DUPLICATE"
            listed = (await client.get("/admin/documents", params={"q": name})).json()
            assert [item["id"] for item in listed["items"]] == [job["document_id"]]
            assert listed["items"][0]["latest_version"]["status"] == settled
            patched = await client.patch(
                f"/admin/versions/{version_id}", json={"validity_status": "effective"}
            )
            assert patched.json()["validity_status"] == "effective"
            print("PASS: Duplicate uploads are refused; list and metadata edits work")

            reparse = await client.post(
                f"/admin/versions/{version_id}/reparse",
                json={
                    "ocr": "none",
                    "chunker": "length",
                    "max_chunk_chars": 4000,
                    "chunk_overlap": 0,
                },
            )
            assert reparse.status_code == 202, reparse.text
            done = await wait(reparse.json()["id"])
            await wait_for_version(client, version_id)
            assert done["status"] == "succeeded", done
            version = (await client.get(f"/admin/versions/{version_id}")).json()
            assert version["parse_stats"]["chunker"] == "length"
            print("PASS: Re-parsing with new options replaces the chunks")

            document_id, stored_version = UUID(job["document_id"]), UUID(version_id)
            keys = [
                original_key(document_id, stored_version, "doc"),
                blocks_key(document_id, stored_version),
            ]
            for key in keys:
                assert await asyncio.to_thread(storage.stat, key) is not None
            deleted = await client.delete(f"/admin/versions/{version_id}")
            assert deleted.status_code == 204
            assert (
                await client.get(f"/admin/versions/{version_id}")
            ).status_code == 404
            for key in keys:
                assert await asyncio.to_thread(storage.stat, key) is None
            print("PASS: Deleting the draft removes its records and stored objects")
    finally:
        async with database.SessionFactory() as session, session.begin():
            await session.execute(
                delete(DocumentVersion).where(
                    DocumentVersion.document_id.in_(document_ids)
                )
            )
            await session.execute(delete(Document).where(Document.id.in_(document_ids)))
            if admin_id is not None:
                await session.execute(delete(User).where(User.id == admin_id))
        if session_keys:
            await redis_client.delete(*session_keys)
        async with database.SessionFactory() as session:
            left = await session.scalars(
                select(Document.id).where(Document.id.in_(document_ids))
            )
            assert left.all() == []
        print(
            "PASS: Only this run's administrator, documents and sessions were removed"
        )


async def main() -> None:
    try:
        await check_imports()
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, httpx.HTTPError) as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        sys.exit(1)

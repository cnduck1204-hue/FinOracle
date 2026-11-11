"""Search published regulations through the API with real DashScope and Milvus.

Requires DASHSCOPE_API_KEY in the shell that started Compose; skipped otherwise.
"""

import asyncio
import statistics
import sys
import time
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import EmbeddingSettings
from app.db import session as database
from tests.integration.api_admin import (
    SAMPLES,
    admin_api,
    remove_documents,
    wait_for_version,
)
from tests.integration.indexing import host_vector_store
from tests.integration.worker import host_storage


async def check_search() -> None:
    embedding_settings = EmbeddingSettings()
    if embedding_settings.dashscope_api_key is None:
        print("SKIP: DASHSCOPE_API_KEY is not set; search was not checked")
        return
    store = host_vector_store(embedding_settings)
    storage = host_storage()
    document_ids: list[UUID] = []

    async with admin_api("srchtest") as client:

        async def search(query: str, **body) -> tuple[list[dict], float]:
            started = time.perf_counter()
            response = await client.post("/search", json={"query": query, **body})
            elapsed = (time.perf_counter() - started) * 1000
            assert response.status_code == 200, response.text
            return response.json()["results"], elapsed

        try:
            upload = await client.post(
                "/admin/imports",
                files={
                    "file": ("旧版规定.doc", (SAMPLES / "04_旧版规定.doc").read_bytes())
                },
                data={
                    "title": f"检索集成测试 {uuid4().hex[:8]}",
                    "version_note": "检索测试版",
                    "ocr": "none",
                },
            )
            assert upload.status_code == 202, upload.text
            job = upload.json()
            document_ids.append(UUID(job["document_id"]))
            version = await wait_for_version(client, job["version_id"])
            assert version["status"] == "ready", version
            chunks = (
                await client.get(f"/admin/versions/{job['version_id']}/chunks")
            ).json()["items"]
            target = max(chunks, key=lambda chunk: len(chunk["content"]))
            query = target["content"][:60]

            results, _ = await search(query)
            assert target["id"] not in [item["chunk_id"] for item in results]
            print("PASS: Ready but unpublished versions are not searchable")

            published = await client.post(
                f"/admin/versions/{job['version_id']}/publish"
            )
            assert published.json()["status"] == "published", published.text
            timings = []
            for _ in range(5):
                results, elapsed = await search(query, top_k=3)
                timings.append(elapsed)
            top = results[0]
            assert top["chunk_id"] == target["id"], results
            assert top["dense_rank"] == 1 and top["keyword_rank"] == 1, top
            assert top["document"]["id"] == job["document_id"]
            assert top["version"]["version_note"] == "检索测试版"
            assert (top["article"], top["content"]) == (
                target["article"],
                target["content"],
            )
            print(
                "PASS: Hybrid search ranks the matching chunk first on both paths "
                f"(median {statistics.median(timings):.0f} ms over 5 requests)"
            )

            scoped, _ = await search(query, document_ids=[str(uuid4())])
            assert scoped == []
            own, _ = await search(query, document_ids=[job["document_id"]])
            assert own and all(i["document"]["id"] == job["document_id"] for i in own)
            print("PASS: Document scope limits results to the selected regulations")

            withdrawn = await client.post(
                f"/admin/versions/{job['version_id']}/withdraw"
            )
            assert withdrawn.json()["status"] == "archived"
            results, _ = await search(query)
            assert target["id"] not in [item["chunk_id"] for item in results]
            print("PASS: Withdrawn versions disappear from search immediately")
        finally:
            await remove_documents(document_ids, storage, store)
            print("PASS: Only this run's documents, objects and vectors were removed")


async def main() -> None:
    from app.cache.redis import redis_client

    try:
        await check_search()
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, httpx.HTTPError) as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        sys.exit(1)

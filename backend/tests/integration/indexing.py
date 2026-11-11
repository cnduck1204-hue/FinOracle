"""Index real documents with DashScope and Milvus through the Compose worker, then publish.

Requires DASHSCOPE_API_KEY in the shell that started Compose; skipped otherwise.
"""

import asyncio
import sys
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import EmbeddingSettings, VectorStoreSettings
from app.core.exceptions import AppException
from app.db import session as database
from app.rag.indexing import DashScopeEmbedding, MilvusVectorStore
from tests.integration.api_admin import (
    SAMPLES,
    admin_api,
    remove_documents,
    wait_for_version,
)
from tests.integration.containers import compose
from tests.integration.worker import host_storage


def host_vector_store(embedding: EmbeddingSettings) -> MilvusVectorStore:
    address = compose("port", "milvus", "19530").splitlines()[0]
    settings = VectorStoreSettings().model_copy(
        update={"milvus_uri": f"http://{address}"}
    )
    return MilvusVectorStore(
        settings,
        model=embedding.embedding_model,
        dimension=embedding.embedding_dimension,
    )


async def check_indexing() -> None:
    embedding_settings = EmbeddingSettings()
    if embedding_settings.dashscope_api_key is None:
        print("SKIP: DASHSCOPE_API_KEY is not set; vectorization was not checked")
        return
    # 先确认密钥可用：无效密钥会让索引任务失败，原因在这里直接给出
    embedder = DashScopeEmbedding(embedding_settings)
    try:
        await embedder.embed_documents(["连通性检查"])
    except AppException as error:
        raise AssertionError(f"DashScope rejected the request: {error.code}") from error
    finally:
        await embedder.aclose()
    store = host_vector_store(embedding_settings)
    storage = host_storage()
    document_ids: list[UUID] = []
    title = f"向量化集成测试 {uuid4().hex[:8]}"

    async def upload(client: httpx.AsyncClient, sample: str, **fields) -> dict:
        response = await client.post(
            "/admin/imports",
            files={"file": (sample, (SAMPLES / sample).read_bytes())},
            data={"ocr": "none", **fields},
        )
        assert response.status_code == 202, response.text
        return response.json()

    async def count(version_id: str) -> int:
        return await asyncio.to_thread(store.count_by_version, UUID(version_id))

    async with admin_api("idxtest") as client:
        try:
            first = await upload(client, "04_旧版规定.doc", title=title)
            document_ids.append(UUID(first["document_id"]))
            version = await wait_for_version(client, first["version_id"])
            assert version["status"] == "ready", version
            assert version["index_model"] == (
                f"{embedding_settings.embedding_model}:"
                f"{embedding_settings.embedding_dimension}"
            )
            assert version["latest_job"]["kind"] == "index"
            assert await count(first["version_id"]) == version["chunk_count"] > 0
            print(
                f"PASS: Parsed version was auto-approved and {version['chunk_count']} "
                "chunks were embedded into Milvus"
            )

            chunks = (
                await client.get(f"/admin/versions/{first['version_id']}/chunks")
            ).json()["items"]
            target = chunks[-1]
            version_filter = f'version_id == "{first["version_id"]}"'
            client_milvus = store._client
            keyword = await asyncio.to_thread(
                client_milvus.search,
                store.collection,
                data=[target["content"][:40]],
                anns_field="sparse",
                limit=1,
                filter=version_filter,
                search_params={"metric_type": "BM25"},
                output_fields=["chunk_id"],
                consistency_level="Strong",
            )
            assert keyword[0][0]["entity"]["chunk_id"] == target["id"]
            embedder = DashScopeEmbedding(embedding_settings)
            try:
                (query,) = await embedder.embed_documents([target["content"]])
            finally:
                await embedder.aclose()
            dense = await asyncio.to_thread(
                client_milvus.search,
                store.collection,
                data=[query],
                anns_field="dense",
                limit=1,
                filter=version_filter,
                output_fields=["chunk_id"],
                consistency_level="Strong",
            )
            assert dense[0][0]["entity"]["chunk_id"] == target["id"]
            print("PASS: BM25 and dense search both find a chunk by its own text")

            published = await client.post(
                f"/admin/versions/{first['version_id']}/publish"
            )
            assert published.json()["status"] == "published", published.text

            second = await upload(
                client, "05_操作指南.md", document_id=first["document_id"]
            )
            version = await wait_for_version(client, second["version_id"])
            assert version["status"] == "ready", version
            replaced = await client.post(
                f"/admin/versions/{second['version_id']}/publish"
            )
            assert replaced.json()["status"] == "published", replaced.text
            old = (await client.get(f"/admin/versions/{first['version_id']}")).json()
            assert old["status"] == "archived"
            assert await count(first["version_id"]) == 0
            assert await count(second["version_id"]) == version["chunk_count"]
            document = (
                await client.get(f"/admin/documents/{first['document_id']}")
            ).json()
            assert document["published_version"]["id"] == second["version_id"]
            print(
                "PASS: Publishing a new version archives the old one and drops its vectors"
            )

            withdrawn = await client.post(
                f"/admin/versions/{second['version_id']}/withdraw"
            )
            assert withdrawn.json()["status"] == "archived"
            assert await count(second["version_id"]) == 0
            approved = await client.post(
                f"/admin/versions/{second['version_id']}/approve"
            )
            assert approved.status_code == 202, approved.text
            version = await wait_for_version(client, second["version_id"])
            assert version["status"] == "ready"
            assert await count(second["version_id"]) == version["chunk_count"]
            deleted = await client.delete(f"/admin/versions/{second['version_id']}")
            assert deleted.status_code == 204
            assert await count(second["version_id"]) == 0
            print("PASS: Withdraw, re-approve and delete keep Milvus in step")
        finally:
            await remove_documents(document_ids, storage, store)
            print("PASS: Only this run's documents, objects and vectors were removed")


async def main() -> None:
    from app.cache.redis import redis_client

    try:
        await check_indexing()
    finally:
        await redis_client.aclose()
        await database.engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (AssertionError, SQLAlchemyError, httpx.HTTPError) as error:
        print(f"FAIL: {type(error).__name__}: {error}")
        sys.exit(1)

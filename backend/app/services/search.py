"""制度检索：在已发布版本中做混合检索，并补全制度、版本与条文位置，供引用展示。

先在一个短事务中取已发布版本，检索期间不占用数据库连接；补全信息时再次限定已发布，
检索过程中被下架的版本不会出现在结果中。
"""

import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.rag.retrieval import HybridRetriever
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.schemas.search import (
    SearchDocument,
    SearchRequest,
    SearchResponse,
    SearchResult,
    SearchVersion,
)

logger = logging.getLogger(__name__)

# 依赖服务的这些错误对用户统一表现为检索暂不可用；具体原因只记日志
_DEPENDENCY_FAILURES = frozenset(
    {"EMBEDDING_UNAVAILABLE", "EMBEDDING_AUTH_FAILED", "VECTOR_STORE_UNAVAILABLE"}
)


def search_unavailable() -> AppException:
    return AppException(
        code="SEARCH_UNAVAILABLE",
        message="检索服务暂时不可用，请稍后重试",
        status_code=503,
    )


async def search(
    payload: SearchRequest, session: AsyncSession, retriever: HybridRetriever
) -> SearchResponse:
    started = time.perf_counter()
    async with session.begin():
        version_ids = await DocumentVersionRepository(session).published_ids(
            payload.document_ids
        )
    try:
        hits = await retriever.retrieve(
            payload.query, version_ids=version_ids, limit=payload.top_k
        )
    except AppException as error:
        if error.code not in _DEPENDENCY_FAILURES:
            raise
        logger.warning("search_dependency_failed: %s", error.code)
        raise search_unavailable() from error
    retrieved_ms = (time.perf_counter() - started) * 1000

    results: list[SearchResult] = []
    if hits:
        async with session.begin():
            rows = await DocumentChunkRepository(session).published_with_context(
                [hit.chunk_id for hit in hits]
            )
        found = {
            chunk.id: (chunk, version, document) for chunk, version, document in rows
        }
        for hit in hits:
            if hit.chunk_id not in found:
                continue
            chunk, version, document = found[hit.chunk_id]
            results.append(
                SearchResult(
                    chunk_id=chunk.id,
                    score=hit.score,
                    dense_rank=hit.dense_rank,
                    dense_score=hit.dense_score,
                    keyword_rank=hit.keyword_rank,
                    keyword_score=hit.keyword_score,
                    document=SearchDocument(
                        id=document.id, title=document.title, issuer=document.issuer
                    ),
                    version=SearchVersion(
                        id=version.id,
                        document_number=version.document_number,
                        version_note=version.version_note,
                        published_date=version.published_date,
                        effective_date=version.effective_date,
                        validity_status=version.validity_status,
                        source_url=version.source_url,
                    ),
                    ordinal=chunk.ordinal,
                    chapter=chunk.chapter,
                    section=chunk.section,
                    article=chunk.article,
                    heading=chunk.heading,
                    content=chunk.content,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                )
            )
    # 只记录耗时与数量，不记录用户的问题
    logger.info(
        "search_completed: versions=%d hits=%d retrieval_ms=%.0f total_ms=%.0f",
        len(version_ids),
        len(results),
        retrieved_ms,
        (time.perf_counter() - started) * 1000,
    )
    return SearchResponse(query=payload.query, results=results)

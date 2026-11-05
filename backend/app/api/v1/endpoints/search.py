from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_current_user
from app.db.session import get_db
from app.rag.indexing import get_query_embedding, get_vector_store
from app.rag.retrieval import HybridRetriever
from app.schemas.search import SearchRequest, SearchResponse
from app.services import search as search_service

router = APIRouter(dependencies=[Depends(require_current_user)])


def get_retriever() -> HybridRetriever:
    return HybridRetriever(get_query_embedding(), get_vector_store())


@router.post(
    "",
    response_model=SearchResponse,
    summary="在已发布制度中检索条文",
    description=(
        "稠密向量与 BM25 关键词两路检索，按倒数排名融合（RRF）。只返回已发布版本的条文，"
        "附制度、版本、条号与页码，供引用展示。用 POST 提交，问题不会出现在访问日志的网址中。"
    ),
)
async def search(
    payload: SearchRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    retriever: Annotated[HybridRetriever, Depends(get_retriever)],
) -> SearchResponse:
    response.headers["Cache-Control"] = "no-store"
    return await search_service.search(payload, session, retriever)

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_current_user
from app.db.session import get_db
from app.rag.indexing import get_query_embedding, get_vector_store
from app.rag.pipelines import PipelineRegistry, get_pipeline_registry
from app.rag.retrieval import HybridRetriever
from app.schemas.pipeline import PipelineResponse
from app.schemas.search import SearchRequest, SearchResponse
from app.services import pipelines as pipeline_service
from app.services import search as search_service

router = APIRouter(dependencies=[Depends(require_current_user)])


def get_retriever() -> HybridRetriever:
    return HybridRetriever(get_query_embedding(), get_vector_store())


def get_pipelines(
    retriever: Annotated[HybridRetriever, Depends(get_retriever)],
) -> PipelineRegistry:
    return get_pipeline_registry().with_default_retrieval(retriever)


@router.get(
    "/pipelines", response_model=list[PipelineResponse], summary="可选的检索方案"
)
async def list_pipelines(
    response: Response,
    pipelines: Annotated[PipelineRegistry, Depends(get_pipelines)],
) -> list[PipelineResponse]:
    response.headers["Cache-Control"] = "no-store"
    return pipeline_service.list_pipelines(pipelines)


@router.post(
    "",
    response_model=SearchResponse,
    summary="在已发布制度中检索条文",
    description=(
        "按所选方案检索，各路结果按倒数排名融合（RRF）。只返回已发布版本的内容，"
        "附制度、版本与页码，供引用展示。用 POST 提交，问题不会出现在访问日志的网址中。"
    ),
)
async def search(
    payload: SearchRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db)],
    pipelines: Annotated[PipelineRegistry, Depends(get_pipelines)],
) -> SearchResponse:
    response.headers["Cache-Control"] = "no-store"
    return await search_service.search(payload, session, pipelines)

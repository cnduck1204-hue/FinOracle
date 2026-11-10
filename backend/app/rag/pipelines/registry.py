"""可选的 Pipeline。新增方案时在 build_pipelines 中组合入库和检索两部分。"""

from functools import lru_cache

from app.core.config import (
    ParsingSettings,
    get_embedding_settings,
    get_parsing_settings,
    get_vector_store_settings,
    get_visual_settings,
)
from app.models.document import DEFAULT_INGEST
from app.rag.indexing import (
    EmbeddingProvider,
    MilvusVectorStore,
    VectorStore,
    get_query_embedding,
    get_vector_store,
)
from app.rag.pipelines.base import IngestPipeline, Pipeline, RetrievalPipeline
from app.rag.pipelines.errors import pipeline_not_found
from app.rag.pipelines.pdf_blocks import PdfBlockParser
from app.rag.pipelines.structured import StructuredParser
from app.rag.pipelines.text_index import TextIndexer, plain_entries
from app.rag.pipelines.visual import (
    PageVectorStore,
    VisualEmbeddingProvider,
    VisualHybridRetriever,
    VisualIndexer,
)
from app.rag.retrieval import HybridRetriever

STRUCTURED_PIPELINE = "structured-hybrid"
VISUAL_PIPELINE = "pdf-block-visual"
BLOCK_INGEST = "pdf-block"


class PipelineRegistry:
    def __init__(self, pipelines: list[Pipeline], *, default: str) -> None:
        self._pipelines = {pipeline.name: pipeline for pipeline in pipelines}
        self._default = default
        self._ingests = {
            pipeline.ingest.name: pipeline.ingest for pipeline in pipelines
        }

    @property
    def default(self) -> Pipeline:
        return self._pipelines[self._default]

    def all(self) -> list[Pipeline]:
        return list(self._pipelines.values())

    def get(self, name: str | None) -> Pipeline:
        pipeline = self._pipelines.get(name or self._default)
        if pipeline is None:
            raise pipeline_not_found()
        return pipeline

    def ingest(self, name: str | None) -> IngestPipeline:
        ingest = self._ingests.get(name or "")
        if ingest is None:
            raise pipeline_not_found()
        return ingest

    def with_default_retrieval(
        self, retrieval: RetrievalPipeline
    ) -> "PipelineRegistry":
        pipelines = [
            pipeline.with_retrieval(retrieval)
            if pipeline.name == self._default
            else pipeline
            for pipeline in self._pipelines.values()
        ]
        return PipelineRegistry(pipelines, default=self._default)


def build_pipelines(
    *,
    parsing: ParsingSettings,
    embedding: EmbeddingProvider,
    store: VectorStore,
    block_store: VectorStore,
    visual: VisualEmbeddingProvider,
    pages: PageVectorStore,
) -> PipelineRegistry:
    structured = Pipeline(
        name=STRUCTURED_PIPELINE,
        label="条文结构化检索",
        description="按条文切片，支持 OCR 和多种格式，向量与关键词两路融合；引用精确到条号。",
        ingest=IngestPipeline(
            DEFAULT_INGEST,
            StructuredParser(parsing),
            (TextIndexer(embedding, store),),
        ),
        retrieval=HybridRetriever(embedding, store),
    )
    block_visual = Pipeline(
        name=VISUAL_PIPELINE,
        label="版面视觉检索",
        description="按 PDF 文本块切分并保留坐标，向量、关键词与页面视觉三路融合；不做 OCR。",
        ingest=IngestPipeline(
            BLOCK_INGEST,
            PdfBlockParser(),
            (
                TextIndexer(embedding, block_store, entries=plain_entries),
                VisualIndexer(visual, pages),
            ),
        ),
        retrieval=VisualHybridRetriever(embedding, block_store, visual, pages),
        unavailable_reason=None if visual.configured else "未配置视觉编码服务",
    )
    return PipelineRegistry([structured, block_visual], default=STRUCTURED_PIPELINE)


def create_pipelines(
    embedding: EmbeddingProvider, store: VectorStore
) -> PipelineRegistry:
    """按配置创建全部 Pipeline；只建立对象，首次使用时才连接外部服务。"""
    vector_settings = get_vector_store_settings()
    embedding_settings = get_embedding_settings()
    visual_settings = get_visual_settings()
    return build_pipelines(
        parsing=get_parsing_settings(),
        embedding=embedding,
        store=store,
        block_store=MilvusVectorStore(
            vector_settings,
            model=embedding_settings.embedding_model,
            dimension=embedding_settings.embedding_dimension,
            namespace=BLOCK_INGEST,
        ),
        visual=VisualEmbeddingProvider(visual_settings),
        pages=PageVectorStore(
            vector_settings,
            namespace=BLOCK_INGEST,
            model=visual_settings.visual_model,
            dimension=visual_settings.visual_dimension,
        ),
    )


@lru_cache
def get_pipeline_registry() -> PipelineRegistry:
    """API 进程内共用；worker 用 create_pipelines 创建自己的实例。"""
    return create_pipelines(get_query_embedding(), get_vector_store())

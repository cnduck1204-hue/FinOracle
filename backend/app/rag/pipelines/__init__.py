"""可组合的检索方案，见 base.py 的说明。"""

from app.rag.pipelines.base import (
    IndexTarget,
    IngestPipeline,
    ParsedUnits,
    Pipeline,
    RetrievalPipeline,
    SourceFile,
    StoredUnit,
)
from app.rag.pipelines.errors import pipeline_unavailable, source_unsupported
from app.rag.pipelines.registry import (
    BLOCK_INGEST,
    STRUCTURED_PIPELINE,
    VISUAL_PIPELINE,
    PipelineRegistry,
    build_pipelines,
    create_pipelines,
    get_pipeline_registry,
)

__all__ = [
    "BLOCK_INGEST",
    "STRUCTURED_PIPELINE",
    "VISUAL_PIPELINE",
    "IndexTarget",
    "IngestPipeline",
    "ParsedUnits",
    "Pipeline",
    "PipelineRegistry",
    "RetrievalPipeline",
    "SourceFile",
    "StoredUnit",
    "build_pipelines",
    "create_pipelines",
    "get_pipeline_registry",
    "pipeline_unavailable",
    "source_unsupported",
]

"""可组合的检索方案，见 base.py 的说明。"""

from app.rag.pipelines.base import (
    IngestPipeline,
    Pipeline,
)
from app.rag.pipelines.errors import pipeline_unavailable
from app.rag.pipelines.registry import (
    BLOCK_INGEST,
    IMAGE_INGEST,
    IMAGE_PIPELINE,
    STRUCTURED_PIPELINE,
    VISUAL_PIPELINE,
    PipelineRegistry,
    build_pipelines,
    create_pipelines,
    get_pipeline_registry,
)

__all__ = [
    "BLOCK_INGEST",
    "IMAGE_INGEST",
    "IMAGE_PIPELINE",
    "STRUCTURED_PIPELINE",
    "VISUAL_PIPELINE",
    "IngestPipeline",
    "Pipeline",
    "PipelineRegistry",
    "build_pipelines",
    "create_pipelines",
    "get_pipeline_registry",
    "pipeline_unavailable",
]

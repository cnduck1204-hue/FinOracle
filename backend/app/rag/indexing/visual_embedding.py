"""视觉编码客户端，调用独立服务生成页面和问题的多向量表示。"""

import logging
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import VisualSettings
from app.rag.indexing.errors import visual_unavailable

logger = logging.getLogger(__name__)

MultiVector = list[list[float]]
# Milvus 数组字段的容量上限，编码校验与存储结构共用
MAX_PAGE_VECTORS = 4096


class VisualEmbeddingProvider:
    """调用视觉编码服务；未配置地址时 configured 为 False，调用时报错。"""

    def __init__(
        self, settings: VisualSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.model = settings.visual_model
        self.dimension = settings.visual_dimension
        self.batch_pages = settings.visual_batch_pages
        self.render_dpi = settings.visual_render_dpi
        self._url = settings.visual_service_url
        self._timeout = settings.visual_timeout_seconds
        self._client = client

    @property
    def configured(self) -> bool:
        return self._url is not None

    @property
    def model_id(self) -> str:
        return f"{self.model}:{self.dimension}"

    async def encode_images(self, images: Sequence[bytes]) -> list[MultiVector]:
        """images 为 PNG；返回每页的图块向量。"""
        files = [
            ("images", (f"{index}.png", data, "image/png"))
            for index, data in enumerate(images)
        ]
        vectors = await self._post("/v1/images", files=files)
        if len(vectors) != len(images):
            raise visual_unavailable("视觉编码结果的数量与请求不一致")
        for page in vectors:
            self._check(page)
        return vectors

    async def encode_query(self, query: str) -> MultiVector:
        (vectors,) = await self._post("/v1/queries", json={"queries": [query]})
        self._check(vectors)
        return vectors

    def _check(self, vectors: MultiVector) -> None:
        if not vectors or any(len(vector) != self.dimension for vector in vectors):
            raise visual_unavailable("视觉编码结果的维度与配置不一致")
        if len(vectors) > MAX_PAGE_VECTORS:
            raise visual_unavailable("视觉编码结果的向量数超过上限")

    async def _post(self, path: str, **kwargs: Any) -> list[MultiVector]:
        if self._url is None:
            raise visual_unavailable("未配置 VISUAL_SERVICE_URL，无法使用视觉通道")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        try:
            response = await self._client.post(f"{self._url}{path}", **kwargs)
        except httpx.HTTPError as error:
            logger.warning("visual_request_failed: %s", type(error).__name__)
            raise visual_unavailable() from error
        if response.status_code != 200:
            logger.warning("visual_service_error: %s", response.status_code)
            raise visual_unavailable()
        try:
            return response.json()["vectors"]
        except (ValueError, KeyError, TypeError) as error:
            raise visual_unavailable("视觉编码服务返回了无法识别的结果") from error

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

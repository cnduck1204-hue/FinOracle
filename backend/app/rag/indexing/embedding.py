"""EmbeddingProvider：把文本转换为稠密向量。离线入库与在线查询必须使用同一实现、模型和维度。"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

import httpx

from app.core.config import EmbeddingSettings
from app.rag.indexing.errors import (
    embedding_auth_failed,
    embedding_rejected,
    embedding_unavailable,
)

logger = logging.getLogger(__name__)

Vector = list[float]


class EmbeddingProvider(ABC):
    name: str
    model: str
    dimension: int
    # 单次请求的最多文本数
    max_batch_size: int

    @property
    def model_id(self) -> str:
        """记录在版本 index_model 上的标识；不同时须重建索引。"""
        return f"{self.model}:{self.dimension}"

    @abstractmethod
    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """为待入库的文本生成向量，顺序与输入一致；texts 不超过 max_batch_size。"""

    async def embed_query(self, text: str) -> Vector:
        """为检索问题生成向量。DashScope 兼容接口不区分查询与文档（text_type 被忽略，
        实测两者向量完全相同），因此与 embed_documents 走同一请求。"""
        (vector,) = await self.embed_documents([text])
        return vector

    async def aclose(self) -> None:
        """释放连接；进程退出时调用。"""


class DashScopeEmbedding(EmbeddingProvider):
    """阿里云百炼（DashScope）的 OpenAI 兼容接口，默认 text-embedding-v4。"""

    name = "dashscope"

    def __init__(
        self, settings: EmbeddingSettings, client: httpx.AsyncClient | None = None
    ) -> None:
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension
        self.max_batch_size = settings.embedding_batch_size
        self._url = f"{settings.embedding_base_url}/embeddings"
        self._key = settings.dashscope_api_key
        self._timeout = settings.embedding_timeout_seconds
        self._client = client

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        if len(texts) > self.max_batch_size:
            raise ValueError(f"每次最多 {self.max_batch_size} 条文本")
        if self._key is None:
            raise embedding_unavailable("未配置 DASHSCOPE_API_KEY，无法向量化")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, trust_env=False)
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._key.get_secret_value()}"},
                json={
                    "model": self.model,
                    "input": list(texts),
                    "dimensions": self.dimension,
                    "encoding_format": "float",
                },
            )
        except httpx.HTTPError as error:
            logger.warning("embedding_request_failed: %s", type(error).__name__)
            raise embedding_unavailable() from error
        return self._vectors(response, len(texts))

    def _vectors(self, response: httpx.Response, count: int) -> list[Vector]:
        status = response.status_code
        if status in (401, 403):
            raise embedding_auth_failed()
        if status == 429 or status >= 500:
            logger.warning("embedding_service_error: %s", status)
            raise embedding_unavailable()
        if status >= 400:
            # 只记录状态码；响应正文可能回显输入文本
            logger.warning("embedding_request_rejected: %s", status)
            raise embedding_rejected(f"向量化请求被拒绝（HTTP {status}）")
        try:
            items = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
        except (ValueError, KeyError, TypeError) as error:
            raise embedding_unavailable("向量化服务返回了无法识别的结果") from error
        if len(vectors) != count or any(len(v) != self.dimension for v in vectors):
            raise embedding_unavailable("向量化结果的数量或维度与请求不一致")
        return vectors

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

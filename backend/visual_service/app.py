"""视觉编码服务：把页面图像和查询编码为 ColPali 系列的多向量。

单独运行，API 和 worker 不依赖 torch；有 GPU 的机器可以只运行这个服务。模型在首次请求时
加载，推理串行执行。
"""

import io
import logging
import os
import threading
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("VISUAL_MODEL", "vidore/colSmol-500M")
DEVICE = os.environ.get("VISUAL_DEVICE", "auto")
MAX_IMAGES = 16

# 顺序保证更具体的名字优先（colqwen2.5 先于 colqwen2）
_FAMILIES = (
    ("colqwen2.5", ("ColQwen2_5", "ColQwen2_5_Processor")),
    ("colqwen2_5", ("ColQwen2_5", "ColQwen2_5_Processor")),
    ("colqwen2", ("ColQwen2", "ColQwen2Processor")),
    ("colpali", ("ColPali", "ColPaliProcessor")),
    ("colsmol", ("ColIdefics3", "ColIdefics3Processor")),
    ("colidefics3", ("ColIdefics3", "ColIdefics3Processor")),
)


class Encoder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self.device = ""

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> None:
        import torch
        from colpali_engine import models as registry
        from colpali_engine.utils.torch_utils import get_torch_device

        lowered = MODEL_NAME.lower()
        classes = next(
            (names for needle, names in _FAMILIES if needle in lowered), None
        )
        if classes is None:
            raise RuntimeError(f"不支持的视觉模型：{MODEL_NAME}")
        model_class, processor_class = (getattr(registry, name) for name in classes)
        self.device = str(get_torch_device(DEVICE))
        dtype = torch.bfloat16 if "cuda" in self.device else torch.float32
        self._model = model_class.from_pretrained(
            MODEL_NAME, torch_dtype=dtype, device_map=self.device
        ).eval()
        self._processor = processor_class.from_pretrained(MODEL_NAME)
        self._torch = torch
        logger.info("visual_model_loaded: %s on %s", MODEL_NAME, self.device)

    def _run(self, batch: Any) -> list[list[float]]:
        with self._torch.no_grad():
            embeddings = self._model(**batch.to(self.device))
        return [row.float().tolist() for row in embeddings[0]]

    def images(self, images: list[bytes]) -> list[list[list[float]]]:
        from PIL import Image

        with self._lock:
            if not self.loaded:
                self._load()
            result = []
            for data in images:
                with Image.open(io.BytesIO(data)) as image:
                    batch = self._processor.process_images([image.convert("RGB")])
                result.append(self._run(batch))
            return result

    def queries(self, queries: list[str]) -> list[list[list[float]]]:
        with self._lock:
            if not self.loaded:
                self._load()
            return [
                self._run(self._processor.process_queries([query])) for query in queries
            ]


encoder = Encoder()
app = FastAPI(title="FinOracle Visual Encoder", version="1.0.0")


class QueriesRequest(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=16)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "loaded": encoder.loaded,
        "device": encoder.device or None,
    }


@app.post("/v1/images")
def encode_images(images: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=413, detail="too many images")
    return {"vectors": encoder.images([image.file.read() for image in images])}


@app.post("/v1/queries")
def encode_queries(request: QueriesRequest) -> dict[str, Any]:
    return {"vectors": encoder.queries(request.queries)}

"""RapidOCR：PaddleOCR 模型 + ONNX Runtime，属于可选依赖 ocr。"""

from importlib.util import find_spec
from typing import Any

from app.rag.parsing.errors import ocr_unavailable
from app.rag.parsing.ocr.base import OcrLine, OCRProvider
from app.rag.parsing.options import DEFAULT_MIN_OCR_CONFIDENCE


class RapidOcrProvider(OCRProvider):
    name = "rapidocr"

    def __init__(
        self,
        min_confidence: float = DEFAULT_MIN_OCR_CONFIDENCE,
        engine: Any = None,
    ):
        super().__init__(min_confidence)
        self._engine = _create_engine() if engine is None else engine

    @classmethod
    def available(cls) -> bool:
        return find_spec("rapidocr") is not None

    def read_lines(self, image: bytes) -> list[OcrLine]:
        result = self._engine(image)
        texts = result.txts or ()
        scores = result.scores or ()
        return [
            OcrLine(text, float(score))
            for text, score in zip(texts, scores, strict=True)
        ]


def _create_engine() -> Any:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise ocr_unavailable("未安装 OCR 依赖，请执行 uv sync --extra ocr") from exc
    # 引擎自身不按置信度过滤，统一由 OCRProvider.recognize 按 min_confidence 过滤
    return RapidOCR(params={"Global.log_level": "warning", "Global.text_score": 0})

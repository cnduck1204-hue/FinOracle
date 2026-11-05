"""OCR Provider 基类。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.rag.parsing.options import DEFAULT_MIN_OCR_CONFIDENCE


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float


class OCRProvider(ABC):
    """识别一张 PNG 图片中的文字；子类只需把具体引擎的结果转换为 OcrLine。"""

    name: str

    def __init__(self, min_confidence: float = DEFAULT_MIN_OCR_CONFIDENCE):
        self.min_confidence = min_confidence

    @classmethod
    def available(cls) -> bool:
        """所需依赖是否已安装；auto 模式按登记顺序选择第一个可用实现。"""
        return True

    def recognize(self, image: bytes) -> list[OcrLine]:
        """按阅读顺序返回各行，去掉空行和低于置信度阈值的行。"""
        return [
            line
            for line in self.read_lines(image)
            if line.text.strip() and line.confidence >= self.min_confidence
        ]

    @abstractmethod
    def read_lines(self, image: bytes) -> list[OcrLine]:
        """调用具体引擎，返回未经过滤的识别行。"""

"""OCR Provider：按名称创建。

新增实现：在本目录添加模块，继承 OCRProvider 并实现 read_lines（依赖可选时
同时覆盖 available），再加入 OCR_PROVIDERS。
"""

from app.rag.parsing.ocr.base import OcrLine, OCRProvider
from app.rag.parsing.ocr.rapid_ocr import RapidOcrProvider
from app.rag.parsing.ocr.tesseract import TesseractOcrProvider
from app.rag.parsing.options import AUTO, DEFAULT_MIN_OCR_CONFIDENCE

NO_OCR = "none"
# auto 按此顺序选择已安装的实现：RapidOCR 中文识别更准，Tesseract 更轻量，Docker 镜像内置
OCR_PROVIDERS: dict[str, type[OCRProvider]] = {
    RapidOcrProvider.name: RapidOcrProvider,
    TesseractOcrProvider.name: TesseractOcrProvider,
}
OCR_CHOICES = (AUTO, *OCR_PROVIDERS, NO_OCR)


def create_ocr(
    name: str = AUTO, min_confidence: float = DEFAULT_MIN_OCR_CONFIDENCE
) -> OCRProvider | None:
    """auto 选择第一个已安装的实现；none 或均未安装时返回 None，结果标记为待 OCR。"""
    if name == NO_OCR:
        return None
    if name == AUTO:
        provider = next((p for p in OCR_PROVIDERS.values() if p.available()), None)
        return provider(min_confidence) if provider else None
    if name not in OCR_PROVIDERS:
        raise ValueError(f"未知的 OCR Provider：{name}")
    return OCR_PROVIDERS[name](min_confidence)


__all__ = [
    "NO_OCR",
    "OCR_CHOICES",
    "OCR_PROVIDERS",
    "OCRProvider",
    "OcrLine",
    "create_ocr",
]

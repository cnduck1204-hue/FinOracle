"""各阶段之间传递的数据结构与类型。"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

BlockKind = Literal["heading", "paragraph", "table"]
BlockSource = Literal["text", "ocr"]
OcrReason = Literal["scanned_page", "image_region", "embedded_image", "image_file"]
# 整页识别的图片；识别不出文字时需要提示
WHOLE_PAGE_OCR_REASONS: tuple[OcrReason, ...] = ("scanned_page", "image_file")
# completed：已完成；needs_ocr：仍有图片未识别；empty：没有可用文本
ParseStatus = Literal["completed", "needs_ocr", "empty"]


class TextBlock(BaseModel):
    """按阅读顺序排列的一段文本；page 为 1 起算的物理页序号，无分页格式为 None。"""

    model_config = ConfigDict(frozen=True)

    page: int | None
    order: int
    kind: BlockKind
    text: str
    source: BlockSource = "text"
    # 标题级别：0 为文档标题，1 起为各级标题；正文为 None
    level: int | None = None


@dataclass(frozen=True, slots=True)
class OcrTarget:
    """待识别的 PNG 图片；order 决定识别结果插回正文的位置。"""

    page: int | None
    order: int
    reason: OcrReason
    image: bytes


@dataclass(slots=True)
class ParsedDocument:
    """Parser 的输出：文本块与待 OCR 图片按添加顺序共用一个阅读顺序。"""

    page_count: int | None
    blocks: list[TextBlock] = field(default_factory=list)
    ocr_targets: list[OcrTarget] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_text(
        self,
        text: str,
        *,
        page: int | None = None,
        kind: BlockKind = "paragraph",
        level: int | None = None,
    ) -> None:
        self.blocks.append(
            TextBlock(
                page=page, order=self._next_order(), kind=kind, text=text, level=level
            )
        )

    def add_image(
        self, image: bytes, *, reason: OcrReason, page: int | None = None
    ) -> None:
        self.ocr_targets.append(OcrTarget(page, self._next_order(), reason, image))

    def _next_order(self) -> int:
        return len(self.blocks) + len(self.ocr_targets)


class Chunk(BaseModel):
    """可检索的文本片段。"""

    ordinal: int
    chapter: str = ""
    section: str = ""
    # 条号（第七条）或通知的一级序号（一、）
    article: str = ""
    # 按标题切片时的标题路径，如“总则 / 细则”
    heading: str = ""
    # 同一条或同一段过长被拆分时的序号与总数
    part: int = 1
    part_count: int = 1
    content: str
    page_start: int | None
    page_end: int | None
    sources: list[BlockSource]


class ParseStats(BaseModel):
    text_blocks: int
    ocr_targets: int
    ocr_blocks: int
    ocr_pending: int
    # 清洗时删除的页码、页眉页脚和重复内容行数
    removed_lines: int
    chunks: int


class ParseResult(BaseModel):
    """一次解析的完整结果，也是解析接口与 CLI 的输出格式。"""

    file_name: str
    file_type: str
    sha256: str
    size_bytes: int
    page_count: int | None
    status: ParseStatus
    chunker: str
    # 先转换为 PDF 的格式记录所用转换器，其余为 None
    converter: str | None
    ocr_provider: str | None
    # 清洗后的文本块；调用方未要求时为 None
    blocks: list[TextBlock] | None
    chunks: list[Chunk]
    stats: ParseStats
    warnings: list[str]

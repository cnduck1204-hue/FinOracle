"""解析流水线：按需格式转换 → 文档解析 → 按需 OCR → 文本汇合清洗 → 切片。"""

import hashlib
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path

from app.rag.parsing.chunkers import create_chunker
from app.rag.parsing.cleaning import merge_blocks
from app.rag.parsing.converters import (
    CONVERTIBLE_TYPES,
    ConverterProvider,
    check_signature,
)
from app.rag.parsing.errors import (
    conversion_unavailable,
    file_too_large,
    invalid_document,
    unsupported_type,
)
from app.rag.parsing.models import (
    WHOLE_PAGE_OCR_REASONS,
    Chunk,
    OcrTarget,
    ParseResult,
    ParseStats,
    ParseStatus,
    TextBlock,
)
from app.rag.parsing.ocr import OCRProvider
from app.rag.parsing.options import AUTO, MIB, ParseOptions
from app.rag.parsing.parsers import PARSER_TYPES, get_parser

logger = logging.getLogger(__name__)

SUPPORTED_TYPES = PARSER_TYPES | CONVERTIBLE_TYPES

# 进度回调：阶段名、当前进度、总量（未知为 None）。阶段依次为 converting（仅需转换的格式）、
# parsing、ocr（逐张图片报告）、chunking。回调在解析线程中执行，应快速返回
ProgressCallback = Callable[[str, int, int | None], None]


def _ignore_progress(stage: str, current: int, total: int | None) -> None:
    pass


def parse_file(
    path: Path,
    *,
    converter: ConverterProvider | None = None,
    ocr: OCRProvider | None = None,
    chunker: str = AUTO,
    options: ParseOptions | None = None,
    progress: ProgressCallback | None = None,
) -> ParseResult:
    options = options or ParseOptions()
    report = progress or _ignore_progress
    file_type = path.suffix.lower().lstrip(".")
    if file_type not in SUPPORTED_TYPES:
        raise unsupported_type(file_type)
    size = _checked_size(path, options)
    converted = file_type in CONVERTIBLE_TYPES

    with tempfile.TemporaryDirectory() as workdir:
        source = path
        if converted:
            report("converting", 0, None)
            source = convert_to_pdf(path, converter, Path(workdir))
        report("parsing", 0, None)
        parser = get_parser(source.suffix.lower().lstrip("."))
        document = parser.parse(source, options)
    ocr_blocks, pending, ocr_warnings = _run_ocr(document.ocr_targets, ocr, report)
    report("chunking", 0, None)
    blocks, removed = merge_blocks(document.blocks, ocr_blocks)
    splitter = create_chunker(chunker, blocks, options)
    chunks = splitter.split(blocks)

    return ParseResult(
        file_name=path.name,
        file_type=file_type,
        sha256=_sha256(path),
        size_bytes=size,
        page_count=document.page_count,
        status=_status(pending, chunks),
        chunker=splitter.name,
        converter=converter.name if converted else None,
        ocr_provider=ocr.name if ocr else None,
        blocks=blocks,
        chunks=chunks,
        stats=ParseStats(
            text_blocks=len(document.blocks),
            ocr_targets=len(document.ocr_targets),
            ocr_blocks=len(ocr_blocks),
            ocr_pending=pending,
            removed_lines=removed,
            chunks=len(chunks),
        ),
        warnings=document.warnings + ocr_warnings,
    )


def convert_to_pdf(
    path: Path, converter: ConverterProvider | None, workdir: Path
) -> Path:
    """把 .doc / .rtf / .odt 转换为 workdir 中的 PDF；先核对文件头，内容与扩展名不符时拒绝。"""
    file_type = path.suffix.lower().lstrip(".")
    if file_type not in CONVERTIBLE_TYPES:
        raise unsupported_type(file_type)
    check_signature(path, file_type)
    if converter is None:
        raise conversion_unavailable("未配置格式转换器，无法解析该格式")
    return converter.to_pdf(path, workdir)


def _checked_size(path: Path, options: ParseOptions) -> int:
    size = path.stat().st_size
    if size == 0:
        raise invalid_document("文件为空")
    if size > options.max_file_bytes:
        raise file_too_large(options.max_file_bytes)
    return size


def _run_ocr(
    targets: list[OcrTarget], ocr: OCRProvider | None, report: ProgressCallback
) -> tuple[list[TextBlock], int, list[str]]:
    """识别全部图片，返回 OCR 文本块、仍待识别的图片数和警告。"""
    if not targets:
        return [], 0, []
    if ocr is None:
        return [], len(targets), [f"{_describe(targets)}待 OCR，未配置 OCR Provider"]

    blocks: list[TextBlock] = []
    failed: list[OcrTarget] = []
    blank: list[OcrTarget] = []
    for index, target in enumerate(targets):
        report("ocr", index, len(targets))
        try:
            lines = ocr.recognize(target.image)
        except Exception:
            # 单张图片识别失败不影响其余内容，该图片计为待 OCR
            logger.warning("OCR failed for %s", target.reason, exc_info=True)
            failed.append(target)
            continue
        text = "\n".join(line.text for line in lines)
        if text.strip():
            blocks.append(
                TextBlock(
                    page=target.page,
                    order=target.order,
                    kind="paragraph",
                    text=text,
                    source="ocr",
                )
            )
        elif target.reason in WHOLE_PAGE_OCR_REASONS:
            blank.append(target)

    report("ocr", len(targets), len(targets))
    warnings = []
    if failed:
        warnings.append(f"{_describe(failed)}OCR 失败")
    if blank:
        warnings.append(f"{_describe(blank)}未识别出文字")
    return blocks, len(failed), warnings


def _status(pending: int, chunks: list[Chunk]) -> ParseStatus:
    if pending:
        return "needs_ocr"
    return "completed" if chunks else "empty"


def _describe(targets: list[OcrTarget]) -> str:
    """把图片列表描述为“第 2 页、第 3 页、1 张内嵌图片”。"""
    pages = sorted({target.page for target in targets if target.page is not None})
    parts = [f"第 {page} 页" for page in pages]
    unpaged = sum(1 for target in targets if target.page is None)
    if unpaged:
        parts.append(f"{unpaged} 张内嵌图片")
    return "、".join(parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(MIB), b""):
            digest.update(block)
    return digest.hexdigest()

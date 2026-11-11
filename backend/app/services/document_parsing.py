"""文档解析服务，适配上传请求、管理临时文件并裁剪预览输出。"""

import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import ParsingSettings, get_parsing_settings
from app.rag.parsing import SUPPORTED_TYPES, ParseResult
from app.rag.parsing import parse_file as run_pipeline
from app.rag.parsing.errors import file_too_large, unsupported_type
from app.rag.parsing.options import MIB
from app.rag.parsing.parameters import ParseRequest, parse_options
from app.rag.parsing.providers import create_document_converter, ocr_provider

# 解析和 OCR 占用大量 CPU，且 OCR 引擎实例在请求间共享，接口同一时间只解析一个文档
_PARSE_LOCK = threading.Lock()


def parse_document(
    path: Path, request: ParseRequest, settings: ParsingSettings | None = None
) -> ParseResult:
    return _parse(path, request, settings or get_parsing_settings())


async def parse_upload(
    upload: UploadFile, request: ParseRequest, settings: ParsingSettings
) -> ParseResult:
    """上传内容写入临时文件后解析，结果中的 file_name 为原始文件名。"""
    file_name = Path(upload.filename or "").name
    suffix = Path(file_name).suffix.lower()
    # 不支持的类型和超限文件在复制前拒绝
    if suffix.lstrip(".") not in SUPPORTED_TYPES:
        raise unsupported_type(suffix.lstrip("."))
    max_bytes = parse_options(request).max_file_bytes
    if upload.size is not None and upload.size > max_bytes:
        raise file_too_large(max_bytes)

    with tempfile.TemporaryDirectory() as workdir:
        path = Path(workdir) / f"upload{suffix}"
        await run_in_threadpool(_save, upload, path)
        result = await run_in_threadpool(_parse_exclusively, path, request, settings)
    return result.model_copy(update={"file_name": file_name})


def _parse(path: Path, request: ParseRequest, settings: ParsingSettings) -> ParseResult:
    result = run_pipeline(
        path,
        converter=create_document_converter(settings),
        ocr=ocr_provider(request.ocr),
        chunker=request.chunker,
        options=parse_options(request),
    )
    if request.include_blocks:
        return result
    return result.model_copy(update={"blocks": None})


def _parse_exclusively(
    path: Path, request: ParseRequest, settings: ParsingSettings
) -> ParseResult:
    with _PARSE_LOCK:
        return _parse(path, request, settings)


def _save(upload: UploadFile, path: Path) -> None:
    upload.file.seek(0)
    with path.open("wb") as output:
        shutil.copyfileobj(upload.file, output, MIB)

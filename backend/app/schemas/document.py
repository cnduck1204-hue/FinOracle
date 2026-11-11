"""文档解析的 HTTP 表单，参数校验复用解析层的模型。"""

from fastapi import UploadFile
from pydantic import Field

from app.rag.parsing.parameters import (
    MAX_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
    ChunkerChoice,
    OcrChoice,
    ParseOptionsInput,
    ParseRequest,
)


class ParseUploadForm(ParseRequest):
    """解析接口的 multipart 表单：上传文件与解析参数。"""

    # format=binary 兼容只识别 OpenAPI 3.0 写法的工具，导入后显示为文件字段
    file: UploadFile = Field(
        description="PDF、Word（.docx/.doc）、RTF、ODT、TXT/Markdown 或图片",
        json_schema_extra={"format": "binary"},
    )


__all__ = [
    "MAX_CHUNK_CHARS",
    "MIN_CHUNK_CHARS",
    "ChunkerChoice",
    "OcrChoice",
    "ParseOptionsInput",
    "ParseRequest",
    "ParseUploadForm",
]

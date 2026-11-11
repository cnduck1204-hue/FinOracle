"""文档解析的请求参数：CLI、预览接口、导入与 worker 共用同一套取值范围和校验。"""

from typing import Literal, Self

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.parsing import AUTO, CHUNKER_CHOICES, OCR_CHOICES
from app.rag.parsing.options import DEFAULT_CHUNK_OVERLAP, DEFAULT_MAX_CHUNK_CHARS

# 由登记表生成，新增 Provider 后自动出现在接口枚举和 CLI 选项中
OcrChoice = Literal[OCR_CHOICES]
ChunkerChoice = Literal[CHUNKER_CHOICES]
MIN_CHUNK_CHARS = 100
MAX_CHUNK_CHARS = 4000


class ParseOptionsInput(BaseModel):
    """解析参数：入库版本保存这四项，重新解析时可整体替换。"""

    model_config = ConfigDict(extra="forbid")

    ocr: OcrChoice = Field(
        default=AUTO, description="OCR 实现；auto 选择已安装的实现，none 不识别图片"
    )
    chunker: ChunkerChoice = Field(
        default=AUTO,
        description="切片方式；auto 依次检测“第X条”“一、”，都没有时按长度切片",
    )
    max_chunk_chars: int = Field(
        default=DEFAULT_MAX_CHUNK_CHARS,
        ge=MIN_CHUNK_CHARS,
        le=MAX_CHUNK_CHARS,
        description="单个切片的最大字数",
    )
    chunk_overlap: int = Field(
        default=DEFAULT_CHUNK_OVERLAP,
        ge=0,
        description="按长度切片时相邻切片的重叠字数",
    )

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.chunk_overlap >= self.max_chunk_chars:
            raise ValueError("chunk_overlap 必须小于 max_chunk_chars")
        return self

    def stored(self) -> dict[str, object]:
        """保存到版本 parse_options 的内容。"""
        return self.model_dump(include=set(ParseOptionsInput.model_fields))


class ParseRequest(ParseOptionsInput):
    """解析预览与命令行的参数。"""

    include_blocks: bool = Field(
        default=False, description="是否在结果中返回清洗后的文本块"
    )


class ParseUploadForm(ParseRequest):
    """解析接口的 multipart 表单：上传文件与解析参数。"""

    # format=binary 兼容只识别 OpenAPI 3.0 写法的工具，导入后显示为文件字段
    file: UploadFile = Field(
        description="PDF、Word（.docx/.doc）、RTF、ODT、TXT/Markdown 或图片",
        json_schema_extra={"format": "binary"},
    )

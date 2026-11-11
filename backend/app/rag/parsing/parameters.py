"""预览、命令行和后台任务共用的解析参数与校验。"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.parsing.chunkers import CHUNKER_CHOICES
from app.rag.parsing.ocr import OCR_CHOICES
from app.rag.parsing.options import (
    AUTO,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MAX_CHUNK_CHARS,
    ParseOptions,
)

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


def parse_options(request: ParseRequest) -> ParseOptions:
    """把请求参数换成解析选项；输入限制与阈值使用默认值。"""
    return ParseOptions(
        max_chunk_chars=request.max_chunk_chars,
        chunk_overlap=request.chunk_overlap,
    )

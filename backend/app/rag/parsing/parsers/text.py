"""TXT / Markdown：按空行分段，Markdown 标题单独成块。"""

import re
from pathlib import Path

from app.rag.parsing.errors import invalid_document
from app.rag.parsing.models import ParsedDocument
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.parsers.base import ParserProvider

_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_ENCODINGS = ("utf-8-sig", "gb18030")


class TextParser(ParserProvider):
    file_types = frozenset({"txt", "md", "markdown"})

    def parse(self, path: Path, options: ParseOptions) -> ParsedDocument:
        text = decode_text(path.read_bytes())
        markdown = path.suffix.lower() in (".md", ".markdown")
        document = ParsedDocument(page_count=None)
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                document.add_text("\n".join(paragraph))
                paragraph.clear()

        for line in text.splitlines():
            heading = _MARKDOWN_HEADING.match(line) if markdown else None
            if heading:
                flush()
                document.add_text(
                    heading.group(2), kind="heading", level=len(heading.group(1))
                )
            elif line.strip():
                paragraph.append(line)
            else:
                flush()
        flush()
        return document


def decode_text(raw: bytes) -> str:
    if b"\x00" in raw:
        raise invalid_document("文本文件包含二进制内容")
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise invalid_document("无法识别文本编码，请使用 UTF-8 或 GB18030")

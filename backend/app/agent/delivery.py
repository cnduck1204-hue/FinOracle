import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.articles import article_label
from app.agent.citations import (
    CitationBuilder,
    EvidenceId,
    compute_confidence,
    insufficient,
)
from app.agent.context import ChatContext
from app.rag.parsing.text_utils import compact
from app.schemas.chat import ChatParagraph, ChatRequest, ChatResponse

_MARKER = re.compile(r"\[\[([^\[\]\n]{1,80})\]\]")
_SINGLE_MARKER = re.compile(r"(?<!\[)\[(e[0-9a-f]{16})\](?!\])")
_TOOL_TAG = re.compile(r"</?[|｜]{1,2}DSML")
_OPEN_MARKUP = re.compile(
    r"(?:\[\[[^\[\]\n]{0,80}\]?|\[(?:e[0-9a-f]{0,16})?"
    r"|</?(?:[|｜]{1,2}(?:D(?:S(?:M)?)?)?)?)\Z"
)
_CITATION_NUMBER = re.compile(r"(?<!\[)\[\s*\d+\s*\](?![\](])|［\s*\d+\s*］")
_FENCE = re.compile(r"^ {0,3}(\x60{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?<!\x60)(\x60+)(?!\x60).*?(?<!\x60)\1(?!\x60)")
_VALIDITY = re.compile(
    r"现行有效|仍(?:然)?有效|继续有效|已(?:经)?(?:废止|失效|生效|施行)|尚未生效|已被废止|"
    r"(?:自|于)[^。\n]{0,25}(?:生效|起执行|施行|开始适用)|"
    r"\b(?:in force|currently effective|has been repealed|became effective|"
    r"effective (?:from|on|as of))\b",
    re.IGNORECASE,
)
_EXCLUSIVE = re.compile(
    r"只有[^。\n]{0,55}才|仅限|唯有[^。\n]{0,55}才|"
    r"\b(?:only if|exclusively)\b",
    re.IGNORECASE,
)
_ARTICLE = re.compile(
    r"第\s*[0-9零〇一二两三四五六七八九十百千]+\s*条(?:之[0-9零〇一二两三四五六七八九十百千]+)?"
)
_DOCUMENT_NUMBER = re.compile(r"(?:第\s*\d+\s*号|[〔\[]\s*\d{4}\s*[〕\]]\s*\d+\s*号)")
_TABLE_SEPARATOR = re.compile(r"\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*")
_CELL_SEPARATOR = re.compile(r"(?<!\\)\|")
_LIST_ITEM = re.compile(r"^(\s*(?:[-+*]|\d+[.)])\s+)")
_EVIDENCE_GAP = "部分问题暂缺少可核对的制度依据。"


def _reject_tool_protocol(text: str) -> None:
    if _TOOL_TAG.search(text):
        raise ValueError("Tool protocol in assistant text")


def split_stream_text(text: str) -> tuple[str, str]:
    """Hide internal markers and retain any unfinished marker or tool tag."""
    _reject_tool_protocol(text)
    tail = _OPEN_MARKUP.search(text)
    visible = text[: tail.start()] if tail else text
    return _SINGLE_MARKER.sub("", _MARKER.sub("", visible)), tail[0] if tail else ""


@dataclass
class TurnEvidence:
    by_original: dict[EvidenceId, str] = field(default_factory=dict)
    by_alias: dict[str, EvidenceId] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)

    def _alias(self, original: EvidenceId) -> str:
        if original not in self.by_original:
            alias = f"e{uuid4().hex[:16]}"
            self.by_original[original] = alias
            self.by_alias[alias] = original
        return self.by_original[original]

    def record(self, result: dict) -> dict:
        if "evidence" in result:
            evidence = [
                {**source, "id": self._alias(source["id"])}
                for source in result["evidence"]
            ]
            self.sources.update({source["id"]: source for source in evidence})
            result = {**result, "evidence": evidence}
        if "evidence_ids" in result:
            result = {
                **result,
                "evidence_ids": [
                    self._alias(original) for original in result["evidence_ids"]
                ],
            }
        return result


@dataclass(frozen=True)
class CandidateBlock:
    markdown: str
    evidence_ids: tuple[str, ...]

    @property
    def is_table(self) -> bool:
        lines = self.markdown.splitlines()
        return len(lines) > 1 and _TABLE_SEPARATOR.fullmatch(lines[1]) is not None

    @property
    def preserves_structure(self) -> bool:
        return self.is_table or _LIST_ITEM.match(self.markdown) is not None

    def render(self, numbers: dict[str, int], versions: dict[str, str]) -> str:
        def number(text: str) -> str:
            return _MARKER.sub(lambda match: f"[{numbers[match[1]]}]", text)

        def version_text(text: str) -> str:
            markers = dict.fromkeys(
                _MARKER.findall(_prose(text, lambda value: value, keep_code=False))
            )
            return " ".join(
                f"{versions[marker]}[{numbers[marker]}]"
                for marker in markers
                if marker in versions
            )

        def table_cell(cell: str) -> str:
            if statement := version_text(cell):
                return " " + statement.replace("|", r"\|") + " "
            return _prose(cell, number)

        if not versions:
            return _prose(self.markdown, number)
        table = self.is_table
        lines = []
        for line in self.markdown.splitlines():
            if table:
                lines.append(
                    "|".join(table_cell(cell) for cell in _CELL_SEPARATOR.split(line))
                )
            elif (item := _LIST_ITEM.match(line)) and (statement := version_text(line)):
                lines.append(item[1] + statement)
            else:
                lines.append(_prose(line, number))
        return "\n".join(lines)


def _markdown_blocks(text: str) -> list[str]:
    blocks, lines = [], []
    fence = ""
    for line in text.strip("\r\n").splitlines():
        if match := _FENCE.match(line):
            mark = match[1]
            if not fence:
                if lines:
                    blocks.append("\n".join(lines))
                    lines = []
                fence = mark
            elif (
                mark[0] == fence[0]
                and len(mark) >= len(fence)
                and not line[match.end() :].strip()
            ):
                fence = ""
                lines.append(line)
                blocks.append("\n".join(lines))
                lines = []
                continue
        if not line.strip() and not fence:
            if lines:
                blocks.append("\n".join(lines))
                lines = []
        else:
            lines.append(line)
    if fence:
        raise ValueError("Incomplete Markdown fence")
    if lines:
        blocks.append("\n".join(lines))
    return blocks


def _prose(
    markdown: str, transform: Callable[[str], str], *, keep_code: bool = True
) -> str:
    if _FENCE.match(markdown):
        return markdown if keep_code else ""
    offset = 0
    parts = []
    for span in _INLINE_CODE.finditer(markdown):
        parts.append(transform(markdown[offset : span.start()]))
        if keep_code:
            parts.append(span[0])
        offset = span.end()
    parts.append(transform(markdown[offset:]))
    return "".join(parts)


def _normalize_markers(prose: str) -> str:
    return _SINGLE_MARKER.sub(r"[[\1]]", _CITATION_NUMBER.sub("", prose))


def _candidate_blocks(
    text: str, evidence: TurnEvidence
) -> tuple[list[CandidateBlock], bool]:
    if not isinstance(text, str) or not text.strip() or len(text) > 16_000:
        raise ValueError("Invalid assistant message")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValueError("Unexpected control character in assistant message")
    _reject_tool_protocol(text)
    blocks = []
    rejected = False
    table_index = None
    for index, markdown in enumerate(_markdown_blocks(text)):
        markers = []
        malformed = False

        def remove_markers(prose: str) -> str:
            nonlocal malformed
            markers.extend(_MARKER.findall(prose))
            cleaned = _MARKER.sub("", prose)
            malformed |= "[[e" in cleaned
            return cleaned

        markdown = _prose(markdown, _normalize_markers)
        has_body = bool(_prose(markdown, remove_markers).strip())
        ids = tuple(dict.fromkeys(markers))
        version_placeholder = False
        if not has_body and ids:
            versions = sum(
                "requested_version_found" in evidence.sources.get(marker, {})
                for marker in ids
            )
            version_placeholder = versions == len(ids)
            if versions == 0 and table_index == index - 1:
                table = blocks.pop()
                markdown = f"{table.markdown}\n\n{markdown}"
                ids = tuple(dict.fromkeys((*table.evidence_ids, *ids)))
                has_body = True
        if (
            (not has_body and not version_placeholder)
            or malformed
            or len(ids) > 20
            or any(marker not in evidence.sources for marker in ids)
        ):
            rejected = True
            continue
        block = CandidateBlock(markdown, ids)
        blocks.append(block)
        if block.is_table:
            table_index = index
    return blocks, rejected


def _matches_source(block: CandidateBlock, evidence: TurnEvidence) -> bool:
    table = block.is_table
    structured = block.preserves_structure
    for line in block.markdown.splitlines():
        prose = _prose(line, lambda value: value, keep_code=False)
        markers = _MARKER.findall(prose)
        attached = [evidence.sources[marker] for marker in markers]
        claim = unicodedata.normalize("NFKC", _MARKER.sub("", prose))
        # A table or list can use distinct evidence on each line. An attached
        # version in a different row cannot support this line's validity claim.
        sources = attached or [
            evidence.sources[marker] for marker in block.evidence_ids
        ]
        if _VALIDITY.search(claim) and not any(
            "requested_version_found" in source
            for source in (attached if structured else sources)
        ):
            return False
        if table and any(
            _VALIDITY.search(cell)
            and not any(
                "requested_version_found" in evidence.sources[marker]
                for marker in _MARKER.findall(cell)
            )
            for cell in _CELL_SEPARATOR.split(prose)
        ):
            return False
        excerpts = [source.get("content", "") for source in sources]
        if _EXCLUSIVE.search(claim) and not any(
            _EXCLUSIVE.search(excerpt) for excerpt in excerpts
        ):
            return False
        articles = {
            article_label(label)
            for source, excerpt in zip(sources, excerpts, strict=True)
            for label in _ARTICLE.findall(excerpt + "\n" + source.get("article", ""))
        }
        if any(
            article_label(label) not in articles for label in _ARTICLE.findall(claim)
        ):
            return False
        numbers = compact(
            unicodedata.normalize(
                "NFKC",
                "\n".join(
                    (source.get("document_number") or "") + "\n" + excerpt
                    for source, excerpt in zip(sources, excerpts, strict=True)
                ),
            )
        )
        if any(
            compact(number) not in numbers for number in _DOCUMENT_NUMBER.findall(claim)
        ):
            return False
    return True


async def deliver(
    text: str,
    request: ChatRequest,
    context: ChatContext,
    evidence: TurnEvidence,
    session: AsyncSession,
) -> tuple[list[ChatParagraph], ChatResponse]:
    blocks, missing = _candidate_blocks(text, evidence)
    citations = CitationBuilder(context, session, request.document_ids)
    fallback_editions = {
        ref
        for ref, entry in context.version_evidence.items()
        if not entry.requested_version_found
    }
    source_ids = fallback_editions | {
        evidence.by_alias[marker] for block in blocks for marker in block.evidence_ids
    }
    sources = await citations.load_sources(source_ids)
    paragraphs = []
    for block in blocks:
        if not block.evidence_ids:
            paragraphs.append(ChatParagraph(text=block.markdown, citations=[]))
            continue
        if not _matches_source(block, evidence):
            missing = True
            continue
        ids = {marker: evidence.by_alias[marker] for marker in block.evidence_ids}
        if paragraph := citations.render(
            block.render, ids, sources, preserve_structure=block.preserves_structure
        ):
            if not paragraph.citations and any(
                paragraph.text == prior.text for prior in paragraphs
            ):
                continue
            paragraphs.append(paragraph)

    for ref in sorted(fallback_editions):
        entry = context.version_evidence[ref]
        if (entry.version_id, False) in citations.reported_versions:
            continue
        if paragraph := citations.render(None, {ref: ref}, sources):
            paragraphs.append(paragraph)
    if not paragraphs:
        return [], insufficient()
    if missing or citations.partial:
        paragraphs.append(ChatParagraph(text=_EVIDENCE_GAP, citations=[]))
    status = "partial" if missing or citations.partial else "answered"
    delivered_citations = list(citations.citations.values())
    return paragraphs, ChatResponse(
        status=status,
        answer="\n\n".join(paragraph.text for paragraph in paragraphs),
        citations=delivered_citations,
        confidence=compute_confidence(status, paragraphs, delivered_citations),
    )

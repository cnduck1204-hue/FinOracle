from collections.abc import Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import ChatContext
from app.core.exceptions import AppException
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.rag import DEFAULT_INGEST
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.schemas.chat import (
    ChatChunkCitation,
    ChatCitation,
    ChatConfidence,
    ChatConfidenceLevel,
    ChatParagraph,
    ChatReference,
    ChatResponse,
    ChatSource,
    ChatStatus,
    ChatVersionCitation,
    ChatVersionMetadata,
    ChatVersionReference,
)
from app.schemas.search import SearchDocument, SearchVersion

_INSUFFICIENT = (
    "已发布的制度资料暂不足以回答这个问题。可以补充制度名称或具体场景后再试。"
)
EvidenceId = int | str
_VALIDITY_LABELS = {
    "unknown": "时效待核验",
    "effective": "现行有效",
    "not_yet_effective": "尚未生效",
    "amended": "已修订",
    "repealed": "已废止",
}


def compute_confidence(
    status: ChatStatus,
    paragraphs: list[ChatParagraph],
    citations: list[ChatCitation],
) -> ChatConfidence | None:
    """基于证据覆盖度、校验完整性和条文时效计算 0-100 可信度评分。"""
    if status == "insufficient_evidence":
        return ChatConfidence(
            score=15,
            level="low",
            reasons=["当前已发布制度资料不足以支撑明确结论"],
        )
    if not citations:
        if status == "partial":
            return ChatConfidence(
                score=35,
                level="low",
                reasons=["部分内容缺少可核对的制度条文依据"],
            )
        return None

    reasons: list[str] = []
    score = 60 if status == "answered" else 42
    if status == "answered":
        reasons.append("已通过后端条文、文号与时效一致性核验")
    else:
        reasons.append("部分主张缺少可核对依据或存在未覆盖版本")

    # 证据覆盖度：带引用段落占比（最多 +20）
    substantive = [p for p in paragraphs if p.text.strip()]
    if substantive:
        cited_ratio = sum(1 for p in substantive if p.citations) / len(substantive)
        score += round(cited_ratio * 20)
        if cited_ratio >= 0.8:
            reasons.append("核心段落均有可溯源的制度引用")

    # 依据充分度：多条依据交叉印证（最多 +10）
    if len(citations) >= 2:
        score += 10
        reasons.append(f"引用 {len(citations)} 条已发布制度依据交叉印证")
    else:
        score += 6
        reasons.append("引用 1 条已发布制度依据")

    # 时效质量（+10 / -10 / -25）
    statuses = {citation.version.validity_status for citation in citations}
    if "repealed" in statuses:
        score -= 25
        reasons.append("所引资料包含已废止条文，请注意时效风险")
    elif "amended" in statuses:
        score -= 10
        reasons.append("所引资料包含已修订条文，建议核对最新版本")
    elif statuses == {"effective"}:
        score += 10
        reasons.append("所引条文均已核验为现行有效")
    else:
        reasons.append("部分引用条文时效待核验或尚未生效")

    clamped = max(0, min(100, score))
    level: ChatConfidenceLevel = (
        "high" if clamped >= 80 else "medium" if clamped >= 55 else "low"
    )
    return ChatConfidence(score=clamped, level=level, reasons=reasons)


def insufficient() -> ChatResponse:
    return ChatResponse(
        status="insufficient_evidence",
        answer=_INSUFFICIENT,
        confidence=compute_confidence("insufficient_evidence", [], []),
    )


def _source(
    chunk: DocumentChunk, version: DocumentVersion, document: Document
) -> ChatSource:
    return ChatSource(
        chunk_id=chunk.id,
        document=SearchDocument.model_validate(document, from_attributes=True),
        version=SearchVersion.model_validate(version, from_attributes=True),
        chapter=chunk.chapter,
        section=chunk.section,
        article=chunk.article,
        heading=chunk.heading,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
    )


def reference_from_chunk(
    chunk: DocumentChunk, version: DocumentVersion, document: Document
) -> ChatReference:
    return ChatReference(
        **_source(chunk, version, document).model_dump(), content=chunk.content
    )


def reference_from_version(
    version: DocumentVersion, document: Document
) -> ChatVersionReference:
    return ChatVersionReference(
        document=SearchDocument.model_validate(document, from_attributes=True),
        version=ChatVersionMetadata.model_validate(version, from_attributes=True),
    )


def _reference_not_found() -> AppException:
    return AppException(
        code="CHAT_REFERENCE_NOT_FOUND", message="此引用当前不可访问", status_code=404
    )


async def get_reference(chunk_id: int, session: AsyncSession) -> ChatReference:
    async with session.begin():
        rows = await DocumentChunkRepository(session).published_with_context([chunk_id])
    if rows:
        chunk, version, document = rows[0]
        if chunk.ingest == DEFAULT_INGEST and chunk.content.strip():
            return reference_from_chunk(chunk, version, document)
    raise _reference_not_found()


async def get_version_reference(
    version_id: UUID, session: AsyncSession
) -> ChatVersionReference:
    async with session.begin():
        records = await DocumentVersionRepository(session).published_with_document(
            [version_id]
        )
    if records:
        return reference_from_version(*records[0])
    raise _reference_not_found()


def _version_statement(
    source: ChatVersionReference, *, requested_version_found: bool
) -> str:
    version = source.version
    identity = version.document_number or version.version_note
    if not identity and version.published_date:
        identity = f"{version.published_date.year} 年发布版本"
    edition = f"（{identity}）" if identity else ""
    title = f"《{source.document.title}》{edition}"
    prefix = "" if requested_version_found else "已发布资料无法确认所问版本。当前收录的"
    state = _VALIDITY_LABELS[version.validity_status]
    statement = (
        "时效待核验" if version.validity_status == "unknown" else f"时效登记为{state}"
    )
    text = f"{prefix}{title}在库内{statement}。"
    if version.effective_date:
        text += f"登记生效日为 {version.effective_date}。"
    if version.validity_checked_at:
        text += f"最近核验日期为 {version.validity_checked_at}。"
    return text


def _article_excerpt(source: ChatReference) -> str:
    excerpt = source.content.strip()
    if len(excerpt) > 240:
        excerpt = excerpt[:240] + "…"
    return f"《{source.document.title}》{source.article or source.heading}原文摘录「{excerpt}」"


class CitationBuilder:
    def __init__(
        self,
        context: ChatContext,
        session: AsyncSession,
        document_ids: list[UUID] | None,
    ) -> None:
        self.context = context
        self.session = session
        self.allowed_documents = set(document_ids or ())
        self.citations: dict[int | UUID, ChatCitation] = {}
        self.reported_versions: set[tuple[UUID, bool]] = set()
        self.partial = False

    async def load_sources(
        self, requested_ids: set[EvidenceId]
    ) -> dict[EvidenceId, ChatReference | ChatVersionReference]:
        if not requested_ids:
            return {}
        version_evidence = {
            ref: self.context.version_evidence[ref]
            for ref in requested_ids
            if isinstance(ref, str)
        }
        async with self.session.begin():
            rows = await DocumentChunkRepository(self.session).published_with_context(
                [ref for ref in requested_ids if isinstance(ref, int)]
            )
            versions = await DocumentVersionRepository(
                self.session
            ).published_with_document(
                list({entry.version_id for entry in version_evidence.values()})
            )
        sources: dict[EvidenceId, ChatReference | ChatVersionReference] = {
            chunk.id: reference_from_chunk(chunk, version, document)
            for chunk, version, document in rows
            if chunk.ingest == DEFAULT_INGEST and self._in_scope(document.id)
        }
        version_sources = {
            version.id: reference_from_version(version, document)
            for version, document in versions
            if self._in_scope(document.id)
        }
        for ref, entry in version_evidence.items():
            if entry.version_id in version_sources:
                sources[ref] = version_sources[entry.version_id]
        return sources

    def _in_scope(self, document_id: UUID) -> bool:
        return not self.allowed_documents or document_id in self.allowed_documents

    def render(
        self,
        render_text: Callable[[dict[str, int], dict[str, str]], str] | None,
        references: dict[str, EvidenceId],
        sources: dict[EvidenceId, ChatReference | ChatVersionReference],
        *,
        preserve_structure: bool = False,
    ) -> ChatParagraph | None:
        if any(ref not in sources for ref in references.values()):
            self.partial = True
            return None
        has_version = any(isinstance(ref, str) for ref in references.values())
        new_citations = []
        numbers: dict[str, int] = {}
        versions: dict[str, str] = {}
        statements = []
        for marker, ref in references.items():
            source = sources[ref]
            number = len(self.citations) + 1
            if isinstance(source, ChatVersionReference):
                key = source.version.id
                requested = self.context.version_evidence[ref].requested_version_found
                self.reported_versions.add((key, requested))
                self.partial |= not requested
                statement = _version_statement(
                    source, requested_version_found=requested
                )
                versions[marker] = statement
                statements.append(statement)
                citation = ChatVersionCitation(**source.model_dump(), number=number)
            else:
                key = source.chunk_id
                if has_version:
                    statements.append(_article_excerpt(source))
                citation = ChatChunkCitation(
                    **source.model_dump(exclude={"content"}), number=number
                )
            if key not in self.citations:
                self.citations[key] = citation
                new_citations.append(citation)
            numbers[marker] = self.citations[key].number
        if not numbers:
            return None
        if render_text and (preserve_structure or not versions):
            text = render_text(numbers, versions)
        else:
            text = "\n".join(statements) + "".join(
                f"[{number}]" for number in dict.fromkeys(numbers.values())
            )
        return ChatParagraph(text=text, citations=new_citations)

import re
import unicodedata
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import reference_from_version
from app.agent.context import ChatContext
from app.rag.parsing.text_utils import compact
from app.repositories.documents import DocumentVersionRepository
from app.schemas.chat import ChatVersionMetadata

MAX_DOCUMENT_MATCHES = 5
_YEAR_HINT = re.compile(r"([0-9]{4})年?版?")
_YEAR_IN_NUMBER = re.compile(r"(?<![0-9])([0-9]{4})(?:年|[〕\])】])")


class DocumentLookupArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ] = Field(description="制度的登记标题、文号或制度 ID")

    @field_validator("document")
    @classmethod
    def _strip_title_marks(cls, document: str) -> str:
        if document.startswith("《") and document.endswith("》"):
            document = document[1:-1].strip()
        if not document:
            raise ValueError("制度标识不能为空")
        return document

    @property
    def reference(self) -> str | UUID:
        try:
            return UUID(self.document)
        except ValueError:
            return self.document


class StatusArguments(DocumentLookupArguments):
    version_hint: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
        ]
        | None
    ) = Field(default=None, description="用户指定的版本年份或完整文号，例如 2012年版")


def document_ambiguity(documents: list[Row]) -> dict:
    return {
        "error": "DOCUMENT_AMBIGUOUS",
        "documents": [
            {
                "document_id": str(document.document_id),
                "title": document.title,
                "document_number": document.document_number,
            }
            for document in documents
        ],
    }


def _normalize(text: str) -> str:
    return compact(unicodedata.normalize("NFKC", text))


def _matches_version(version: ChatVersionMetadata, hint: str | None) -> bool:
    if hint is None:
        return True
    requested = _normalize(hint)
    number = _normalize(version.document_number or "")
    if requested == number or requested == _normalize(version.version_note or ""):
        return True
    year = _YEAR_HINT.fullmatch(requested)
    if not year:
        return False
    if version.published_date:
        return version.published_date.year == int(year[1])
    return set(_YEAR_IN_NUMBER.findall(number)) == {year[1]}


async def get_document_status(
    args: StatusArguments,
    document_ids: list[UUID] | None,
    session: AsyncSession,
    context: ChatContext,
) -> dict:
    repository = DocumentVersionRepository(session)
    async with session.begin():
        matches = await repository.match_published_reference(
            args.reference,
            document_ids,
            limit=None if args.version_hint else MAX_DOCUMENT_MATCHES,
        )
        if len(matches) > 1 and not args.version_hint:
            return document_ambiguity(matches)
        records = await repository.published_with_document(
            [match.version_id for match in matches]
        )
        if not records:
            return {"error": "DOCUMENT_NOT_FOUND"}
        sources = [reference_from_version(*record) for record in records]

    matching_versions = [
        source
        for source in sources
        if _matches_version(source.version, args.version_hint)
    ]
    if len(sources) > 1 and len(matching_versions) != 1:
        choices = {source.version.id for source in matching_versions or sources}
        return document_ambiguity(
            [match for match in matches if match.version_id in choices][
                :MAX_DOCUMENT_MATCHES
            ]
        )
    source = (matching_versions or sources)[0]
    matches_hint = _matches_version(source.version, args.version_hint)
    evidence = context.add_version_evidence(
        source, requested_version_found=matches_hint
    )
    result = {"evidence": [evidence]}
    if not matches_hint:
        result["error"] = "VERSION_NOT_FOUND"
    return result

import re
import unicodedata
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import reference_from_chunk
from app.agent.context import MAX_EVIDENCE_CHARS, ChatContext
from app.agent.regulations import (
    MAX_DOCUMENT_MATCHES,
    DocumentLookupArguments,
    document_ambiguity,
)
from app.rag.parsing.text_utils import chinese_number, compact
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)

_NUMBER = r"(?:[0-9]+|[零〇一二两三四五六七八九十百千]+)"
_ARTICLE_LABEL = re.compile(rf"(?:第)?({_NUMBER})(?:条(?:之({_NUMBER}))?)?")
_DIGITS = str.maketrans("零〇一二两三四五六七八九", "001223456789")
_MAX_LOCATIONS = 5
_MAX_PARTS = 20


def _number(text: str) -> int:
    digits = text.translate(_DIGITS)
    return int(digits) if digits.isdecimal() else chinese_number(text)


def article_label(text: str) -> str | None:
    match = _ARTICLE_LABEL.fullmatch(compact(unicodedata.normalize("NFKC", text)))
    if not match:
        return None
    number, suffix = match.groups()
    main = _number(number)
    extension = _number(suffix) if suffix else None
    if not main or extension == 0:
        return None
    return f"第{main}条" + (f"之{extension}" if extension is not None else "")


class ArticleArguments(DocumentLookupArguments):
    article: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        description="完整条号，例如第六条、第6条之一"
    )
    start_ordinal: int | None = Field(
        default=None, gt=0, description="仅在消歧时传入候选位置返回的 start_ordinal"
    )

    @field_validator("article")
    @classmethod
    def _normalize_article(cls, article: str) -> str:
        label = article_label(article)
        if label is None:
            raise ValueError("请提供一个完整条号")
        return label


def _group_locations(locations: list[Row], article: str) -> list[list[Row]]:
    groups: list[list[Row]] = []
    for location in locations:
        if article_label(location.article) != article:
            continue
        if groups and location.part > 1:
            previous = groups[-1][-1]
            if (
                location.ordinal == previous.ordinal + 1
                and location.part == previous.part + 1
            ):
                groups[-1].append(location)
                continue
        groups.append([location])
    return groups


async def get_article(
    args: ArticleArguments,
    document_ids: list[UUID] | None,
    session: AsyncSession,
    context: ChatContext,
) -> dict:
    async with session.begin():
        documents = await DocumentVersionRepository(session).match_published_reference(
            args.reference, document_ids, limit=MAX_DOCUMENT_MATCHES
        )
        if not documents:
            return {"error": "DOCUMENT_NOT_FOUND"}
        if len(documents) > 1:
            return document_ambiguity(documents)
        repository = DocumentChunkRepository(session)
        groups = _group_locations(
            await repository.article_locations(documents[0].version_id), args.article
        )
        if args.start_ordinal is not None:
            groups = [
                group for group in groups if group[0].ordinal == args.start_ordinal
            ]
        if not groups:
            return {"error": "ARTICLE_NOT_FOUND"}
        if len(groups) > 1:
            return {
                "error": "ARTICLE_AMBIGUOUS",
                "locations": [
                    {
                        "start_ordinal": group[0].ordinal,
                        "chapter": group[0].chapter,
                        "section": group[0].section,
                        "heading": group[0].heading,
                    }
                    for group in groups[:_MAX_LOCATIONS]
                ],
            }

        group = groups[0]
        ids = [location.id for location in group[:_MAX_PARTS]]
        references = {
            chunk.id: reference_from_chunk(chunk, version, document)
            for chunk, version, document in await repository.published_with_context(ids)
        }
        if not references:
            return {"error": "ARTICLE_NOT_FOUND"}

    evidence = context.add_evidence(
        [references[chunk_id] for chunk_id in ids if chunk_id in references],
        excerpt_limit=MAX_EVIDENCE_CHARS,
    )
    available = [
        chunk_id
        for chunk_id in ids
        if chunk_id in references and chunk_id in context.evidence_ids
    ]
    return {
        "evidence": evidence,
        "evidence_ids": available,
        "truncated": len(available) < max(len(group), group[0].part_count)
        or not context.truncated_ids.isdisjoint(available),
    }

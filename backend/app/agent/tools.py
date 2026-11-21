from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.articles import ArticleArguments, get_article
from app.agent.citations import EvidenceId, reference_from_chunk, reference_from_version
from app.agent.context import ChatContext
from app.agent.regulations import StatusArguments, get_document_status
from app.rag import DEFAULT_INGEST
from app.rag.pipelines import PipelineRegistry
from app.rag.pipelines.registry import STRUCTURED_PIPELINE
from app.repositories.documents import (
    DocumentChunkRepository,
    DocumentVersionRepository,
)
from app.schemas.chat import ChatChunkCitation, ChatRequest, ChatTurn
from app.schemas.search import QueryText, SearchRequest
from app.services.search import search

MAX_TOOL_EXECUTIONS = 4
SEARCH_TOOL = "search_knowledge"
ARTICLE_TOOL = "get_article"
STATUS_TOOL = "get_document_status"
REUSE_TOOL = "reuse_answer_evidence"


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: QueryText


class ReuseEvidenceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: UUID


ToolArguments = (
    SearchArguments | ArticleArguments | StatusArguments | ReuseEvidenceArguments
)


_TOOL_SCHEMAS = {
    SEARCH_TOOL: (
        SearchArguments,
        "在用户可见的金融制度资料中检索条文，返回正文及引用 ID。",
    ),
    ARTICLE_TOOL: (
        ArticleArguments,
        "按制度和条号精确读取发布版本的条文。多个候选时返回定位选项。",
    ),
    STATUS_TOOL: (
        StatusArguments,
        "查询已发布的制度版本、登记时效、生效和核验日期。指定版本时传入版本线索。",
    ),
    REUSE_TOOL: (
        ReuseEvidenceArguments,
        "总结或改写已交付答复时，按上下文中的轮次 ID 复核原引用。新的制度主张请另行查证。",
    ),
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema.model_json_schema(),
        },
    }
    for name, (schema, description) in _TOOL_SCHEMAS.items()
]

_TOOL_ERRORS = {
    "INVALID_TOOL_ARGUMENTS": "查询条件不完整",
    "ALREADY_SEARCHED": "已查询过相同条件",
    "TOOL_BUDGET_EXHAUSTED": "本轮查询次数已用完",
    "DOCUMENT_NOT_FOUND": "未找到已发布的制度",
    "DOCUMENT_AMBIGUOUS": "匹配到多部制度或版本",
    "ARTICLE_NOT_FOUND": "未找到指定条文",
    "ARTICLE_AMBIGUOUS": "匹配到多个条文位置",
    "VERSION_NOT_FOUND": "未找到指定版本",
    "HISTORY_EVIDENCE_UNAVAILABLE": "原答复的出处当前不可复核",
}


def parse_tool_arguments(name: str, raw: object) -> ToolArguments | None:
    schema, _ = _TOOL_SCHEMAS[name]
    try:
        return schema.model_validate(raw)
    except ValidationError:
        return None


def tool_target(args: ToolArguments | None) -> str | None:
    if isinstance(args, SearchArguments):
        return args.query
    if isinstance(args, ArticleArguments):
        return f"{args.document} · {args.article}"
    if isinstance(args, StatusArguments):
        return " · ".join(part for part in (args.document, args.version_hint) if part)
    return None


def tool_summary(name: str, result: dict) -> str:
    if error := result.get("error"):
        return _TOOL_ERRORS.get(error, "查询失败")
    matches = result.get("evidence_ids", result.get("evidence", []))
    if not matches:
        return "未取得可引用的依据"
    unit = {
        SEARCH_TOOL: "条依据",
        ARTICLE_TOOL: "段条文",
        STATUS_TOOL: "个制度版本",
        REUSE_TOOL: "条原有依据",
    }[name]
    return f"返回 {len(matches)} {unit}"


class ChatTools:
    def __init__(
        self,
        request: ChatRequest,
        session: AsyncSession,
        pipelines: PipelineRegistry,
        approved_turns: dict[UUID, ChatTurn],
    ) -> None:
        self.request = request
        self.session = session
        self.pipelines = pipelines
        self.approved_turns = approved_turns
        self.executed: set[tuple[str, str]] = set()

    @property
    def remaining_calls(self) -> int:
        return MAX_TOOL_EXECUTIONS - len(self.executed)

    async def execute(
        self, name: str, args: ToolArguments | None, context: ChatContext
    ) -> dict:
        if args is None:
            return {"error": "INVALID_TOOL_ARGUMENTS"}
        key = name, args.model_dump_json()
        if key in self.executed:
            return {"error": "ALREADY_SEARCHED", "message": "请参考此前的查询结果。"}
        if not self.remaining_calls:
            return {"error": "TOOL_BUDGET_EXHAUSTED"}

        self.executed.add(key)
        if isinstance(args, ArticleArguments):
            return await get_article(
                args, self.request.document_ids, self.session, context
            )
        if isinstance(args, StatusArguments):
            return await get_document_status(
                args, self.request.document_ids, self.session, context
            )
        if isinstance(args, ReuseEvidenceArguments):
            return await self._reuse_evidence(args.turn_id, context)
        response = await search(
            SearchRequest(
                query=args.query,
                document_ids=self.request.document_ids,
                pipeline=STRUCTURED_PIPELINE,
                top_k=5,
            ),
            self.session,
            self.pipelines,
        )
        return {"evidence": context.add_evidence(response.results)}

    async def _reuse_evidence(self, turn_id: UUID, context: ChatContext) -> dict:
        turn = self.approved_turns.get(turn_id)
        if turn is None or turn.result is None or not turn.result.citations:
            return {"error": "HISTORY_EVIDENCE_UNAVAILABLE"}
        allowed = set(self.request.document_ids or ())
        chunks = {}
        versions = {}
        for citation in turn.result.citations:
            if allowed and citation.document.id not in allowed:
                continue
            if isinstance(citation, ChatChunkCitation):
                chunks[citation.chunk_id] = (citation.document.id, citation.version.id)
            else:
                versions[citation.version.id] = citation.document.id
        async with self.session.begin():
            rows = await DocumentChunkRepository(self.session).published_with_context(
                list(chunks)
            )
            records = await DocumentVersionRepository(
                self.session
            ).published_with_document(list(versions))
        refs = [
            reference_from_chunk(chunk, version, document)
            for chunk, version, document in rows
            if chunk.ingest == DEFAULT_INGEST
            and chunks[chunk.id] == (document.id, version.id)
        ]
        evidence = context.add_evidence(refs)
        available: list[EvidenceId] = [
            ref.chunk_id for ref in refs if ref.chunk_id in context.evidence_ids
        ]
        for version, document in records:
            if versions[version.id] == document.id:
                item = context.add_version_evidence(
                    reference_from_version(version, document),
                    requested_version_found=True,
                )
                evidence.append(item)
                available.append(item["id"])
        if not available:
            return {"error": "HISTORY_EVIDENCE_UNAVAILABLE"}
        return {
            "evidence": evidence,
            "evidence_ids": available,
            "unavailable": len(turn.result.citations) - len(available),
        }

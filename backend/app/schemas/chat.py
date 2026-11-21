from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from app.schemas.search import SearchDocument, SearchVersion

ChatStatus = Literal["answered", "partial", "insufficient_evidence"]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    document_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    session_id: UUID | None = Field(default=None, description="省略时创建本人会话")
    request_id: UUID | None = None

    @model_validator(mode="after")
    def validate_request_id(self) -> "ChatRequest":
        if self.request_id is not None and self.session_id is None:
            raise ValueError("请求标识需要会话")
        return self


class ChatSource(BaseModel):
    chunk_id: int
    document: SearchDocument
    version: SearchVersion
    chapter: str
    section: str
    article: str
    heading: str
    page_start: int | None
    page_end: int | None


class ChatChunkCitation(ChatSource):
    kind: Literal["chunk"] = "chunk"
    number: int


class ChatVersionMetadata(SearchVersion):
    validity_checked_at: date | None


class ChatVersionReference(BaseModel):
    kind: Literal["version"] = "version"
    document: SearchDocument
    version: ChatVersionMetadata


class ChatVersionCitation(ChatVersionReference):
    number: int


ChatCitation = ChatChunkCitation | ChatVersionCitation


class ChatProgress(BaseModel):
    stage: Literal["retrieving", "generating"]


ChatToolName = Literal[
    "search_knowledge", "get_article", "get_document_status", "reuse_answer_evidence"
]
ChatStepState = Literal["running", "completed", "failed", "interrupted"]


class ChatModelStep(BaseModel):
    attempt: int = Field(gt=0)
    state: ChatStepState


class ChatToolStep(BaseModel):
    id: str
    tool: ChatToolName
    state: ChatStepState
    target: str | None = None
    summary: str | None = None


ChatExecutionStep = ChatModelStep | ChatToolStep


class ChatAnswerDelta(BaseModel):
    text: str


class ChatParagraph(BaseModel):
    text: str
    citations: list[ChatCitation]


class ChatReference(ChatSource):
    content: str


class ChatResponse(BaseModel):
    status: ChatStatus
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)


ChatTurnEnd = Literal["completed", "interrupted", "failed"]


class ChatTurn(BaseModel):
    id: UUID
    question: str
    state: Literal["running"] | ChatTurnEnd
    result: ChatResponse | None = None
    execution: list[ChatExecutionStep] = Field(default_factory=list)

    @computed_field
    @property
    def tool_steps(self) -> list[ChatToolStep]:
        return [step for step in self.execution if isinstance(step, ChatToolStep)]


class ChatSessionCreated(BaseModel):
    id: UUID


class ChatSessionSummary(ChatSessionCreated):
    title: str
    archived: bool
    pinned: bool
    updated_at: AwareDatetime


class ChatSessionList(BaseModel):
    items: list[ChatSessionSummary]
    next_cursor: str | None = None


class ChatSessionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
        ]
        | None
    ) = None
    archived: bool | None = None
    pinned: bool | None = None

    @model_validator(mode="after")
    def has_changes(self) -> "ChatSessionUpdate":
        if self.title is None and self.archived is None and self.pinned is None:
            raise ValueError("请提供会话标题、归档状态或置顶状态")
        return self


class ChatSessionHistory(ChatSessionSummary):
    turns: list[ChatTurn]
    through_seq: int
    next_before_seq: int | None = None

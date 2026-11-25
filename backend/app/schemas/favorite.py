"""收藏夹与收藏项请求响应结构。"""

from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.models.favorite import FavoriteKind

FolderName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
]
FavoriteTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
FavoriteNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
FavoriteScope = Literal["root", "all"]


def _validate_http_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 2048:
        raise ValueError("链接长度无效")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("仅支持 http 或 https 链接")
    return cleaned


class FavoriteFolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: FolderName
    parent_id: UUID | None = None


class FavoriteFolderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: FolderName | None = None
    parent_id: UUID | None = None

    @model_validator(mode="after")
    def has_changes(self) -> "FavoriteFolderUpdate":
        if (
            "name" not in self.model_fields_set
            and "parent_id" not in self.model_fields_set
        ):
            raise ValueError("请提供文件夹名称或目标位置")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("文件夹名称不能为空")
        return self


class FavoriteFolderSummary(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    position: int
    item_count: int
    subfolder_count: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class FavoriteTreeResponse(BaseModel):
    folders: list[FavoriteFolderSummary]
    root_item_count: int
    total_item_count: int


class FavoriteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FavoriteKind
    folder_id: UUID | None = None
    title: FavoriteTitle | None = None
    note: FavoriteNote | None = None
    chunk_id: int | None = Field(default=None, gt=0)
    session_id: UUID | None = None
    turn_id: UUID | None = None
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("note")
    @classmethod
    def _empty_note_as_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_source(self) -> "FavoriteCreate":
        if self.kind == "chunk":
            if self.chunk_id is None:
                raise ValueError("收藏条文需要提供 chunk_id")
            if (
                self.session_id is not None
                or self.turn_id is not None
                or self.url is not None
            ):
                raise ValueError("条文收藏不接受会话或链接字段")
        elif self.kind == "qa":
            if self.session_id is None or self.turn_id is None:
                raise ValueError("收藏问答需要提供 session_id 与 turn_id")
            if self.chunk_id is not None or self.url is not None:
                raise ValueError("问答收藏不接受条文或链接字段")
        else:
            if not self.title:
                raise ValueError("链接收藏需要填写标题")
            if not self.url:
                raise ValueError("链接收藏需要填写网址")
            self.url = _validate_http_url(self.url)
            if (
                self.chunk_id is not None
                or self.session_id is not None
                or self.turn_id is not None
            ):
                raise ValueError("链接收藏不接受条文或会话字段")
        return self


class FavoriteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: FavoriteTitle | None = None
    note: FavoriteNote | None = None
    folder_id: UUID | None = None
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("note")
    @classmethod
    def _empty_note_as_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_http_url(value)

    @model_validator(mode="after")
    def has_changes(self) -> "FavoriteUpdate":
        if not (self.model_fields_set & {"title", "note", "folder_id", "url"}):
            raise ValueError("请提供要更新的字段")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("收藏标题不能为空")
        return self


class FavoriteItem(BaseModel):
    id: UUID
    folder_id: UUID | None
    kind: FavoriteKind
    title: str
    subtitle: str
    note: str | None
    source_key: str | None
    payload: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class FavoriteListResponse(BaseModel):
    folders: list[FavoriteFolderSummary]
    items: list[FavoriteItem]


class FavoriteSourceMatch(BaseModel):
    id: UUID
    folder_id: UUID | None


class FavoriteLookupResponse(BaseModel):
    items: dict[str, FavoriteSourceMatch]


class FavoriteBatchDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_ids: list[UUID] = Field(default_factory=list, max_length=200)
    item_ids: list[UUID] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def has_targets(self) -> "FavoriteBatchDelete":
        if not self.folder_ids and not self.item_ids:
            raise ValueError("请选择至少一个文件夹或收藏项")
        return self


class FavoriteBatchMove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    folder_ids: list[UUID] = Field(default_factory=list, max_length=200)
    item_ids: list[UUID] = Field(default_factory=list, max_length=200)
    target_folder_id: UUID | None = None

    @model_validator(mode="after")
    def has_targets(self) -> "FavoriteBatchMove":
        if not self.folder_ids and not self.item_ids:
            raise ValueError("请选择至少一个文件夹或收藏项")
        return self

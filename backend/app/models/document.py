"""制度、版本与切片。设计见 docs/design/2026-11-05-document-ingestion。

一个制度（Document）有多个版本（DocumentVersion），文件、时效与解析结果都属于版本；
切片（DocumentChunk）属于版本。每个制度最多一个已发布版本，由数据库唯一约束保证。
"""

import hashlib
from datetime import date, datetime
from typing import Any, Literal, get_args
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Computed,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.mysql import DATETIME, MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.rag import DEFAULT_INGEST as DEFAULT_INGEST

# uploaded → parsing → parsed →（批准）→ indexing → ready →（发布）→ published → archived；
# 解析或索引失败为 failed，可重试
VersionStatus = Literal[
    "uploaded",
    "parsing",
    "parsed",
    "indexing",
    "ready",
    "published",
    "archived",
    "failed",
]
# 时效状态与版本生命周期无关；未核实时为 unknown，不能显示为现行有效
ValidityStatus = Literal[
    "unknown", "effective", "not_yet_effective", "amended", "repealed"
]
ParseStatus = Literal["completed", "needs_ocr", "empty"]
# 附加方案的索引状态；默认方案的状态就是版本状态
IndexStatus = Literal["building", "ready", "failed"]

VERSION_STATUSES: tuple[VersionStatus, ...] = get_args(VersionStatus)
VALIDITY_STATUSES: tuple[ValidityStatus, ...] = get_args(ValidityStatus)
PARSE_STATUSES: tuple[ParseStatus, ...] = get_args(ParseStatus)
INDEX_STATUSES: tuple[IndexStatus, ...] = get_args(IndexStatus)

# 大整数主键：SQLite（仓库单元测试）仅 INTEGER PRIMARY KEY 自增
_BIGINT_ID = BigInteger().with_variant(Integer(), "sqlite")
# 单个切片不超过 4000 字；外部导入的整条条文可能更长，MySQL 使用 MEDIUMTEXT
_LONG_TEXT = Text().with_variant(MEDIUMTEXT(), "mysql")


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class Document(Base):
    """制度：跨版本不变的身份信息。"""

    __tablename__ = "documents"

    __table_args__ = (UniqueConstraint("external_id", name="uq_documents_external_id"),)

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    # 外部数据集中的 doc_id，便于重复导入时找回同一制度
    external_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    issuer: Mapped[str | None] = mapped_column(String(255))
    # 导入脚本等系统操作没有操作人
    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", name="fk_documents_created_by")
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )


class DocumentVersion(Base):
    """制度的一个版本：原件、版本与时效信息、解析和索引状态。"""

    __tablename__ = "document_versions"

    __table_args__ = (
        # 同一制度不重复上传同一文件
        UniqueConstraint(
            "document_id", "sha256", name="uq_document_versions_document_sha256"
        ),
        # 供切片的复合外键引用，保证切片的 document_id 与所属版本一致
        UniqueConstraint("id", "document_id", name="uq_document_versions_id_document"),
        UniqueConstraint(
            "published_document_id", name="uq_document_versions_published_document"
        ),
        Index("ix_document_versions_status", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id", name="fk_document_versions_document_id"),
    )
    status: Mapped[str] = mapped_column(
        Enum(*VERSION_STATUSES, name="document_version_status", validate_strings=True),
        default="uploaded",
        server_default="uploaded",
    )
    # 仅已发布版本有值，唯一约束保证每个制度最多一个已发布版本；由数据库计算，不要读写
    published_document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        Computed("CASE WHEN status = 'published' THEN document_id END", persisted=True),
        deferred=True,
    )

    document_number: Mapped[str | None] = mapped_column(String(255))
    version_note: Mapped[str | None] = mapped_column(String(255))
    published_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    validity_status: Mapped[str] = mapped_column(
        Enum(
            *VALIDITY_STATUSES, name="document_validity_status", validate_strings=True
        ),
        default="unknown",
        server_default="unknown",
    )
    validity_checked_at: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    download_url: Mapped[str | None] = mapped_column(String(2048))

    file_name: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(512))
    # .doc/.rtf/.odt 转换后的 PDF；切片页码以它为准
    pdf_object_key: Mapped[str | None] = mapped_column(String(512))

    # ocr / chunker / max_chunk_chars / chunk_overlap
    parse_options: Mapped[dict[str, Any]] = mapped_column(JSON)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    parse_status: Mapped[str | None] = mapped_column(
        Enum(*PARSE_STATUSES, name="document_parse_status", validate_strings=True)
    )
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 登记的应有条数；与实际切出的条数不符时在校验页提示
    expected_articles: Mapped[int | None] = mapped_column(Integer)
    parse_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    parse_warnings: Mapped[list[str] | None] = mapped_column(JSON)

    # 向量化所用模型与维度，换模型时据此重建索引
    index_model: Mapped[str | None] = mapped_column(String(128))
    indexed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))

    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_document_versions_created_by"),
    )
    published_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", name="fk_document_versions_published_by"),
    )
    published_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )


class DocumentChunk(Base):
    """版本的一个切片；id 同时作为向量库主键。"""

    __tablename__ = "document_chunks"

    __table_args__ = (
        UniqueConstraint(
            "version_id", "ingest", "ordinal", name="uq_document_chunks_ordinal"
        ),
        ForeignKeyConstraint(
            ["version_id", "document_id"],
            ["document_versions.id", "document_versions.document_id"],
            name="fk_document_chunks_version",
            ondelete="CASCADE",
        ),
        Index("ix_document_chunks_version_document", "version_id", "document_id"),
    )

    id: Mapped[int] = mapped_column(_BIGINT_ID, primary_key=True, autoincrement=True)
    version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    # 冗余保存，便于按制度过滤；由复合外键保证与版本一致
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    # 产生该切片的入库方案
    ingest: Mapped[str] = mapped_column(
        String(32), default=DEFAULT_INGEST, server_default=DEFAULT_INGEST
    )
    # 外部数据集中的 chunk_id
    external_id: Mapped[str | None] = mapped_column(String(128))
    ordinal: Mapped[int] = mapped_column(Integer)
    chapter: Mapped[str] = mapped_column(String(255), default="", server_default="")
    section: Mapped[str] = mapped_column(String(255), default="", server_default="")
    article: Mapped[str] = mapped_column(String(255), default="", server_default="")
    heading: Mapped[str] = mapped_column(String(512), default="", server_default="")
    part: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    part_count: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    content: Mapped[str] = mapped_column(_LONG_TEXT)
    content_sha256: Mapped[str] = mapped_column(String(64))
    # PDF 物理页序号；有格式转换时指转换后的 PDF
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # 内容来源：text / ocr
    sources: Mapped[list[str]] = mapped_column(JSON)
    # 页面上的位置 [x0, y0, x1, y1]（PDF 点，原点在左上角）；解析器不提供时为空
    bbox: Mapped[list[float] | None] = mapped_column(JSON)
    # text / table；解析器不提供时为空
    modality: Mapped[str | None] = mapped_column(String(16))


class VersionIndex(Base):
    """版本在附加入库方案下的索引状态。默认方案的状态就是版本状态，不在此表。"""

    __tablename__ = "version_indexes"

    version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "document_versions.id",
            name="fk_version_indexes_version_id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    ingest: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(
        Enum(*INDEX_STATUSES, name="version_index_status", validate_strings=True),
        default="building",
        server_default="building",
    )
    unit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    page_count: Mapped[int | None] = mapped_column(Integer)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # 各索引器的模型标识，以 + 连接；不同时须重建
    index_model: Mapped[str | None] = mapped_column(String(255))
    indexed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), default=utc_now, onupdate=utc_now
    )

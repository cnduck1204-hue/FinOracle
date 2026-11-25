"""收藏夹业务服务：文件夹树管理、条文/问答/链接收藏及批量整理。"""

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import UTC
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.db.base import utc_now
from app.models.chat_session import ChatSession, ChatSessionEvent
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.favorite import Favorite, FavoriteFolder, FavoriteKind
from app.repositories.documents import DocumentChunkRepository
from app.schemas.favorite import (
    FavoriteBatchDelete,
    FavoriteBatchMove,
    FavoriteCreate,
    FavoriteFolderCreate,
    FavoriteFolderSummary,
    FavoriteFolderUpdate,
    FavoriteItem,
    FavoriteListResponse,
    FavoriteLookupResponse,
    FavoriteScope,
    FavoriteSourceMatch,
    FavoriteTreeResponse,
    FavoriteUpdate,
)
from app.services import chat_sessions

MAX_FOLDER_DEPTH = 5


def _folder_not_found() -> AppException:
    return AppException(
        code="FAVORITE_FOLDER_NOT_FOUND", message="找不到此收藏文件夹", status_code=404
    )


def _favorite_not_found() -> AppException:
    return AppException(
        code="FAVORITE_NOT_FOUND", message="找不到此收藏项", status_code=404
    )


def _source_not_found() -> AppException:
    return AppException(
        code="FAVORITE_SOURCE_NOT_FOUND",
        message="目标条文或问答不存在或暂不可用",
        status_code=404,
    )


def _duplicate_folder() -> AppException:
    return AppException(
        code="FAVORITE_FOLDER_EXISTS",
        message="同一目录下已存在同名文件夹",
        status_code=409,
    )


def _duplicate_favorite() -> AppException:
    return AppException(
        code="FAVORITE_ALREADY_EXISTS",
        message="该内容已在你的收藏夹中",
        status_code=409,
    )


def _folder_cycle() -> AppException:
    return AppException(
        code="FAVORITE_FOLDER_CYCLE",
        message="不能将文件夹移动到自身或其子文件夹下",
        status_code=400,
    )


def _folder_depth_exceeded() -> AppException:
    return AppException(
        code="FAVORITE_FOLDER_DEPTH_EXCEEDED",
        message=f"文件夹层级不能超过 {MAX_FOLDER_DEPTH} 级",
        status_code=400,
    )


def _folder_summary(
    folder: FavoriteFolder,
    *,
    item_count: int = 0,
    subfolder_count: int = 0,
) -> FavoriteFolderSummary:
    return FavoriteFolderSummary(
        id=folder.id,
        parent_id=folder.parent_id,
        name=folder.name,
        position=folder.position,
        item_count=item_count,
        subfolder_count=subfolder_count,
        created_at=folder.created_at.replace(tzinfo=UTC),
        updated_at=folder.updated_at.replace(tzinfo=UTC),
    )


def _favorite_item(item: Favorite) -> FavoriteItem:
    return FavoriteItem(
        id=item.id,
        folder_id=item.folder_id,
        kind=cast(FavoriteKind, item.kind),
        title=item.title,
        subtitle=item.subtitle,
        note=item.note,
        source_key=item.source_key,
        payload=item.payload,
        created_at=item.created_at.replace(tzinfo=UTC),
        updated_at=item.updated_at.replace(tzinfo=UTC),
    )


async def _owned_folders(session: AsyncSession, user_id: UUID) -> list[FavoriteFolder]:
    result = await session.scalars(
        select(FavoriteFolder)
        .where(FavoriteFolder.user_id == user_id)
        .order_by(FavoriteFolder.position, FavoriteFolder.created_at, FavoriteFolder.id)
    )
    return list(result)


async def _folder_counts(
    session: AsyncSession, user_id: UUID
) -> tuple[Counter[UUID | None], int]:
    rows = (
        await session.execute(
            select(Favorite.folder_id, func.count())
            .where(Favorite.user_id == user_id)
            .group_by(Favorite.folder_id)
        )
    ).all()
    counts: Counter[UUID | None] = Counter()
    total = 0
    for folder_id, count in rows:
        counts[folder_id] = int(count)
        total += int(count)
    return counts, total


def _children_map(folders: Sequence[FavoriteFolder]) -> dict[UUID | None, list[UUID]]:
    children: dict[UUID | None, list[UUID]] = defaultdict(list)
    for folder in folders:
        children[folder.parent_id].append(folder.id)
    return children


def _descendant_ids(
    children: dict[UUID | None, list[UUID]], root_ids: Sequence[UUID]
) -> set[UUID]:
    visited: set[UUID] = set()
    stack = list(root_ids)
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        stack.extend(children.get(current, ()))
    return visited


def _depth_of(folders_by_id: dict[UUID, FavoriteFolder], folder_id: UUID | None) -> int:
    depth = 0
    current = folder_id
    seen: set[UUID] = set()
    while current is not None:
        if current in seen:
            break
        seen.add(current)
        folder = folders_by_id.get(current)
        if folder is None:
            break
        depth += 1
        current = folder.parent_id
    return depth


def _subtree_height(children: dict[UUID | None, list[UUID]], folder_id: UUID) -> int:
    kids = children.get(folder_id, ())
    if not kids:
        return 1
    return 1 + max(_subtree_height(children, child) for child in kids)


def _check_sibling_name(
    folders: Sequence[FavoriteFolder],
    *,
    parent_id: UUID | None,
    name: str,
    exclude_id: UUID | None = None,
) -> None:
    target = name.casefold()
    for folder in folders:
        if folder.id == exclude_id:
            continue
        if folder.parent_id == parent_id and folder.name.casefold() == target:
            raise _duplicate_folder()


def _article_path(chunk: DocumentChunk) -> str:
    parts = [
        part
        for part in (chunk.chapter, chunk.section, chunk.article, chunk.heading)
        if part
    ]
    if parts:
        return " · ".join(parts)
    if chunk.page_start is not None:
        return f"第 {chunk.page_start} 页"
    return f"片段 #{chunk.ordinal}"


def _build_chunk_snapshot(
    chunk: DocumentChunk, version: DocumentVersion, document: Document
) -> tuple[str, str, dict]:
    path = _article_path(chunk)
    default_title = f"{document.title} · {path}"[:255]
    subtitle_parts = [document.title, path]
    if version.document_number:
        subtitle_parts.append(version.document_number)
    subtitle = " · ".join(subtitle_parts)[:512]
    payload = {
        "chunk_id": chunk.id,
        "document_id": str(document.id),
        "document_title": document.title,
        "issuer": document.issuer,
        "version_id": str(version.id),
        "document_number": version.document_number,
        "version_note": version.version_note,
        "validity_status": version.validity_status,
        "effective_date": (
            version.effective_date.isoformat() if version.effective_date else None
        ),
        "source_url": version.source_url,
        "chapter": chunk.chapter,
        "section": chunk.section,
        "article": chunk.article,
        "heading": chunk.heading,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "content": chunk.content,
    }
    return default_title, subtitle, payload


def _compact_excerpt(text: str, limit: int = 160) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= limit:
        return single_line
    return single_line[: limit - 1] + "…"


async def get_tree(session: AsyncSession, user_id: UUID) -> FavoriteTreeResponse:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        item_counts, total_items = await _folder_counts(session, user_id)
    children = _children_map(folders)
    summaries = [
        _folder_summary(
            folder,
            item_count=item_counts.get(folder.id, 0),
            subfolder_count=len(children.get(folder.id, ())),
        )
        for folder in folders
    ]
    return FavoriteTreeResponse(
        folders=summaries,
        root_item_count=item_counts.get(None, 0),
        total_item_count=total_items,
    )


async def list_contents(
    session: AsyncSession,
    user_id: UUID,
    *,
    folder_id: UUID | None = None,
    scope: FavoriteScope = "root",
    query: str = "",
    kind: FavoriteKind | None = None,
) -> FavoriteListResponse:
    cleaned_query = query.strip()
    async with session.begin():
        all_folders = await _owned_folders(session, user_id)
        folders_by_id = {folder.id: folder for folder in all_folders}
        if folder_id is not None and folder_id not in folders_by_id:
            raise _folder_not_found()
        item_counts, _ = await _folder_counts(session, user_id)
        children = _children_map(all_folders)

        item_stmt = select(Favorite).where(Favorite.user_id == user_id)
        if kind is not None:
            item_stmt = item_stmt.where(Favorite.kind == kind)

        if cleaned_query:
            keyword = cleaned_query.casefold()
            matched_folders = (
                []
                if kind is not None
                else [
                    folder
                    for folder in all_folders
                    if keyword in folder.name.casefold()
                ]
            )
            item_stmt = item_stmt.where(
                or_(
                    Favorite.title.contains(cleaned_query, autoescape=True),
                    Favorite.subtitle.contains(cleaned_query, autoescape=True),
                    Favorite.note.contains(cleaned_query, autoescape=True),
                )
            )
        elif folder_id is not None:
            matched_folders = (
                []
                if kind is not None
                else [folder for folder in all_folders if folder.parent_id == folder_id]
            )
            item_stmt = item_stmt.where(Favorite.folder_id == folder_id)
        elif scope == "all":
            matched_folders = []
        else:
            matched_folders = (
                []
                if kind is not None
                else [folder for folder in all_folders if folder.parent_id is None]
            )
            item_stmt = item_stmt.where(Favorite.folder_id.is_(None))

        items = list(
            await session.scalars(
                item_stmt.order_by(Favorite.created_at.desc(), Favorite.id)
            )
        )

    folder_summaries = [
        _folder_summary(
            folder,
            item_count=item_counts.get(folder.id, 0),
            subfolder_count=len(children.get(folder.id, ())),
        )
        for folder in matched_folders
    ]
    return FavoriteListResponse(
        folders=folder_summaries,
        items=[_favorite_item(item) for item in items],
    )


async def create_folder(
    session: AsyncSession, user_id: UUID, payload: FavoriteFolderCreate
) -> FavoriteFolderSummary:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        folders_by_id = {folder.id: folder for folder in folders}
        if payload.parent_id is not None:
            if payload.parent_id not in folders_by_id:
                raise _folder_not_found()
            if _depth_of(folders_by_id, payload.parent_id) >= MAX_FOLDER_DEPTH:
                raise _folder_depth_exceeded()
        _check_sibling_name(folders, parent_id=payload.parent_id, name=payload.name)
        siblings = [f for f in folders if f.parent_id == payload.parent_id]
        next_pos = max((f.position for f in siblings), default=-1) + 1
        folder = FavoriteFolder(
            user_id=user_id,
            parent_id=payload.parent_id,
            name=payload.name,
            position=next_pos,
        )
        session.add(folder)
        await session.flush()
        return _folder_summary(folder)


async def update_folder(
    session: AsyncSession,
    user_id: UUID,
    folder_id: UUID,
    payload: FavoriteFolderUpdate,
) -> FavoriteFolderSummary:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        folders_by_id = {folder.id: folder for folder in folders}
        folder = folders_by_id.get(folder_id)
        if folder is None:
            raise _folder_not_found()
        children = _children_map(folders)

        target_parent = (
            payload.parent_id
            if "parent_id" in payload.model_fields_set
            else folder.parent_id
        )
        target_name = payload.name if payload.name is not None else folder.name

        if target_parent is not None:
            if target_parent not in folders_by_id:
                raise _folder_not_found()
            if target_parent in _descendant_ids(children, [folder_id]):
                raise _folder_cycle()
            new_depth = _depth_of(folders_by_id, target_parent) + _subtree_height(
                children, folder_id
            )
            if new_depth > MAX_FOLDER_DEPTH:
                raise _folder_depth_exceeded()

        _check_sibling_name(
            folders, parent_id=target_parent, name=target_name, exclude_id=folder_id
        )

        folder.name = target_name
        folder.parent_id = target_parent
        folder.updated_at = utc_now()
        await session.flush()
        item_counts, _ = await _folder_counts(session, user_id)
        return _folder_summary(
            folder,
            item_count=item_counts.get(folder.id, 0),
            subfolder_count=len(children.get(folder.id, ())),
        )


async def delete_folder(session: AsyncSession, user_id: UUID, folder_id: UUID) -> None:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        folders_by_id = {folder.id: folder for folder in folders}
        if folder_id not in folders_by_id:
            raise _folder_not_found()
        children = _children_map(folders)
        to_remove = _descendant_ids(children, [folder_id])
        await session.execute(
            delete(Favorite).where(
                Favorite.user_id == user_id, Favorite.folder_id.in_(to_remove)
            )
        )
        await session.execute(
            delete(FavoriteFolder).where(
                FavoriteFolder.user_id == user_id, FavoriteFolder.id.in_(to_remove)
            )
        )


async def create_favorite(
    session: AsyncSession, user_id: UUID, payload: FavoriteCreate
) -> FavoriteItem:
    async with session.begin():
        if payload.folder_id is not None:
            folder = await session.scalar(
                select(FavoriteFolder).where(
                    FavoriteFolder.id == payload.folder_id,
                    FavoriteFolder.user_id == user_id,
                )
            )
            if folder is None:
                raise _folder_not_found()

        source_key: str | None = None
        if payload.kind == "chunk":
            assert payload.chunk_id is not None
            source_key = f"chunk:{payload.chunk_id}"
            existing = await session.scalar(
                select(Favorite.id).where(
                    Favorite.user_id == user_id, Favorite.source_key == source_key
                )
            )
            if existing is not None:
                raise _duplicate_favorite()
            rows = await DocumentChunkRepository(session).published_with_context(
                [payload.chunk_id]
            )
            if not rows:
                raise _source_not_found()
            chunk, version, document = rows[0]
            default_title, subtitle, snapshot = _build_chunk_snapshot(
                chunk, version, document
            )
            title = payload.title or default_title
        elif payload.kind == "qa":
            assert payload.session_id is not None and payload.turn_id is not None
            source_key = f"qa:{payload.turn_id}"
            existing = await session.scalar(
                select(Favorite.id).where(
                    Favorite.user_id == user_id, Favorite.source_key == source_key
                )
            )
            if existing is not None:
                raise _duplicate_favorite()
            conversation = await session.scalar(
                select(ChatSession).where(
                    ChatSession.id == payload.session_id,
                    ChatSession.user_id == user_id,
                )
            )
            if conversation is None:
                raise _source_not_found()
            events = list(
                await session.scalars(
                    select(ChatSessionEvent)
                    .where(
                        ChatSessionEvent.session_id == payload.session_id,
                        ChatSessionEvent.turn_id == payload.turn_id,
                    )
                    .order_by(ChatSessionEvent.seq)
                )
            )
            if not events:
                raise _source_not_found()
            projected = chat_sessions._project(events)
            if not projected or projected[0].result is None:
                raise _source_not_found()
            turn = projected[0]
            result = turn.result
            assert result is not None
            title = payload.title or turn.question.strip()[:255]
            citation_label = (
                f"依据 {len(result.citations)} 条制度"
                if result.citations
                else "制度问答"
            )
            excerpt = _compact_excerpt(result.answer)
            subtitle = f"{citation_label} · {excerpt}"[:512]
            snapshot = {
                "session_id": str(payload.session_id),
                "turn_id": str(payload.turn_id),
                "question": turn.question,
                "answer": result.answer,
                "status": result.status,
                "citations": [
                    citation.model_dump(mode="json") for citation in result.citations
                ],
            }
        else:
            assert payload.title is not None and payload.url is not None
            title = payload.title
            subtitle = payload.url[:512]
            snapshot = {"url": payload.url}

        item = Favorite(
            user_id=user_id,
            folder_id=payload.folder_id,
            kind=payload.kind,
            title=title,
            subtitle=subtitle,
            note=payload.note,
            source_key=source_key,
            payload=snapshot,
        )
        session.add(item)
        await session.flush()
        return _favorite_item(item)


async def update_favorite(
    session: AsyncSession,
    user_id: UUID,
    favorite_id: UUID,
    payload: FavoriteUpdate,
) -> FavoriteItem:
    async with session.begin():
        item = await session.scalar(
            select(Favorite).where(
                Favorite.id == favorite_id, Favorite.user_id == user_id
            )
        )
        if item is None:
            raise _favorite_not_found()

        if "folder_id" in payload.model_fields_set:
            if payload.folder_id is not None:
                folder = await session.scalar(
                    select(FavoriteFolder.id).where(
                        FavoriteFolder.id == payload.folder_id,
                        FavoriteFolder.user_id == user_id,
                    )
                )
                if folder is None:
                    raise _folder_not_found()
            item.folder_id = payload.folder_id

        if payload.title is not None:
            item.title = payload.title
        if "note" in payload.model_fields_set:
            item.note = payload.note
        if payload.url is not None and item.kind == "link":
            item.subtitle = payload.url[:512]
            item.payload = {**item.payload, "url": payload.url}
        item.updated_at = utc_now()
        await session.flush()
        return _favorite_item(item)


async def delete_favorite(
    session: AsyncSession, user_id: UUID, favorite_id: UUID
) -> None:
    async with session.begin():
        item = await session.scalar(
            select(Favorite).where(
                Favorite.id == favorite_id, Favorite.user_id == user_id
            )
        )
        if item is None:
            raise _favorite_not_found()
        await session.delete(item)


async def lookup_sources(
    session: AsyncSession, user_id: UUID, keys: Sequence[str]
) -> FavoriteLookupResponse:
    cleaned = [key.strip() for key in keys if key.strip()][:200]
    if not cleaned:
        return FavoriteLookupResponse(items={})
    async with session.begin():
        rows = (
            await session.execute(
                select(Favorite.source_key, Favorite.id, Favorite.folder_id).where(
                    Favorite.user_id == user_id,
                    Favorite.source_key.in_(cleaned),
                )
            )
        ).all()
    return FavoriteLookupResponse(
        items={
            source_key: FavoriteSourceMatch(id=fav_id, folder_id=folder_id)
            for source_key, fav_id, folder_id in rows
            if source_key is not None
        }
    )


async def batch_delete(
    session: AsyncSession, user_id: UUID, payload: FavoriteBatchDelete
) -> None:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        owned_folder_ids = {folder.id for folder in folders}
        target_folders = [fid for fid in payload.folder_ids if fid in owned_folder_ids]
        children = _children_map(folders)
        folders_to_remove = _descendant_ids(children, target_folders)

        if folders_to_remove:
            await session.execute(
                delete(Favorite).where(
                    Favorite.user_id == user_id,
                    Favorite.folder_id.in_(folders_to_remove),
                )
            )
            await session.execute(
                delete(FavoriteFolder).where(
                    FavoriteFolder.user_id == user_id,
                    FavoriteFolder.id.in_(folders_to_remove),
                )
            )
        if payload.item_ids:
            await session.execute(
                delete(Favorite).where(
                    Favorite.user_id == user_id, Favorite.id.in_(payload.item_ids)
                )
            )


async def batch_move(
    session: AsyncSession, user_id: UUID, payload: FavoriteBatchMove
) -> None:
    async with session.begin():
        folders = await _owned_folders(session, user_id)
        folders_by_id = {folder.id: folder for folder in folders}
        target_parent = payload.target_folder_id
        if target_parent is not None and target_parent not in folders_by_id:
            raise _folder_not_found()

        children = _children_map(folders)
        moving_folders = [
            folders_by_id[fid]
            for fid in dict.fromkeys(payload.folder_ids)
            if fid in folders_by_id
        ]
        if moving_folders:
            if target_parent is not None:
                descendants = _descendant_ids(
                    children, [folder.id for folder in moving_folders]
                )
                if target_parent in descendants:
                    raise _folder_cycle()
            parent_depth = _depth_of(folders_by_id, target_parent)
            # 校验批量移动后的深度与同级重名
            existing_names = {
                folder.name.casefold()
                for folder in folders
                if folder.parent_id == target_parent
                and folder.id not in {m.id for m in moving_folders}
            }
            for folder in moving_folders:
                if (
                    parent_depth + _subtree_height(children, folder.id)
                    > MAX_FOLDER_DEPTH
                ):
                    raise _folder_depth_exceeded()
                key = folder.name.casefold()
                if key in existing_names:
                    raise _duplicate_folder()
                existing_names.add(key)

            now = utc_now()
            for folder in moving_folders:
                folder.parent_id = target_parent
                folder.updated_at = now

        if payload.item_ids:
            items = list(
                await session.scalars(
                    select(Favorite).where(
                        Favorite.user_id == user_id,
                        Favorite.id.in_(payload.item_ids),
                    )
                )
            )
            now = utc_now()
            for item in items:
                item.folder_id = target_parent
                item.updated_at = now
        await session.flush()

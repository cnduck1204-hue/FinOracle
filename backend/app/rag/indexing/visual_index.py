"""页面视觉索引，支持 PDF 原件和已保存的页面图片。"""

import asyncio
import tempfile
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from app.rag.files import download
from app.rag.indexing.page_store import PageRecord, PageVectorStore
from app.rag.indexing.visual_embedding import VisualEmbeddingProvider
from app.rag.models import IndexTarget, IngestRun, StoredUnit
from app.rag.parsing.errors import source_unsupported
from app.rag.parsing.pages import page_count, render_pages
from app.storage.keys import page_image_key


class VisualIndexer:
    provides = frozenset({"visual"})

    def __init__(
        self, embedding: VisualEmbeddingProvider, store: PageVectorStore
    ) -> None:
        self.embedding = embedding
        self.store = store

    @property
    def model_id(self) -> str:
        return self.embedding.model_id

    async def index(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None:
        if target.pdf_key is None:
            raise source_unsupported()
        await asyncio.to_thread(self.store.ensure_collection)
        await run.run_owned(self.store.delete_by_version, target.version_id)
        with tempfile.TemporaryDirectory(prefix="finoracle-visual-") as directory:
            path = Path(directory) / "source.pdf"
            await asyncio.to_thread(download, run.storage, target.pdf_key, path)
            total = await asyncio.to_thread(page_count, path)
            run.report("visual", 0, total)
            size = max(1, self.embedding.batch_pages)
            for start in range(0, total, size):
                images = await asyncio.to_thread(
                    render_pages, path, start, start + size, self.embedding.render_dpi
                )
                vectors = await self.embedding.encode_images(images)
                records = [
                    PageRecord(target.document_id, target.version_id, start + i + 1, v)
                    for i, v in enumerate(vectors)
                ]
                await run.run_owned(self.store.upsert, records)
                run.report("visual", start + len(images), total)

    def delete(self, version_id: UUID) -> None:
        self.store.delete_by_version(version_id)


class StoredPageIndexer(VisualIndexer):
    """编码对象存储中的页面图片，页码来自入库时写下的切片。"""

    def __init__(
        self,
        embedding: VisualEmbeddingProvider,
        store: PageVectorStore,
        *,
        ingest: str,
    ) -> None:
        super().__init__(embedding, store)
        self.ingest = ingest

    async def index(
        self, run: IngestRun, target: IndexTarget, units: Sequence[StoredUnit]
    ) -> None:
        await asyncio.to_thread(self.store.ensure_collection)
        await run.run_owned(self.store.delete_by_version, target.version_id)
        numbers = [unit.page_start for unit in units if unit.page_start is not None]
        total = len(numbers)
        run.report("visual", 0, total)
        size = max(1, self.embedding.batch_pages)
        for start in range(0, total, size):
            batch = numbers[start : start + size]
            images = await asyncio.to_thread(
                self._read, run, target.document_id, target.version_id, batch
            )
            vectors = await self.embedding.encode_images(images)
            records = [
                PageRecord(target.document_id, target.version_id, number, vector)
                for number, vector in zip(batch, vectors, strict=True)
            ]
            await run.run_owned(self.store.upsert, records)
            run.report("visual", start + len(batch), total)

    def _read(
        self,
        run: IngestRun,
        document_id: UUID,
        version_id: UUID,
        numbers: Sequence[int],
    ) -> list[bytes]:
        images = []
        with tempfile.TemporaryDirectory(prefix="finoracle-page-") as directory:
            for number in numbers:
                path = Path(directory) / f"{number}.png"
                key = page_image_key(document_id, version_id, self.ingest, number)
                download(run.storage, key, path)
                images.append(path.read_bytes())
        return images

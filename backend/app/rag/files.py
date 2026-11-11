"""入库解析和索引共用的原件下载。"""

from pathlib import Path

from app.rag.parsing.errors import file_missing
from app.storage import ObjectStorage


def download(storage: ObjectStorage, key: str, path: Path) -> None:
    try:
        storage.download_file(key, path)
    except FileNotFoundError as error:
        raise file_missing() from error

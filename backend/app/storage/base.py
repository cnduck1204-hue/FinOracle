"""对象存储接口：原件、转换后的 PDF 与解析产物按对象键读写。

方法均为同步阻塞的网络调用：API 中经 run_in_threadpool 调用，worker 在任务线程中
直接调用。对象不存在时抛出 FileNotFoundError；存储服务不可达、凭据或配置错误时抛出
STORAGE_UNAVAILABLE（503），worker 据此判定可重试。
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ObjectInfo:
    key: str
    size: int
    content_type: str | None


class ObjectStorage(ABC):
    @abstractmethod
    def ensure_bucket(self) -> None:
        """桶不存在时创建；可重复调用，并发创建也安全。"""

    @abstractmethod
    def put_file(
        self, key: str, path: Path, *, content_type: str | None = None
    ) -> None:
        """上传本地文件，覆盖同名对象。"""

    @abstractmethod
    def download_file(self, key: str, path: Path) -> None:
        """下载到本地路径；失败时不留下不完整的文件。"""

    @abstractmethod
    def open_object(self, key: str) -> tuple[ObjectInfo, Iterator[bytes]]:
        """读取对象信息并返回按块读取内容的迭代器；对象不存在时立即抛出 FileNotFoundError。

        迭代器读完或被关闭时释放连接，适合直接交给 StreamingResponse。
        """

    @abstractmethod
    def stat(self, key: str) -> ObjectInfo | None:
        """返回对象信息，不存在时返回 None。"""

    @abstractmethod
    def delete(self, key: str) -> None:
        """删除对象；对象或桶不存在时视为已删除。"""


def check_key(key: str) -> str:
    """对象键由服务端按固定规则生成；拒绝空键、绝对路径和上级目录，防止误写其他前缀。"""
    parts = key.split("/")
    if (
        not key
        or key.startswith("/")
        or "\\" in key
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"非法的对象键：{key!r}")
    return key

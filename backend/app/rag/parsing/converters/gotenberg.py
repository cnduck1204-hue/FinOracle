"""Gotenberg：独立容器中的 LibreOffice 转换服务，生产环境使用。"""

import logging
from pathlib import Path

import httpx

from app.rag.parsing.converters.base import ConverterProvider, checked_pdf
from app.rag.parsing.errors import conversion_unavailable, invalid_document

logger = logging.getLogger(__name__)

_CONVERT_ROUTE = "/forms/libreoffice/convert"


class GotenbergConverter(ConverterProvider):
    name = "gotenberg"

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        # 测试时注入模拟传输层；生产使用默认网络连接
        transport: httpx.BaseTransport | None = None,
    ):
        self.endpoint = base_url.rstrip("/") + _CONVERT_ROUTE
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def to_pdf(self, path: Path, workdir: Path) -> Path:
        try:
            with (
                httpx.Client(
                    timeout=self.timeout_seconds, transport=self._transport
                ) as client,
                path.open("rb") as file,
            ):
                # Gotenberg 按文件扩展名判断格式
                response = client.post(
                    self.endpoint, files={"files": (path.name, file)}
                )
        except httpx.TimeoutException as exc:
            raise conversion_unavailable("文档转换服务超时") from exc
        except httpx.HTTPError as exc:
            logger.warning("Gotenberg request failed: %s", exc)
            raise conversion_unavailable("文档转换服务不可用") from exc

        # 400 为文件本身无法转换；503 为 Gotenberg 端超时，其余为服务故障
        if response.status_code == httpx.codes.BAD_REQUEST:
            raise invalid_document("文档无法转换为 PDF")
        if response.status_code != httpx.codes.OK:
            logger.warning(
                "Gotenberg returned %s: %s", response.status_code, response.text[:200]
            )
            raise conversion_unavailable("文档转换服务暂时不可用")

        output = workdir / f"{path.stem}.pdf"
        output.write_bytes(response.content)
        return checked_pdf(output)

"""本机 LibreOffice：未配置 Gotenberg 时使用，适合本地开发和命令行。"""

import shutil
import subprocess
from pathlib import Path

from app.rag.parsing.converters.base import ConverterProvider, checked_pdf
from app.rag.parsing.errors import conversion_unavailable, invalid_document


class LibreOfficeConverter(ConverterProvider):
    name = "libreoffice"

    def __init__(self, soffice_path: str | None, timeout_seconds: float):
        self.soffice_path = soffice_path
        self.timeout_seconds = timeout_seconds

    def to_pdf(self, path: Path, workdir: Path) -> Path:
        executable = self.soffice_path or shutil.which("soffice")
        if executable is None:
            raise conversion_unavailable(
                "转换该格式需要配置 GOTENBERG_URL 或安装 LibreOffice"
            )
        # 独立用户配置目录，避免与本机已打开的 LibreOffice 实例冲突
        profile = (workdir / "profile").as_uri()
        command = [
            executable,
            f"-env:UserInstallation={profile}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workdir),
            str(path),
        ]
        try:
            subprocess.run(
                command, check=True, capture_output=True, timeout=self.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            raise conversion_unavailable("文档转换超时") from exc
        except (subprocess.CalledProcessError, OSError) as exc:
            raise invalid_document("文档无法转换为 PDF") from exc
        return checked_pdf(workdir / f"{path.stem}.pdf")

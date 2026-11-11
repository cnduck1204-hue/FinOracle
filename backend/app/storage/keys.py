"""文档对象键：按版本组织，不含原始文件名（原始文件名记在数据库中）。"""

from uuid import UUID


def version_prefix(document_id: UUID, version_id: UUID) -> str:
    return f"documents/{document_id}/{version_id}"


def original_key(document_id: UUID, version_id: UUID, file_type: str) -> str:
    return f"{version_prefix(document_id, version_id)}/original.{file_type}"


def converted_pdf_key(document_id: UUID, version_id: UUID) -> str:
    """.doc / .rtf / .odt 转换后的 PDF；切片页码以它为准，原文预览也展示它。"""
    return f"{version_prefix(document_id, version_id)}/converted.pdf"


def blocks_key(document_id: UUID, version_id: UUID) -> str:
    """清洗后的文本块，供校验页对照原文。"""
    return f"{version_prefix(document_id, version_id)}/blocks.json"

"""金融制度 PDF 解析；候选文件通过审批和检查后才进入 release。"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import unicodedata
import pdfplumber

VERSION = "2.0.0"
BASE = Path(__file__).resolve().parent
NUM = r"[零〇一二三四五六七八九十百千两0-9]+"
ARTICLE = re.compile(rf"^(第\s*({NUM})\s*条)(.*)$")
CHAPTER = re.compile(rf"^第\s*{NUM}\s*章\s*[^。;；:：,，]{{0,40}}$")
REFERENCE = re.compile(r"^(?:规定|所|的|至|、|和|及|[，,。])")


def normalize_chars(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.translate(str.maketrans({"⺠": "民", "⻔": "门", "⻓": "长"}))


def number(text: str) -> int:
    if text.isdigit():
        return int(text)
    digits = dict(zip("零一二三四五六七八九", range(10))) | {"〇": 0, "两": 2}
    total = digit = 0
    for char in text:
        if char in digits:
            digit = digits[char]
        else:
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
    return total + digit


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def extract_pages(path: Path):
    with pdfplumber.open(path) as pdf:
        pages = [normalize_chars(p.extract_text() or "") for p in pdf.pages]
        stats = [{"page": i, "text_chars": len(text.strip()), "image_count": len(p.images)}
                 for i, (p, text) in enumerate(zip(pdf.pages, pages), 1)]
    return pages, stats


def split_articles(pages: list[str], doc_id: str, profile: dict | None = None):
    """保留条文换行和页码，记录每一行排除理由；支持章/条式文档。"""
    profile = profile or {}
    excluded, chunks, issues = [], [], []
    chapter, active, ended = "", None, False
    patterns = [re.compile(p) for p in profile.get("exclude_fullmatch", [])]
    end_markers = profile.get("end_markers", [])

    def exclude(page, line, reason):
        excluded.append({"page": page, "text": line, "reason": reason})

    def flush():
        nonlocal active
        if active is not None:
            active["content"] = "\n".join(active.pop("lines")).strip()
            chunks.append(active)
            active = None

    for page_no, page in enumerate(pages, 1):
        lines = [s.strip() for s in page.splitlines() if s.strip()]
        for index, line in enumerate(lines):
            if ended:
                exclude(page_no, line, "after_body_end")
                continue
            if (active or chunks) and any(line.startswith(m) for m in end_markers):
                flush()
                ended = True
                exclude(page_no, line, "body_end_marker")
                continue
            browser_header = re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}.*", line)
            browser_footer = re.fullmatch(r"https?://\S+(?:\s+\d+\s*/\s*\d+)?", line)
            page_number = (index < 2 or index >= len(lines) - 2) and re.fullmatch(
                r"(?:[-—]\s*)?\d+(?:\s*/\s*\d+)?(?:\s*[-—])?", line)
            if browser_header or browser_footer or page_number or any(p.fullmatch(line) for p in patterns):
                exclude(page_no, line, "page_or_profile_noise")
                continue
            if CHAPTER.fullmatch(line):
                flush()
                chapter = line
                continue
            match = ARTICLE.match(line)
            if match and not REFERENCE.match(match[3].lstrip()):
                flush()
                n = number(match[2])
                if n != len(chunks) + 1:
                    issues.append({"code": "article_sequence", "page": page_no,
                                   "expected": len(chunks) + 1, "found": n})
                active = {"doc_id": doc_id, "chunk_id": f"{doc_id}_{len(chunks)+1:03d}",
                          "chapter": chapter, "article": re.sub(r"\s+", "", match[1]),
                          "lines": [match[3].strip()] if match[3].strip() else [],
                          "page_start": page_no, "page_end": page_no}
            elif active is not None:
                active["lines"].append(line)
                active["page_end"] = page_no
            else:
                exclude(page_no, line, "outside_article")
    flush()
    if not chunks:
        issues.append({"code": "no_articles"})
    for c in chunks:
        if not c["content"]:
            issues.append({"code": "empty_article", "chunk_id": c["chunk_id"]})
    return chunks, excluded, issues


def process_document(entry: dict, root: Path, output: Path):
    path = (root / entry["file_path"]).resolve()
    doc_id = entry["doc_id"]
    if Path(doc_id).name != doc_id or any(c in doc_id for c in '/\\:*?"<>|'):
        raise ValueError("doc_id 不能包含文件路径字符")
    report = {"doc_id": doc_id, "parser_version": VERSION,
              "file_sha256": sha256(path), "issues": [], "eligible_for_release": False,
              "declared_quality_status": entry.get("quality_status"),
              "known_issues": entry.get("issues", [])}
    if not entry.get("sha256") or entry["sha256"] != report["file_sha256"]:
        report["issues"].append({"code": "unreviewed_file_hash"})
    if path.suffix.lower() != ".pdf":
        report.update(status="needs_conversion", chunk_count=0)
        report["issues"].append({"code": "unsupported_format"})
        return [], report
    pages, stats = extract_pages(path)
    chunks, excluded, problems = split_articles(pages, doc_id, entry.get("parse_profile"))
    report.update(page_count=len(pages), pages=stats, chunk_count=len(chunks))
    report["issues"].extend(problems)
    empty_pages = [s["page"] for s in stats if not s["text_chars"]]
    if empty_pages:
        report["issues"].append({"code": "pages_without_text", "pages": empty_pages})
    expected = entry.get("expected_articles")
    if expected is not None and len(chunks) != expected:
        report["issues"].append({"code": "article_count", "expected": expected, "actual": len(chunks)})
    report["status"] = "needs_ocr_review" if empty_pages else (
        "needs_review" if report["issues"] else "parsed_candidate")
    report["eligible_for_release"] = bool(entry.get("approved_for_integration")
        and entry.get("quality_status") == "approved_for_integration"
        and entry.get("reviewed_parser_version") == VERSION
        and entry.get("source_url") and not report["issues"] and not report["known_issues"] and chunks)
    folder = output / "candidates"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / f"{doc_id}.json", chunks)
    write_json(folder / f"{doc_id}.excluded.json", excluded)
    (folder / f"{doc_id}.raw.txt").write_text("\n\f\n".join(pages), encoding="utf-8")
    (folder / f"{doc_id}.txt").write_text("\n\n".join(
        f"{c['chapter']}\n{c['article']} {c['content']}" for c in chunks), encoding="utf-8")
    return chunks, report


def run(manifest: Path, output: Path) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entries = data["documents"]
    ids = [e["doc_id"] for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("文档清单中 doc_id 重复")
    # 先失效旧发布结果；中断时消费者不会误用上一批次。
    write_json(output / "quality_report.json", {"status": "running", "documents": []})
    write_json(output / "release_chunks.json", [])
    write_json(output / "release_documents.json", [])
    reports, released, docs = [], [], []
    for entry in entries:
        try:
            chunks, report = process_document(entry, manifest.parent, output)
        except Exception as error:
            chunks = []
            report = {"doc_id": entry["doc_id"], "status": "failed", "chunk_count": 0,
                      "eligible_for_release": False,
                      "issues": [{"code": "exception", "message": str(error)}]}
        reports.append(report)
        if report["eligible_for_release"]:
            released.extend(chunks)
            docs.append(entry)
        print(f"{entry['doc_id']}: {report['status']}, {report['chunk_count']} 条, "
              f"入选={report['eligible_for_release']}")
    result = {"status": "completed", "parser_version": VERSION,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "manifest_sha256": sha256(manifest), "document_count": len(entries),
              "release_document_count": len(docs), "release_chunk_count": len(released),
              "documents": reports}
    write_json(output / "release_chunks.json", released)
    write_json(output / "release_documents.json", docs)
    write_json(output / "quality_report.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=BASE / "documents.json")
    parser.add_argument("--output-dir", type=Path, default=BASE / "processed")
    args = parser.parse_args()
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    result = run(args.manifest.resolve(), args.output_dir.resolve())
    return 1 if any(d["status"] == "failed" for d in result["documents"]) else 0


if __name__ == "__main__":
    sys.exit(main())

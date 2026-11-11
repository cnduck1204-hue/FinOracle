"""Parse one local document (PDF/Word/TXT/MD/image) into cleaned chunks as JSON."""

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pydantic import ValidationError

    from app.core.config import get_parsing_settings
    from app.core.exceptions import AppException
    from app.rag.parsing import CHUNKER_CHOICES, OCR_CHOICES
    from app.schemas.document import ParseRequest
    from app.services.document_parsing import parse_document

    defaults = ParseRequest()
    settings = get_parsing_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--ocr", choices=OCR_CHOICES, default=defaults.ocr)
    parser.add_argument("--chunker", choices=CHUNKER_CHOICES, default=defaults.chunker)
    parser.add_argument("--max-chars", type=int, default=defaults.max_chunk_chars)
    parser.add_argument("--overlap", type=int, default=defaults.chunk_overlap)
    parser.add_argument("--blocks", action="store_true", help="include cleaned blocks")
    parser.add_argument(
        "--soffice",
        help="local soffice for .doc/.rtf/.odt when GOTENBERG_URL is unset "
        "(default: SOFFICE_PATH)",
    )
    parser.add_argument("--output", type=Path, help="write JSON here, not stdout")
    args = parser.parse_args(argv)

    if not args.path.is_file():
        print(f"File not found: {args.path}", file=sys.stderr)
        return 2
    try:
        request = ParseRequest(
            ocr=args.ocr,
            chunker=args.chunker,
            max_chunk_chars=args.max_chars,
            chunk_overlap=args.overlap,
            include_blocks=args.blocks,
        )
    except ValidationError as exc:
        for error in exc.errors():
            print(f"Invalid option: {error['msg']}", file=sys.stderr)
        return 2
    if args.soffice:
        settings = settings.model_copy(update={"soffice_path": args.soffice})
    try:
        result = parse_document(args.path, request, settings)
    except AppException as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1

    payload = result.model_dump_json(indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.buffer.write(payload.encode("utf-8"))
        sys.stdout.flush()
    summary = (
        f"{result.status}: {result.stats.chunks} chunks via {result.chunker}, "
        f"OCR {result.stats.ocr_blocks}/{result.stats.ocr_targets}"
    )
    print(summary, *result.warnings, sep="\n", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

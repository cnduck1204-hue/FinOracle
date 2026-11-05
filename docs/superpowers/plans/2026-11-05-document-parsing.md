# Document parsing pipeline (RAG section A)

**Goal:** Turn PDF, Word, TXT/Markdown and image documents into cleaned, citable
chunks: parse → OCR where needed → merge and clean → chunk. This is section A
of the architecture board; storage, embedding and retrieval come later.

**Architecture:** `backend/app/rag/parsing/` with one package per provider seam
on the board: `parsers/` (`ParserProvider`: pdfplumber, python-docx with
LibreOffice for `.doc`, text, Pillow images), `ocr/` (`OCRProvider`: RapidOCR,
PaddleOCR models on ONNX Runtime) and `chunkers/` (`ChunkerProvider`:
`StructuredChunker` with `ArticleChunker` / `ItemChunker`, and `LengthChunker`).
Each package has a `base.py` abstract class, one module per implementation, and
an `__init__.py` registry with a factory (`get_parser`, `create_ocr`,
`create_chunker`), so a new provider is one module plus one registry entry.
Shared `models.py`, `errors.py`, `text_utils.py`, `cleaning.py` and `pipeline.py`
sit at the package root. `parse_file()` returns a `ParseResult` with file hash,
status, blocks, chunks, stats and warnings. `scripts/parse_document.py` is the
offline entry point and takes its choices from the registries.

**Constraints:** No database tables yet; the parse endpoint is a preview that
stores nothing. Leave the `feat/data-collection` branch alone; its sample files
serve only as local verification input. OCR is an optional `ocr` extra so the API image and CI stay
small. A missing or failing OCR yields `needs_ocr`, never `completed`.

## Tasks

- [x] Data model, options and provider protocols.
- [x] PDF text/table extraction in reading order; whole-page OCR for scanned
  pages; region OCR for large images without a text layer; bounded render size.
- [x] Word paragraphs, heading levels, tables with merged cells, embedded
  images; `.doc` conversion in an isolated LibreOffice profile.
- [x] TXT/Markdown with UTF-8/GB18030 detection; multi-frame images.
- [x] Selective NFKC, CJK spacing, page numbers, repeated headers/footers,
  OCR/text-layer duplicates.
- [x] Article chunking by chapter/section/article with cross-reference and
  numbering-regression guards; item chunking for `一、二、` notices; length
  chunking with heading path and overlap; sentence-aware splitting of long text.
- [x] CLI, tests, README.
- [x] Reorganize into `parsers/`, `ocr/`, `chunkers/` packages with base classes
  and registries; tests mirror the packages. Real-sample results unchanged.
- [x] Review: shared defaults and `AUTO` in `models.py`, all error codes in
  `errors.py`, one OCR confidence filter (engine threshold disabled), PDF page
  parsing split into layout steps, clearer cleaning helpers. Real-sample output
  identical, including OCR text.
- [x] Split entry points: shared `ParseRequest` schema and `services/document_parsing.py`;
  admin `POST /admin/documents/parse` (multipart, OpenAPI file field for Apifox) and a
  thin CLI adapter. Verified over real HTTP with curl: all three samples, 415/422
  errors, form options, serialized concurrent requests.
- [x] Config layers: `ParseOptions` and defaults moved to `options.py`; deployment
  config in `core/config.py` (`ParsingSettings`, no secrets, shared by API and CLI;
  values from `.env`); per-request parameters in `schemas/document.py`.
- [x] Gotenberg: `converters/` (`GotenbergConverter`, `LibreOfficeConverter`) turn
  `.doc`/`.rtf`/`.odt` into PDF before parsing, so legacy Word gains page numbers;
  `.docx` stays on python-docx. Compose runs `gotenberg/gotenberg:8.39.0-libreoffice`
  (curl health check, 110 s API timeout, LibreOffice restart every 10 conversions);
  the API depends on it with `service_started`.
- [x] File-header check before conversion: LibreOffice converts unrecognised bytes
  as plain text, so `.doc` must be OLE2, RTF, ZIP or HTML/MHT/XML and `.rtf`/`.odt`
  their own formats; anything else is `DOCUMENT_INVALID` without calling a converter.

## Decisions

RapidOCR instead of PaddlePaddle: same PaddleOCR models, about 30 MB of wheels,
Apache-2.0, no framework install. Chinese full-width punctuation is preserved;
only compatibility characters are NFKC-normalized. Article labels stay in chunk
content, so each chunk reads on its own. A sole remaining line on a page is never
removed as a repeated header.

## Review and verification

- `uv run pytest`: 591 passed, coverage 99% (548 after the package
  reorganization, plus service, API, CLI, settings, converter and file-header
  tests). In a CI-equivalent environment without the `ocr` extra the real-OCR test
  is skipped. Ruff check and format clean;
  `uv lock --check` passes.
- 《证券公司风险处置条例》 text PDF: 64 chunks (preamble + 63 articles) in about
  3 s. All 63 articles match the hand-verified data-branch reference in content,
  chapter and PDF page range after whitespace and NFKC normalization.
- 银发〔2012〕178号 scanned PDF: 4 pages OCR'd, 10 chunks (preamble + items
  一 to 九) with correct page ranges. About 14 s through the CLI including model
  loading, about 9–10 s on a running API server (2–3 s per page). An earlier
  40 s cold run overstated the per-page cost.
- 上期所 `.doc` attachment: converted by local LibreOffice, 23 article chunks,
  about 9 s through the CLI and 2.6 s through the API.
- Converters: the `.doc`, plus `.rtf` and `.odt` exported from it, each give 23
  articles over 7 pages through local LibreOffice. Through a local stand-in that
  implements Gotenberg's documented route over real HTTP, the `.doc` gives the same
  chunks and pages; a stopped service yields `DOCUMENT_CONVERSION_UNAVAILABLE`.
- Real Gotenberg 8.39.0 (LibreOffice 26.8.1, Noto CJK) via Compose: healthy in
  about 6 s, flags applied, non-root user. `.doc`/`.rtf`/`.odt` each give 23
  articles over 7 pages in about 0.5 s through the API. Against local LibreOffice
  26.2 (Microsoft YaHei), `.rtf` is identical; `.doc`/`.odt` shift page ranges of 4
  articles by one page and lose one 、 in 3,581 characters where a line break fell
  before it (absent from the PDF itself, confirmed with pdfium). Random bytes or
  plain text named `.doc` came back as a 200 PDF of garbage, which led to the
  file-header check; afterwards both are rejected with 422. Stopping the container
  gives 503 through the API.

Open items: the preview endpoint parses synchronously and one document at a time;
production uploads need a background job. The API image has no OCR. Gotenberg can
drop a punctuation mark at a forbidden line start; report upstream or add the
documents' original fonts to the image if exact punctuation matters for citations.
Multi-column layouts, tables without borders and appendices that restart article
numbering without restarting chapters are not handled specially.

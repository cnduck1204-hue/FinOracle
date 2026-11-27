"""保留全部非空文本的候选切片；不推断效力、不自动审批。"""
import json
import re
import os
from pathlib import Path
from .store import digest, write_json
from .completeness import classify

VERSION = '1.1.1'
CHAPTER = re.compile(r'^第[零〇一二三四五六七八九十百]+章\s')
ARTICLE = re.compile(r'^第[零〇一二三四五六七八九十百千万0-9]+条(?:\s|　|[、，。]|$)')
SECTION = re.compile(r'^(?:[一二三四五六七八九十百]+[、．.]|[（(][一二三四五六七八九十百0-9]+[）)]|[0-9]+[、．.])')


def split_pages(pages, doc_id):
    lines = [(n, line.strip()) for n, text in enumerate(pages, 1)
             for line in text.splitlines() if line.strip()]
    mode = 'article' if any(ARTICLE.match(line) for _, line in lines) else 'section'
    boundary = ARTICLE if mode == 'article' else SECTION
    chunks, current = [], []
    def flush():
        if current:
            structural = bool(CHAPTER.match(current[0][1])) and mode == 'article'
            chunks.append({'chunk_id':f'{doc_id}_{len(chunks)+1:04d}', 'doc_id':doc_id,
                'kind': 'structure' if structural else (mode if boundary.match(current[0][1]) else 'preamble'),
                'heading':current[0][1], 'content':'\n'.join(s for _,s in current),
                'page_start':current[0][0], 'page_end':current[-1][0]})
    for page, line in lines:
        if boundary.match(line) or (mode == 'article' and CHAPTER.match(line)):
            flush()
            current = []
        current.append((page, line))
    flush()
    context = None
    for index, chunk in enumerate(chunks):
        top_section = mode == 'section' and re.match(r'^[一二三四五六七八九十百]+[、．.]', chunk['heading'])
        if chunk['kind'] == 'structure' or top_section:
            context = chunk['heading']
        if top_section and '\n' not in chunk['content']:
            chunk['kind'] = 'structure'
        chunk['context_heading'] = context
        chunk['retrieval_eligible'] = chunk['kind'] not in ('structure', 'preamble')
        chunk['heading_is_first_line'] = True
    issues = []
    if not any(boundary.match(s) for _, s in lines):
        issues.append('no_structural_headings')
    if any(not text.strip() for text in pages):
        issues.append('empty_pages_require_ocr_review')
    headings = [c['heading'] for c in chunks if c['kind'] != 'preamble']
    if len(headings) != len(set(headings)):
        issues.append('duplicate_headings')
    return chunks, issues


def parse_manifest(manifest, output):
    import pdfplumber
    manifest, output = Path(manifest), Path(output)
    data = json.loads(manifest.read_text('utf-8'))
    ids = [d['doc_id'] for d in data['documents']]
    if len(ids) != len(set(ids)):
        raise ValueError('doc_id 重复')
    # 先使旧结果明确失效，不能将失败前的结果误判为本轮成功。
    write_json(output/'report.json', {'status':'running', 'parser_version':VERSION})
    write_json(output/'chunks.json', [])
    write_json(output/'documents.json', [])
    reviewed_docs = []
    all_chunks, reports = [], []
    for doc in data['documents']:
        report = {'doc_id':doc['doc_id'], 'title':doc['title'], 'status':'needs_review',
                  'approved_for_integration':False, 'chunk_count':0, 'issues':[]}
        try:
            path = (manifest.parent/doc['file_path']).resolve()
            if digest(path.read_bytes()) != doc['sha256']:
                raise ValueError('原件 SHA-256 不匹配')
            with pdfplumber.open(path) as pdf:
                pages = [p.extract_text() or '' for p in pdf.pages]
                excluded = []
                cleaned = list(pages)
                for n, page in enumerate(pdf.pages, 1):
                    lines = pages[n-1].splitlines()
                    if not lines:
                        continue
                    footer = lines[-1].strip()
                    if footer not in (str(n), f'－{n}－', f'-{n}-'):
                        continue
                    bottom = page.crop((0, page.height*0.90, page.width, page.height)).extract_text() or ''
                    if footer not in [s.strip() for s in bottom.splitlines()]:
                        continue
                    excluded.append({'page':n, 'line_index':len(lines)-1, 'text':footer,
                                     'reason':'page_number_in_bottom_10_percent'})
                    cleaned[n-1] = '\n'.join(lines[:-1])
            chunks, issues = split_pages(cleaned, doc['doc_id'])
            completeness = classify(pages)
            if completeness['source_completeness'] == 'abridged':
                issues.append('source_declares_abridged')
            for chunk in chunks:
                chunk.update(source_url=doc['source_url'], sha256=doc['sha256'],
                             revision_id=doc['revision_id'], review_status='needs_review')
                chunk.update(completeness)
            report.update(completeness)
            reviewed_docs.append(doc | completeness | {'review_status':'needs_review',
                'file_path':Path(os.path.relpath(path, output)).as_posix()})
            # 全部非空行均保留，包括前言和页眉页脚；不做不可审计的清理。
            report.update(chunk_count=len(chunks), page_count=len(pages), issues=issues)
            report['pages'] = pages
            report['excluded'] = excluded
            all_chunks.extend(chunks)
        except Exception as exc:
            report.update(status='failed', issues=[str(exc)])
        reports.append(report)
    write_json(output/'chunks.json', all_chunks)
    write_json(output/'documents.json', reviewed_docs)
    result = {'status':'completed', 'parser_version':VERSION,
              'manifest_sha256':digest(manifest.read_bytes()), 'documents':reports,
              'chunk_count':len(all_chunks), 'released_count':0}
    write_json(output/'report.json', result)
    return result

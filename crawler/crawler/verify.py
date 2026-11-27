import json
from pathlib import Path
from .store import digest
from .completeness import classify


def verify(root):
    root = Path(root)
    manifest = root/'staging/documents.json'
    data = json.loads(manifest.read_text('utf-8'))
    docs = {d['doc_id']:d for d in data['documents']}
    if len(docs) != len(data['documents']):
        raise ValueError('文档 ID 重复')
    for doc in docs.values():
        if doc['approved_for_integration'] is not False:
            raise ValueError('候选数据不允许自动审批')
        if digest((manifest.parent/doc['file_path']).read_bytes()) != doc['sha256']:
            raise ValueError('原件哈希不匹配')
    output = root/'staging/parsed_v1'
    report = json.loads((output/'report.json').read_text('utf-8'))
    if report.get('released_count') != 0:
        raise ValueError('候选报告不得声明发布')
    chunks = json.loads((output/'chunks.json').read_text('utf-8'))
    reviewed = json.loads((output/'documents.json').read_text('utf-8'))
    reviewed_by_id = {d['doc_id']:d for d in reviewed}
    if len(reviewed_by_id) != len(reviewed) or set(reviewed_by_id) != set(docs):
        raise ValueError('完整性清单文档不一致')
    if report['status'] != 'completed' or report['manifest_sha256'] != digest(manifest.read_bytes()):
        raise ValueError('解析未完成或清单已变化')
    if len(chunks) != report['chunk_count'] or len({c['chunk_id'] for c in chunks}) != len(chunks):
        raise ValueError('切片数量或 ID 异常')
    grouped = {key:[] for key in docs}
    for chunk in chunks:
        grouped[chunk['doc_id']].append(chunk)
    if {r['doc_id'] for r in report['documents']} != set(docs):
        raise ValueError('解析报告文档不一致')
    if len(report['documents']) != len(docs):
        raise ValueError('解析报告文档重复')
    for r in report['documents']:
        if r['status'] == 'failed':
            raise ValueError('存在解析失败文档')
        selected = grouped[r['doc_id']]
        reviewed_doc = reviewed_by_id[r['doc_id']]
        if reviewed_doc['sha256'] != docs[r['doc_id']]['sha256'] or digest((output/reviewed_doc['file_path']).read_bytes()) != reviewed_doc['sha256']:
            raise ValueError('审核清单原件关联不一致')
        expected_completeness = classify(r['pages'])
        for record in [r, reviewed_by_id[r['doc_id']], *selected]:
            if any(record.get(key) != value for key,value in expected_completeness.items()):
                raise ValueError('完整性标记或审批状态不一致')
        if len(selected) != r['chunk_count']:
            raise ValueError('文档切片数量不一致')
        for c in selected:
            if c.get('review_status') != 'needs_review' or c['revision_id'] != docs[r['doc_id']]['revision_id']:
                raise ValueError('切片审核状态或版本不一致')
            if c['source_url'] != docs[r['doc_id']]['source_url']:
                raise ValueError('切片来源不一致')
            if c.get('retrieval_eligible') != (c['kind'] in ('article','section')):
                raise ValueError('结构块检索标记错误')
            if not 1 <= c['page_start'] <= c['page_end'] <= r['page_count']:
                raise ValueError('页码越界')
            if c['sha256'] != docs[r['doc_id']]['sha256'] or not c['content'].strip():
                raise ValueError('切片证据或正文异常')
        actual = '\n'.join(c['content'] for c in selected)
        lines = [page.splitlines() for page in r['pages']]
        excluded = set()
        for item in r.get('excluded', []):
            n, idx = item['page'], item['line_index']
            if (n,idx) in excluded or lines[n-1][idx].strip() != item['text']:
                raise ValueError('排除记录与原始文本不一致')
            if item['text'] not in (str(n),f'－{n}－',f'-{n}-') or idx != len(lines[n-1])-1:
                raise ValueError('不允许排除非页尾页码')
            excluded.add((n,idx))
        expected = '\n'.join(s.strip() for n,page in enumerate(lines,1) for idx,s in enumerate(page)
                             if s.strip() and (n,idx) not in excluded)
        if actual != expected:
            raise ValueError('候选切片未完整保留提取文本')
    return {'result':'passed', 'documents':len(docs), 'chunks':len(chunks),
            'released':0, 'note':'结构与哈希校验；不代表全文人工审核或法律效力确认'}

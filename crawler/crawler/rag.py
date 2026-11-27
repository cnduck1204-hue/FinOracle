"""隔离接入与词面检索基线；不调用模型、不向正式向量库写入。"""
import json
import math
import re
from collections import Counter
from pathlib import Path
from .store import digest, write_json
from .verify import verify


def adapt(documents, chunks, parser_version):
    docs = {d['doc_id']:d for d in documents}
    records = []
    for chunk in chunks:
        if chunk['kind'] not in ('article', 'section') or not chunk['retrieval_eligible']:
            continue
        doc = docs[chunk['doc_id']]
        if chunk['approved_for_integration'] or doc['approved_for_integration']:
            raise ValueError('本适配器仅接受待审隔离数据')
        if chunk['sha256'] != doc['sha256'] or chunk['source_completeness'] != doc['source_completeness']:
            raise ValueError('文档与切片版本或完整性不一致')
        citation = dict(title=doc['title'], source_url=doc['source_url'],
                        download_url=doc['download_url'], page_start=chunk['page_start'],
                        page_end=chunk['page_end'], sha256=chunk['sha256'])
        # 内容、解析器与源版本变化均生成新索引键，不能覆盖旧条文。
        identity = json.dumps([chunk['doc_id'], parser_version, chunk['chunk_id'], chunk['content']], ensure_ascii=False)
        records.append({'id':digest(identity.encode('utf-8')), 'source_chunk_id':chunk['chunk_id'],
            'doc_id':chunk['doc_id'], 'revision_id':chunk['revision_id'], 'parser_version':parser_version,
            'text':chunk['content'], 'context_heading':chunk.get('context_heading'), 'kind':chunk['kind'],
            'citation':citation, 'source_completeness':chunk['source_completeness'],
            'completeness_notice':chunk['completeness_notice'], 'review_status':'needs_review',
            'approved_for_integration':False, 'environment':'sandbox',
            'validity_status':doc.get('validity_status','unknown')})
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('索引键重复')
    return records


def export_sandbox(root):
    root = Path(root)
    output = root/'sandbox'
    output.mkdir(parents=True, exist_ok=True)
    # 旧快照显式失效；消费方必须要求 manifest.status=completed。
    write_json(output/'manifest.json', {'status':'running','environment':'sandbox'})
    verify(root)
    source = root/'staging/parsed_v1'
    docs = json.loads((source/'documents.json').read_text('utf-8'))
    chunks = json.loads((source/'chunks.json').read_text('utf-8'))
    report = json.loads((source/'report.json').read_text('utf-8'))
    records = adapt(docs, chunks, report['parser_version'])
    temp = output/'records.jsonl.tmp'
    temp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')
    temp.replace(output/'records.jsonl')
    summary = {'status':'completed', 'schema_version':'rag-sandbox-1.0', 'environment':'sandbox',
               'records':len(records), 'excluded_structure_or_preamble':len(chunks)-len(records),
               'input_sha256':{name:digest((source/name).read_bytes()) for name in ('documents.json','chunks.json','report.json')},
               'records_sha256':digest((output/'records.jsonl').read_bytes()), 'released':0}
    write_json(output/'manifest.json', summary)
    return summary


def tokens(text):
    text = re.sub(r'\s+', '', text.lower())
    return Counter(text[i:i+2] for i in range(len(text)-1))


def search(records, query, top_k=5):
    """中文二元字符 TF-IDF 余弦基线，只返回证据，不生成答案。"""
    counts = [tokens(r['text']) for r in records]
    df = Counter(t for count in counts for t in count)
    def vector(count):
        return {t:(1+math.log(n))* (math.log((len(records)+1)/(df.get(t,0)+1))+1) for t,n in count.items()}
    q = vector(tokens(query))
    qnorm = math.sqrt(sum(v*v for v in q.values())) or 1
    ranked = []
    for record, count in zip(records,counts):
        v = vector(count)
        score = sum(value*v.get(t,0) for t,value in q.items())/(qnorm*(math.sqrt(sum(x*x for x in v.values())) or 1))
        if score > 0:
            ranked.append({'score':score,'record':record})
    return sorted(ranked,key=lambda x:(-x['score'],x['record']['id']))[:top_k]


def evaluate(root, cases_path):
    # 每次重新核验并导出，避免评测陈旧候选。
    summary = export_sandbox(root)
    output = Path(root)/'sandbox'
    records = [json.loads(line) for line in (output/'records.jsonl').read_text('utf-8').splitlines()]
    cases = json.loads(Path(cases_path).read_text('utf-8'))
    results = []
    for case in cases:
        hits = search(records,case['question'])
        matches = [i+1 for i,h in enumerate(hits) if h['record']['doc_id']==case['expected_doc_id']
                   and re.sub(r'\s','',h['record']['text']).startswith(case['expected_prefix'])]
        results.append({'question':case['question'], 'expected_doc_id':case['expected_doc_id'],
                        'expected_prefix':case['expected_prefix'], 'rank':matches[0] if matches else None,
                        'top5':hits})
    result = {'method':'character-bigram-tfidf-cosine', 'cases':len(results),
              'hit_at_5':sum(r['rank'] is not None for r in results),
              'hit_at_1':sum(r['rank']==1 for r in results),
              'records':summary['records'], 'results':results,
              'limits':'固定小样本词面检索，不代表向量召回、生成答案质量或实际 RAG 联调通过'}
    write_json(output/'evaluation.json',result)
    return {k:v for k,v in result.items() if k!='results'}

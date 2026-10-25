"""检查交付数据关联、哈希、页码、条文完整性及联调引用。"""
import json
from pathlib import Path
import re
from collection_pipeline import BASE, sha256, normalize_chars, number, VERSION


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def validate(root=BASE):
    manifest=load(root/'documents.json')
    report=load(root/'processed/quality_report.json')
    chunks=load(root/'processed/release_chunks.json')
    documents=load(root/'processed/release_documents.json')
    examples=load(root/'qa_examples.json')['examples']
    assert report['status']=='completed', '处理尚未完成'
    assert report['manifest_sha256']==sha256(root/'documents.json'), '清单已更改，请重跑解析'
    assert report['parser_version']==VERSION, '解析器版本不符'
    assert len(documents)==report['release_document_count']
    assert len(chunks)==report['release_chunk_count']
    docs={e['doc_id']:e for e in manifest['documents']}
    released={e['doc_id']:e for e in documents}
    assert len(docs)==len(manifest['documents']), '文档ID重复'
    assert len({c['chunk_id'] for c in chunks})==len(chunks), '切片ID重复'
    for e in docs.values():
        assert (root/e['file_path']).is_file(), e['file_path']
        assert e['sha256']==sha256(root/e['file_path']), '原件已变更: '+e['doc_id']
    reports={r['doc_id']:r for r in report['documents']}
    for c in chunks:
        assert c['doc_id'] in released
        assert c['content'].strip()
        assert re.fullmatch(r'第[零〇一二三四五六七八九十百千两0-9]+条',c['article'])
        assert 1<=c['page_start']<=c['page_end']<=reports[c['doc_id']]['page_count']
    for doc_id,e in released.items():
        assert e==docs[doc_id], '发布文档与清单不同'
        assert reports[doc_id]['eligible_for_release'] and e['approved_for_integration']
        selected=[c for c in chunks if c['doc_id']==doc_id]
        assert len(selected)==e['expected_articles']
        assert [number(c['article'][1:-1]) for c in selected]==list(range(1,len(selected)+1))
        candidate=load(root/'processed/candidates'/f'{doc_id}.json')
        assert selected==candidate, '发布切片不等于校对候选'
    by_id={c['chunk_id']:c for c in chunks}
    clean=lambda s:re.sub(r'\s+','',normalize_chars(s))
    for q in examples:
        for ref in q['references']:
            c=by_id[ref['chunk_id']]
            assert ref['doc_id']==c['doc_id'] and ref['article']==c['article']
            assert clean(ref['evidence_text']) in clean(c['content']), q['id']
            assert (ref['page_start'],ref['page_end'])==(c['page_start'],c['page_end'])
    assert not any(e['extraction_type']=='scanned' for e in documents)
    return {'result':'passed','documents_registered':len(docs),'documents_released':len(documents),
            'chunks_released':len(chunks),'qa_examples':len(examples)}


if __name__=='__main__':
    print(json.dumps(validate(),ensure_ascii=False,indent=2))

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collection_pipeline import split_articles, normalize_chars, run, process_document, sha256, VERSION


class ParserTests(unittest.TestCase):
    def test_no_space_and_keywords_preserved(self):
        chunks, _, issues = split_articles([
            "第一章 总则\n第一条应遵守法律法规，履行信息公开义务。\n第二条 依法制定货币政策。"], "d")
        self.assertEqual([c['article'] for c in chunks], ['第一条', '第二条'])
        self.assertIn('法律法规', chunks[0]['content'])
        self.assertFalse(issues)

    def test_reference_and_cross_page(self):
        chunks, _, issues = split_articles([
            "第一条 本办法依照\n第三条规定处理。", "续行内容。\n第二条 下一条。"], 'd')
        self.assertEqual(len(chunks), 2)
        self.assertIn('第三条规定处理', chunks[0]['content'])
        self.assertEqual((chunks[0]['page_start'], chunks[0]['page_end']), (1, 2))
        self.assertFalse(issues)

    def test_chapter_change(self):
        chunks, _, _ = split_articles(['第一章 总则\n第一条 内容。\n第二章 管理\n第二条 内容。'], 'd')
        self.assertEqual(chunks[0]['chapter'], '第一章 总则')
        self.assertEqual(chunks[1]['chapter'], '第二章 管理')

    def test_noise_logged(self):
        chunks, excluded, _ = split_articles([
            '2026/10/17 10:00 页眉\n第一条 正文。\nhttps://example.com/a 1/2',
            '续文。\n本站声明\n不能进入正文'], 'd', {'end_markers': ['本站声明']})
        self.assertEqual(chunks[0]['content'], '正文。\n续文。')
        self.assertEqual(len(excluded), 4)

    def test_empty_and_duplicate_flagged(self):
        chunks, _, issues = split_articles(['第一条\n第一条 重复。'], 'd')
        self.assertEqual(len(chunks), 2)
        self.assertEqual({x['code'] for x in issues}, {'article_sequence', 'empty_article'})

    def test_normalization(self):
        self.assertEqual(normalize_chars('第⼀条 ⺠ ⻔ ⻓'), '第一条 民 门 长')

    def test_unapproved_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'documents.json').write_text(json.dumps({'documents': [{'doc_id': 'd'}]}))
            with patch('collection_pipeline.process_document', return_value=([{'doc_id': 'd'}], {
                'doc_id': 'd', 'status': 'needs_review', 'chunk_count': 1,
                'eligible_for_release': False})):
                run(root/'documents.json', root/'out')
            self.assertEqual(json.loads((root/'out/release_chunks.json').read_text()), [])

    def test_failure_invalidates_old_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'release_chunks.json').write_text('[{"stale": true}]')
            (root/'documents.json').write_text(json.dumps({'documents': [{'doc_id':'d','file_path':'missing.pdf'}]}))
            result = run(root/'documents.json', root)
            self.assertEqual(json.loads((root/'release_chunks.json').read_text()), [])
            self.assertEqual(result['documents'][0]['status'], 'failed')

    def test_hash_or_parser_change_prevents_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'d.pdf').write_bytes(b'fixture')
            entry = dict(doc_id='d',file_path='d.pdf',sha256=sha256(root/'d.pdf'),
                         approved_for_integration=True,quality_status='approved_for_integration',
                         reviewed_parser_version=VERSION,source_url='https://example.com',expected_articles=1)
            with patch('collection_pipeline.extract_pages', return_value=(['第一条 正文。'],
                       [{'page':1,'text_chars':7,'image_count':0}])):
                self.assertTrue(process_document(entry,root,root/'out')[1]['eligible_for_release'])
                entry['sha256'] = 'changed'
                self.assertFalse(process_document(entry,root,root/'out')[1]['eligible_for_release'])
                entry['sha256'] = sha256(root/'d.pdf')
                entry['reviewed_parser_version'] = 'old'
                self.assertFalse(process_document(entry,root,root/'out')[1]['eligible_for_release'])

    def test_mixed_scan_pages_prevent_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'d.pdf').write_bytes(b'fixture')
            entry=dict(doc_id='d',file_path='d.pdf',sha256=sha256(root/'d.pdf'),
                       approved_for_integration=True,quality_status='approved_for_integration',
                       reviewed_parser_version=VERSION,source_url='https://example.com')
            with patch('collection_pipeline.extract_pages', return_value=(['第一条 正文。',''],
                       [{'page':1,'text_chars':7,'image_count':0},{'page':2,'text_chars':0,'image_count':1}])):
                _, report=process_document(entry,root,root/'out')
                self.assertEqual(report['status'],'needs_ocr_review')
                self.assertFalse(report['eligible_for_release'])


if __name__ == '__main__':
    unittest.main()

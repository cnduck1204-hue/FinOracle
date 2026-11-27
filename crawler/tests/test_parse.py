import unittest
from crawler.parse import split_pages
from crawler.completeness import classify


class ParsingTests(unittest.TestCase):
    def test_abridged_declaration_across_line(self):
        result = classify(['正文', '（本文有\n删减）'])
        self.assertEqual(result['source_completeness'], 'abridged')
        self.assertEqual(result['completeness_evidence'][0]['page'], 2)
        self.assertFalse(result['approved_for_integration'])

    def test_no_declaration_does_not_prove_complete(self):
        self.assertEqual(classify(['正常正文'])['source_completeness'], 'unknown')

    def test_chapter_not_attached_to_previous_article(self):
        chunks, _ = split_pages(['第一章 总则\n第一条 正文\n第二章 管理\n第二条 其他'], 'd')
        self.assertEqual(chunks[1]['content'], '第一条 正文')
        self.assertEqual(chunks[2]['kind'], 'structure')
        self.assertFalse(chunks[2]['retrieval_eligible'])
        self.assertEqual(chunks[3]['context_heading'], '第二章 管理')

    def test_section_context_for_child(self):
        chunks, _ = split_pages(['二、监管\n（一）措施\n内容'], 'd')
        self.assertEqual(chunks[0]['kind'], 'structure')
        self.assertEqual(chunks[1]['context_heading'], '二、监管')

    def test_section_content_and_cross_page(self):
        pages = ['文件标题\n一、总体要求\n正文甲\n（二）具体措施\n正文乙', '接续正文\n二、保障\n正文丙']
        chunks, issues = split_pages(pages, 'd')
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[2]['page_end'], 2)
        self.assertEqual('\n'.join(c['content'] for c in chunks), '\n'.join(pages))
        self.assertEqual(issues, [])

    def test_article_priority_and_empty_page(self):
        chunks, issues = split_pages(['标题\n第一条 内容\n（一）子项', '', '第二条 内容'], 'd')
        self.assertEqual(len(chunks), 3)
        self.assertIn('（一）子项', chunks[1]['content'])
        self.assertIn('empty_pages_require_ocr_review', issues)

    def test_no_heading_retains_text(self):
        chunks, issues = split_pages(['只有普通正文'], 'd')
        self.assertEqual(chunks[0]['content'], '只有普通正文')
        self.assertIn('no_structural_headings', issues)

    def test_duplicate_heading(self):
        _, issues = split_pages(['一、说明\n甲\n一、说明\n乙'], 'd')
        self.assertIn('duplicate_headings', issues)

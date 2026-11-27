import unittest
from crawler.rag import adapt, search


class RagTests(unittest.TestCase):
    def setUp(self):
        self.doc = dict(doc_id='d',sha256='abc',title='标题',source_url='https://example.org/a',
                        download_url='https://example.org/a.pdf',source_completeness='abridged',
                        approved_for_integration=False)
        self.chunk = dict(doc_id='d',chunk_id='d_1',sha256='abc',revision_id='abc',
                          kind='article',retrieval_eligible=True,approved_for_integration=False,
                          source_completeness='abridged',completeness_notice='公开删减版',
                          content='第一条 审计收费不得以审计结果作为条件。',page_start=1,page_end=2)

    def test_preserves_citation_and_completeness(self):
        record=adapt([self.doc],[self.chunk],'1.1.1')[0]
        self.assertEqual(record['citation']['page_end'],2)
        self.assertEqual(record['source_completeness'],'abridged')
        self.assertEqual(record['environment'],'sandbox')
        self.assertFalse(record['approved_for_integration'])

    def test_structure_never_exported(self):
        self.chunk['kind']='structure'
        self.assertEqual(adapt([self.doc],[self.chunk],'1'),[])

    def test_new_parser_and_content_change_index_id(self):
        first=adapt([self.doc],[self.chunk],'1')[0]['id']
        self.assertNotEqual(first,adapt([self.doc],[self.chunk],'2')[0]['id'])
        self.chunk['content']+='新增文字'
        self.assertNotEqual(first,adapt([self.doc],[self.chunk],'1')[0]['id'])

    def test_approval_and_wrong_revision_rejected(self):
        self.chunk['approved_for_integration']=True
        with self.assertRaises(ValueError):adapt([self.doc],[self.chunk],'1')
        self.chunk['approved_for_integration']=False
        self.chunk['sha256']='different'
        with self.assertRaises(ValueError):adapt([self.doc],[self.chunk],'1')

    def test_search_returns_evidence_not_generated_answer(self):
        rows=adapt([self.doc],[self.chunk],'1')
        result=search(rows,'审计收费与审计结果')
        self.assertEqual(result[0]['record']['text'],self.chunk['content'])
        self.assertNotIn('answer',result[0])
        self.assertEqual(search(rows,''),[])

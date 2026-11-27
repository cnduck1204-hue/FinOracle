import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from crawler.config import load
from crawler.engine import run
from crawler.store import Store
from crawler.adapters import szse_links


class IntegrationTests(unittest.TestCase):
    def test_szse_script_links(self):
        text = '''<script>
        var curHref = './file.pdf';
        //var curTitle = '旧标题';
        var curTitle = '证券 &amp; 规则';
        document.write('not executed');
        </script>'''
        self.assertEqual(szse_links(text), [('./file.pdf', '证券 & 规则')])

    def test_task_limit_is_reported(self):
        self.config['max_tasks'] = 2
        result = run(self.store, self.config)
        self.assertEqual(result['states'], {'done':2})
        self.assertEqual(result['skipped_task_limit'], 2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.version = b'%PDF-1.4\nfixture-v1\n%%EOF'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                status = 200
                content_type = 'text/html; charset=utf-8'
                if self.path == '/robots.txt':
                    body = b'User-agent: *\nDisallow: /blocked.pdf\n'
                elif self.path == '/':
                    body = b'<a href="/detail">detail</a><a href="/detail#x">duplicate</a>'
                elif self.path == '/detail':
                    body = '<a href="/doc.pdf">测试制度</a><a href="/copy.pdf">副本</a>'.encode()
                elif self.path in ('/doc.pdf', '/copy.pdf'):
                    body = owner.version
                    content_type = 'application/pdf'
                elif self.path == '/retry.pdf':
                    status, body = 429, b'busy'
                elif self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://example.org/escape.pdf')
                    self.end_headers()
                    return
                else:
                    body = b'<html>login</html>'
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                if status == 429:
                    self.send_header('Retry-After', '30')
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        path = Path(self.temp.name) / 'config.json'
        path.write_text(json.dumps(dict(name='test', allowed_hosts=['127.0.0.1'],
            seeds=[self.base+'/'], interval=0, link_pattern='/detail|\\.pdf$', max_depth=3)), encoding='utf-8')
        self.config = load(path)
        self.store = Store(Path(self.temp.name) / 'data')

    def tearDown(self):
        self.store.db.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def seed(self, path):
        self.config['seeds'] = [self.base + path]

    def test_discovery_dedup_export_and_versions(self):
        first = run(self.store, self.config)
        self.assertEqual(first['states'], {'done':4})
        self.assertEqual(len(list((self.store.root/'raw').glob('*.pdf'))), 1)
        self.assertEqual(run(self.store, self.config)['downloaded'], 0)
        self.assertEqual(run(self.store, self.config, True)['unchanged'], 4)
        self.version = b'%PDF-1.4\nfixture-v2\n%%EOF'
        run(self.store, self.config, True)
        docs = self.store.export()['documents']
        self.assertEqual(len(docs), 4)
        self.assertEqual(len({d['doc_id'] for d in docs}), 4)
        for d in docs:
            self.assertFalse(d['approved_for_integration'])
            self.assertTrue((self.store.root/'staging'/d['file_path']).is_file())
        # 种子自身、列表到详情、详情到两个附件；片段重复链接合并。
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM edges').fetchone()[0], 4)

    def test_fake_pdf_and_robots_denial(self):
        self.config['seeds'] = [self.base+'/fake.pdf', self.base+'/blocked.pdf']
        result = run(self.store, self.config)
        self.assertEqual(result['states'], {'quarantined':2})
        self.assertEqual(self.store.export()['documents'], [])

    def test_retry_after_persisted_and_bounded(self):
        self.seed('/retry.pdf')
        self.config['max_attempts'] = 2
        start = time.time()
        self.assertEqual(run(self.store,self.config)['states'], {'retry':1})
        row = self.store.db.execute('SELECT * FROM tasks').fetchone()
        self.assertGreaterEqual(row['due'], start+30)
        self.assertEqual(run(self.store,self.config)['failed'], 0)
        self.store.db.execute('UPDATE tasks SET due=0')
        self.store.db.commit()
        self.assertEqual(run(self.store,self.config)['states'], {'quarantined':1})

    def test_interrupted_task_resumes(self):
        self.seed('/doc.pdf')
        self.store.enqueue(self.base+'/doc.pdf', self.base+'/', '恢复', 0, 10)
        self.store.db.execute("UPDATE tasks SET status='fetching'")
        self.store.db.commit()
        self.assertEqual(run(self.store,self.config)['states'], {'done':1})

    def test_cross_host_redirect_blocked(self):
        self.seed('/redirect')
        self.assertEqual(run(self.store,self.config)['states'], {'quarantined':1})

    def test_export_rejects_changed_original(self):
        self.seed('/doc.pdf')
        run(self.store,self.config)
        path = next((self.store.root/'raw').glob('*.pdf'))
        path.write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, '哈希'):
            self.store.export()

    def test_download_size_limit(self):
        self.seed('/doc.pdf')
        self.config['max_bytes'] = 10
        self.config['respect_robots'] = False
        self.assertEqual(run(self.store,self.config)['states'], {'quarantined':1})


if __name__ == '__main__':
    unittest.main()

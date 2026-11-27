import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'state.db')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS tasks(
          url TEXT PRIMARY KEY, source TEXT, title TEXT, depth INTEGER,
          status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
          due REAL DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS revisions(
          url TEXT, sha256 TEXT, path TEXT, kind TEXT, final_url TEXT,
          headers TEXT, fetched_at REAL, PRIMARY KEY(url, sha256));
        CREATE TABLE IF NOT EXISTS edges(
          source TEXT, target TEXT, PRIMARY KEY(source,target));
        ''')

    def enqueue(self, url, source, title, depth, limit):
        self.db.execute('INSERT OR IGNORE INTO edges VALUES (?,?)', (source, url))
        if self.db.execute('SELECT 1 FROM tasks WHERE url=?', (url,)).fetchone():
            return True
        if self.db.execute('SELECT count(*) FROM tasks').fetchone()[0] < limit:
            self.db.execute('INSERT OR IGNORE INTO tasks(url,source,title,depth) VALUES(?,?,?,?)',
                            (url, source, title, depth))
            return True
        return False

    def save(self, url, body, kind, final, headers):
        sha = digest(body)
        path = self.root / 'raw' / (sha + '.' + kind)
        path.parent.mkdir(exist_ok=True)
        if not path.exists() or digest(path.read_bytes()) != sha:
            tmp = path.with_suffix('.tmp')
            tmp.write_bytes(body)
            tmp.replace(path)
        old = self.db.execute('SELECT 1 FROM revisions WHERE url=? AND sha256=?', (url, sha)).fetchone()
        self.db.execute('INSERT OR REPLACE INTO revisions VALUES(?,?,?,?,?,?,?)',
                        (url, sha, str(path), kind, final, json.dumps(headers), time.time()))
        return bool(old)

    def export(self):
        docs = []
        rows = self.db.execute('''SELECT r.*,t.title,t.source FROM revisions r JOIN tasks t USING(url)
          WHERE r.kind='pdf' ORDER BY r.url,r.sha256''')
        for r in rows:
            # 内容寻址路径使整个数据目录可以移动和打包。
            path = self.root / 'raw' / (r['sha256'] + '.' + r['kind'])
            if not path.is_file() or digest(path.read_bytes()) != r['sha256']:
                raise ValueError('原件缺失或哈希不匹配: ' + str(path))
            docs.append(dict(doc_id=digest(r['url'].encode())[:16]+'_'+r['sha256'][:16],
                title=r['title'] or r['url'], source_url=r['source'] or r['url'],
                download_url=r['url'], file_path='../raw/' + path.name, file_type='pdf',
                sha256=r['sha256'], validity_status='unknown', quality_status='needs_review',
                approved_for_integration=False, reviewed_parser_version=None,
                expected_articles=None, issues=[], parse_profile={},
                revision_id=r['sha256'],
                collected_at=datetime.fromtimestamp(r['fetched_at'], timezone.utc).isoformat()))
        result = {'schema_version':'1.0.0', 'documents':docs}
        write_json(self.root / 'staging' / 'documents.json', result)
        return result

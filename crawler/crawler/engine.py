import re
import time
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from .config import check_url
from .fetch import Fetcher
from .store import write_json
from .adapters import szse_links


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.href = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href = dict(attrs).get('href')
            self.text = []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.links.append((self.href, ''.join(self.text).strip()))
            self.href = None


def run(store, config, refresh=False):
    db = store.db
    # 单进程锁由 CLI 持有；遗留 fetching 表示上次运行中断。
    db.execute("UPDATE tasks SET status='pending' WHERE status='fetching'")
    if refresh:
        db.execute("UPDATE tasks SET status='pending',attempts=0,due=0,error=NULL WHERE status='done'")
    for url in config['seeds']:
        store.enqueue(check_url(url, config), url, '', 0, config['max_tasks'])
    db.commit()
    fetcher = Fetcher(config)
    counts = {'downloaded':0, 'unchanged':0, 'failed':0, 'skipped_task_limit':0}
    while True:
        task = db.execute("SELECT * FROM tasks WHERE status IN ('pending','retry') AND due<=? ORDER BY depth,url LIMIT 1", (time.time(),)).fetchone()
        if task is None:
            break
        url = task['url']
        attempt = task['attempts'] + 1
        db.execute("UPDATE tasks SET status='fetching',attempts=? WHERE url=?", (attempt,url))
        db.commit()
        try:
            body, final, headers, encoding = fetcher.get(url)
            if body.startswith(b'%PDF-'):
                if b'%%EOF' not in body[-2048:]:
                    raise ValueError('PDF 缺少结束标记')
                kind = 'pdf'
            elif urlsplit(url).path.lower().endswith('.pdf'):
                raise ValueError('PDF 链接未返回 PDF 原件')
            elif 'html' in next((v.lower() for k,v in headers.items() if k.lower()=='content-type'), ''):
                kind = 'html'
            else:
                raise ValueError('当前仅支持 HTML 和 PDF')
            unchanged = store.save(url, body, kind, final, headers)
            if kind == 'html' and task['depth'] < config['max_depth']:
                parser = Links()
                text = body.decode(encoding, errors='replace')
                parser.feed(text)
                links = parser.links + (szse_links(text) if config['adapter']=='szse' else [])
                for href, title in links:
                    try:
                        target = check_url(urljoin(final, href), config)
                    except ValueError:
                        continue
                    if re.search(config['link_pattern'], target):
                        if not store.enqueue(target, url, title, task['depth']+1, config['max_tasks']):
                            counts['skipped_task_limit'] += 1
            db.execute("UPDATE tasks SET status='done',error=NULL,due=0 WHERE url=?", (url,))
            db.commit()
            counts['unchanged' if unchanged else 'downloaded'] += 1
        except Exception as exc:
            db.rollback()
            retryable = not isinstance(exc, ValueError)
            if isinstance(exc, HTTPError):
                retryable = exc.code in (408,429,500,502,503,504)
            delay = min(300, 2 ** min(attempt, 8))
            if isinstance(exc, HTTPError) and exc.headers.get('Retry-After'):
                value = exc.headers['Retry-After']
                try:
                    delay = max(delay, float(value))
                except ValueError:
                    try:
                        delay = max(delay, parsedate_to_datetime(value).timestamp()-time.time())
                    except (ValueError, TypeError, OverflowError):
                        pass
            status = 'retry' if retryable and attempt < config['max_attempts'] else 'quarantined'
            db.execute('UPDATE tasks SET status=?,due=?,error=? WHERE url=?',
                       (status,time.time()+delay,str(exc),url))
            db.commit()
            counts['failed'] += 1
    counts['states'] = dict(db.execute('SELECT status,count(*) FROM tasks GROUP BY status').fetchall())
    write_json(store.root / 'report.json', counts)
    return counts

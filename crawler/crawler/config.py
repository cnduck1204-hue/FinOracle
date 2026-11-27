import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def normalize(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('仅接受无凭据的 HTTP(S) URL')
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', p.query, ''))


def load(path):
    c = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    for key in ('name', 'allowed_hosts', 'seeds'):
        if not c.get(key):
            raise ValueError(f'配置缺少 {key}')
    defaults = dict(interval=1.0, timeout=20, max_bytes=20*1024*1024,
                    max_attempts=3, max_depth=3, max_tasks=200,
                    link_pattern=r'\.pdf(?:\?|$)', user_agent='FinOracleCrawler/0.1',
                    respect_robots=True, adapter='generic')
    c = defaults | c
    if c['adapter'] not in ('generic', 'szse'):
        raise ValueError('未知 adapter')
    for key in ('timeout', 'max_bytes', 'max_attempts', 'max_tasks'):
        if c[key] <= 0:
            raise ValueError(f'{key} 必须大于零')
    if c['interval'] < 0 or c['max_depth'] < 0:
        raise ValueError('间隔及深度不能为负数')
    re.compile(c['link_pattern'])
    c['allowed_hosts'] = [h.lower() for h in c['allowed_hosts']]
    for seed in c['seeds']:
        check_url(seed, c)
    return c


def check_url(url, config):
    url = normalize(url)
    if urlsplit(url).hostname not in config['allowed_hosts']:
        raise ValueError('URL 主机不在允许列表: ' + url)
    return url

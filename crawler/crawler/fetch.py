import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.robotparser import RobotFileParser
from .config import check_url


class Fetcher:
    def __init__(self, config):
        self.config = config
        self.last = 0
        self.robots = {}
        owner = self

        class Redirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                owner.allowed(newurl, robots=getattr(req, '_check_robots', True))
                owner.throttle()
                redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
                if redirected is not None:
                    redirected._check_robots = getattr(req, '_check_robots', True)
                return redirected

        self.opener = build_opener(Redirect())

    def throttle(self):
        time.sleep(max(0, self.config['interval'] - (time.monotonic() - self.last)))
        self.last = time.monotonic()

    def allowed(self, url, robots=True):
        url = check_url(url, self.config)
        if robots and self.config['respect_robots']:
            p = urlsplit(url)
            origin = f'{p.scheme}://{p.netloc}'
            if origin not in self.robots:
                # 占位防止 robots.txt 重定向递归；错误时移除，下一次重新检查。
                rp = RobotFileParser()
                rp.disallow_all = True
                self.robots[origin] = rp
                try:
                    body, _, _, _ = self.get(origin + '/robots.txt', robots=False)
                    rp.disallow_all = False
                    rp.parse(body.decode('utf-8', errors='replace').splitlines())
                except HTTPError as e:
                    if e.code == 404:
                        rp.disallow_all = False
                        rp.allow_all = True
                    else:
                        self.robots.pop(origin, None)
                        raise
                except Exception:
                    self.robots.pop(origin, None)
                    raise
            if not self.robots[origin].can_fetch(self.config['user_agent'], url):
                raise ValueError('robots.txt 禁止访问: ' + url)
        return url

    def get(self, url, robots=True):
        url = self.allowed(url, robots)
        self.throttle()
        req = Request(url, headers={'User-Agent': self.config['user_agent'],
                                    'Accept-Encoding': 'identity'})
        req._check_robots = robots
        with self.opener.open(req, timeout=self.config['timeout']) as response:
            if response.status != 200:
                raise ValueError(f'不支持的响应状态 {response.status}')
            limit = self.config['max_bytes']
            length = response.headers.get('Content-Length')
            if length and int(length) > limit:
                raise ValueError('文件超过大小限制')
            data = response.read(limit + 1)
            if len(data) > limit or (length and len(data) != int(length)):
                raise ValueError('文件超限或下载不完整')
            return data, response.geturl(), dict(response.headers), response.headers.get_content_charset() or 'utf-8'

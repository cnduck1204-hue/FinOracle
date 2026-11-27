"""提取已下载网页中的站点链接；不执行网页脚本。"""
import html
import re


def szse_links(text):
    links = []
    for script in re.findall(r'<script\b[^>]*>(.*?)</script>', text, re.S | re.I):
        # 只移除整行注释，避免破坏字符串中的 https://。
        script = re.sub(r'^\s*//.*$', '', script, flags=re.M)
        href = re.search(r"\bvar\s+curHref\s*=\s*(['\"])(.*?)\1", script, re.S)
        title = re.search(r"\bvar\s+curTitle\s*=\s*(['\"])(.*?)\1", script, re.S)
        if href:
            links.append((html.unescape(href[2]), html.unescape(title[2]) if title else ''))
    return links

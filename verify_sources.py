"""离线复核首批两个制度与已保存的官方来源文本；不重新联网下载。"""
from html.parser import HTMLParser
import logging
import re
from collection_pipeline import BASE, extract_pages, split_articles, normalize_chars, sha256, write_json


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if tag in ('p', 'br', 'div', 'h1', 'h2'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip -= 1
        if tag in ('p', 'div'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def verify(root=BASE):
    securities = root/'database/P020240802341036118987.pdf'
    downloaded = root/'sources/securities_risk_2023.pdf'
    payment = root/'database/非银行支付机构监督管理条例(国务院令 第768号).pdf'
    webpage = root/'sources/payment_gov.html'
    a, _ = extract_pages(securities)
    b, _ = extract_pages(downloaded)
    pages, _ = extract_pages(payment)
    pdf, _, pi = split_articles(pages, 'payment', {'end_markers': ['打印本页 关闭窗口']})
    parser = VisibleText()
    parser.feed(webpage.read_text(encoding='utf-8'))
    text = normalize_chars(''.join(parser.parts))
    html, _, hi = split_articles([text], 'reference', {'end_markers': ['点击打开']})
    clean = lambda s: re.sub(r'\s+', '', s)
    diffs = [{'article': x['article'], 'pdf': x['content'], 'reference': y['content']}
             for x, y in zip(pdf, html)
             if x['article'] != y['article'] or clean(x['content']) != clean(y['content'])]
    result = {
        'result': 'passed' if a == b and len(a) == 12 and len(pdf) == len(html) == 60
                  and not diffs and not pi and not hi else 'failed',
        'securities_page_count': len(a), 'securities_all_pages_equal': a == b,
        'payment_pdf_count': len(pdf), 'payment_reference_count': len(html),
        'payment_differences': diffs, 'parser_issues': pi + hi,
        'method': 'NFKC and explicit character mapping; comparison ignores whitespace only',
        'source_files': [{'path': str(p.relative_to(root)), 'sha256': sha256(p)}
                         for p in [securities, downloaded, payment, webpage]],
    }
    write_json(root/'sources/text_comparison.json', result)
    return result


if __name__ == '__main__':
    logging.getLogger('pdfminer').setLevel(logging.ERROR)
    result = verify()
    print(result['result'])
    raise SystemExit(0 if result['result'] == 'passed' else 1)

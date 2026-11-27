"""来源完整性与发布审批是两个独立维度。"""
import re


def classify(pages):
    evidence = []
    for n, page in enumerate(pages, 1):
        if re.search(r'本文\s*有\s*删减', page):
            evidence.append({'page':n, 'text':'本文有删减', 'type':'source_declaration'})
    return {'source_completeness':'abridged' if evidence else 'unknown',
            'completeness_evidence':evidence,
            'completeness_notice':'公开删减版，不能据此断言原文件未规定其他事项' if evidence else '完整性尚未经独立核定',
            'approved_for_integration':False}

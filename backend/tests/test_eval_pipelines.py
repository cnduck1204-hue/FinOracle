import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_pipelines.py"
spec = importlib.util.spec_from_file_location("eval_pipelines", SCRIPT)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_articles_span_pages_and_keep_their_label():
    pages = [
        "示例管理办法\n第一条 为了规范金融机构的经营行为，保护投资者合法权益，制定本办法，各机构应当遵守。\n",
        "第二条 机构应当建立内部控制制度，并定期开展内部检查\n工作，形成书面报告。\n",
        "第二条的后续内容没有换行标记，继续说明报告的报送要求和时间。\n第三条 短\n",
    ]
    passages = evaluation.extract_passages("doc", pages)
    assert [(p.label, p.pages) for p in passages] == [
        ("第一条", (1,)),
        ("第二条", (2, 3)),
    ]
    assert passages[0].title == "示例管理办法"


def test_notices_are_split_by_numbered_items():
    item = "金融机构应当按照监管要求持续完善内部控制体系并定期向监管部门报送相关材料和说明。"
    pages = [f"关于加强管理的通知\n一、{item}\n二、{item}\n（三）{item}\n"]
    passages = evaluation.extract_passages("doc", pages)
    assert [p.label for p in passages] == ["一、", "二、", "（三）"]
    assert evaluation.is_article("第十条") and not evaluation.is_article("一、")


def test_documents_without_articles_use_long_paragraphs():
    long_line = (
        "金融机构应当按照监管要求持续完善内部控制体系并定期向监管部门报送相关材料和说明。"
        * 2
    )
    pages = [f"标题\n返回主站首页系统简介综合查询{long_line}\n{long_line}\n短句\n"]
    passages = evaluation.extract_passages("doc", pages)
    assert len(passages) == 1 and passages[0].label == ""


def test_sampling_is_balanced_and_reproducible():
    make = lambda doc, n: [  # noqa: E731
        evaluation.Passage(doc, "t", "第一条", f"{doc}{i}", (1,)) for i in range(n)
    ]
    by_doc = {"a": make("a", 20), "b": make("b", 2)}
    first = evaluation.sample_passages(by_doc, 6)
    assert first == evaluation.sample_passages(by_doc, 6)
    assert sum(p.doc == "b" for p in first) == 2


def test_copies_source_detects_long_verbatim_runs():
    source = "金融机构应当建立健全内部控制制度"
    assert evaluation.copies_source("如何建立健全内部控制？", source)
    assert not evaluation.copies_source("内控体系该怎么搭建", source)


def test_metrics_score_by_page():
    assert evaluation.recall_at([3, 1, 2], {1, 9}, 2) == 0.5
    assert evaluation.mrr_at([3, 1], {1}, 10) == 0.5
    assert evaluation.mrr_at([3, 4], {1}, 10) == 0.0
    assert evaluation.ndcg_at([1, 2], {1, 2}, 10) == pytest.approx(1.0)
    assert evaluation.ndcg_at([], {1}, 10) == 0.0


def test_ranked_pages_expand_ranges_and_penalize_other_documents():
    hit = lambda version, start, end: {  # noqa: E731
        "version": {"id": version},
        "page_start": start,
        "page_end": end,
    }
    results = [hit("other", 1, 1), hit("v", 2, 3), hit("v", 3, 3), hit("v", None, None)]
    assert evaluation.ranked_pages(results, "v") == [0, 2, 3, 0]


def test_bootstrap_interval_contains_the_true_difference():
    low, high = evaluation.bootstrap_diff([1.0] * 50, [0.0] * 50, rounds=200)
    assert (low, high) == (1.0, 1.0)
    low, high = evaluation.bootstrap_diff([1.0, 0.0] * 30, [0.0, 1.0] * 30, rounds=200)
    assert low < 0 < high


def test_summary_reports_latency_percentiles():
    rows = [
        {"hit1": 1.0, "recall5": 1.0, "mrr10": 1.0, "ndcg10": 1.0, "ms": float(ms)}
        for ms in range(1, 21)
    ]
    summary = evaluation.summarize(rows)
    assert summary["n"] == 20 and summary["p50_ms"] == 11 and summary["p95_ms"] == 20
    assert evaluation.summarize([]) == {}

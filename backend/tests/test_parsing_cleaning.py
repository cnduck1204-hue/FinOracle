from app.rag.parsing.cleaning import merge_blocks, normalize_text
from app.rag.parsing.models import TextBlock


def block(text, page=1, order=0, source="text"):
    return TextBlock(page=page, order=order, kind="paragraph", text=text, source=source)


def test_normalize_maps_compat_characters_and_keeps_chinese_punctuation():
    raw = "第 七 条　⾦融  机构 ＡＢＣ１２３，应当：\n\n 按 期 报 送   report  data "
    assert normalize_text(raw) == (
        "第七条 金融机构 ABC123，应当：\n按期报送 report data"
    )
    assert normalize_text("第一章　总  则") == "第一章 总则"
    assert normalize_text("第十条之一 内 容") == "第十条之一 内容"


def test_merge_removes_page_numbers_and_repeated_headers():
    pages = [
        block(f"金融制度汇编\n第{n}条 内容{n}\n- {n} -", page=n, order=n)
        for n in range(1, 4)
    ]
    merged, removed = merge_blocks(pages, [])
    assert [b.text for b in merged] == ["第1条 内容1", "第2条 内容2", "第3条 内容3"]
    assert removed == 6

    # 少于 3 页时不推断页眉，避免误删正文首行
    short, _ = merge_blocks(pages[:2], [])
    assert short[0].text == "金融制度汇编\n第1条 内容1"

    # 每页仅剩的一行即使只差数字也视为正文保留
    sole = [block(f"附表{n}", page=n, order=n) for n in range(1, 4)]
    assert [b.text for b in merge_blocks(sole, [])[0]] == ["附表1", "附表2", "附表3"]


def test_merge_strips_two_layer_footers_and_drops_emptied_blocks():
    bodies = ["第一条 规范经营。", "第二条 适用范围。", "第三条 依法履职。"]
    pages = []
    for n, body in enumerate(bodies, start=1):
        pages.append(block(body, page=n, order=2 * n))
        pages.append(block(f"内部资料\n第 {n} 页 共 3 页", page=n, order=2 * n + 1))
    merged, removed = merge_blocks(pages, [])
    assert [b.text for b in merged] == bodies
    assert removed == 6


def test_merge_inserts_ocr_in_order_and_drops_duplicates():
    text = [
        block("第一条 规范经营。", order=0),
        block("第二条 适用范围。", order=2),
    ]
    ocr = [
        block("图表：资本 充足率", order=1, source="ocr"),
        block("第二条 适用 范围。", order=3, source="ocr"),
        block("   ", order=4, source="ocr"),
    ]
    merged, removed = merge_blocks(text, ocr)
    assert [(b.text, b.source) for b in merged] == [
        ("第一条 规范经营。", "text"),
        ("图表：资本充足率", "ocr"),
        ("第二条 适用范围。", "text"),
    ]
    assert removed == 1


def test_merge_drops_adjacent_duplicate_blocks_without_pages():
    blocks = [block("同一段", page=None, order=n) for n in range(3)]
    merged, removed = merge_blocks(blocks, [])
    assert [b.text for b in merged] == ["同一段"]
    assert removed == 2

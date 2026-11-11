import pytest

from app.rag.parsing.chunkers import (
    STRUCTURED_CHUNKERS,
    ArticleChunker,
    ItemChunker,
    LengthChunker,
    StructuredChunker,
    create_chunker,
)
from app.rag.parsing.models import TextBlock
from app.rag.parsing.options import ParseOptions
from app.rag.parsing.text_utils import chinese_number

OPTIONS = ParseOptions()


def block(text, page=1, order=0, kind="paragraph", source="text", level=None):
    return TextBlock(
        page=page, order=order, kind=kind, text=text, source=source, level=level
    )


def test_chinese_number_parses_common_forms():
    cases = {"七": 7, "十": 10, "十二": 12, "二十": 20, "一百零三": 103, "两千": 2000}
    for text, value in cases.items():
        assert chinese_number(text) == value
    assert chinese_number("15") == 15


def test_article_chunker_tracks_chapters_sections_and_references():
    blocks = [
        block("某某管理办法\n第一章 总则", page=1, order=0),
        block(
            "第一条 为了规范经营，制定本办法。\n第二条 本办法适用于：\n"
            "（一）银行；\n第七条规定的机构除外。",
            page=1,
            order=1,
        ),
        block(
            "第二章 监督管理\n第一节 一般规定\n第三条 监管机构依法履职。",
            page=2,
            order=2,
        ),
        block("第三条之一 补充规定。\n第二章所列情形另行规定。", page=3, order=3),
        block("第二条 不可能回退的条号。", page=3, order=4),
    ]

    chunks = ArticleChunker().split(blocks)

    assert [(c.chapter, c.section, c.article) for c in chunks] == [
        ("", "", ""),
        ("第一章 总则", "", "第一条"),
        ("第一章 总则", "", "第二条"),
        ("第二章 监督管理", "第一节 一般规定", "第三条"),
        ("第二章 监督管理", "第一节 一般规定", "第三条之一"),
    ]
    assert chunks[0].content == "某某管理办法"
    assert (
        chunks[2].content
        == "第二条 本办法适用于：\n（一）银行；\n第七条规定的机构除外。"
    )
    assert chunks[4].content.endswith(
        "第二章所列情形另行规定。\n第二条 不可能回退的条号。"
    )
    assert (chunks[4].page_start, chunks[4].page_end) == (3, 3)
    assert [c.ordinal for c in chunks] == [1, 2, 3, 4, 5]


def test_article_chunker_restarts_numbering_when_chapters_restart():
    blocks = [
        block("第一章 总则\n第一条 主文。\n第二条 主文。"),
        block("第一章 附件总则\n第一条 附件内容。", page=2, order=1),
    ]
    chunks = ArticleChunker().split(blocks)
    assert [(c.chapter, c.article) for c in chunks] == [
        ("第一章 总则", "第一条"),
        ("第一章 总则", "第二条"),
        ("第一章 附件总则", "第一条"),
    ]


def test_long_articles_are_split_by_sentence_and_keep_pages():
    sentence = "金融机构应当按照规定报送数据。"
    blocks = [
        block("第一条 " + sentence * 6, page=1, order=0),
        block(sentence * 3, page=2, order=1),
    ]
    chunks = ArticleChunker(max_chars=40).split(blocks)

    assert {c.article for c in chunks} == {"第一条"}
    assert [c.part for c in chunks] == list(range(1, len(chunks) + 1))
    assert all(c.part_count == len(chunks) for c in chunks)
    assert all(len(c.content) <= 40 for c in chunks)
    assert "".join(c.content.replace("\n", "") for c in chunks) == (
        "第一条 " + sentence * 9
    )
    assert (chunks[0].page_start, chunks[-1].page_end) == (1, 2)


def test_long_sentence_without_punctuation_is_hard_cut():
    chunks = ArticleChunker(max_chars=10).split([block("第一条 " + "甲" * 25)])
    assert [len(c.content) for c in chunks] == [10, 10, 9]


def test_item_chunker_splits_notices_and_keeps_sub_items():
    notice = [
        block("关于加强管理的通知\n各分行：\n现通知如下：", page=1),
        block(
            "一、金融机构应评估风险。\n（一）建立制度；\n（二）定期报告。\n"
            "二、反洗钱控制要覆盖\n全部产品。",
            page=1,
            order=1,
        ),
        block("一、编号回退不是新项。\n三、加强审计。\n特此通知。", page=2, order=2),
    ]

    chunks = ItemChunker().split(notice)

    assert ItemChunker().count_labels(notice) == 3
    assert [c.article for c in chunks] == ["", "一、", "二、", "三、"]
    assert (
        chunks[1].content
        == "一、金融机构应评估风险。\n（一）建立制度；\n（二）定期报告。"
    )
    assert chunks[2].content.endswith("全部产品。\n一、编号回退不是新项。")
    assert (chunks[2].page_start, chunks[2].page_end) == (1, 2)
    assert chunks[3].content == "三、加强审计。\n特此通知。"


def test_length_chunker_uses_heading_path_and_overlap():
    blocks = [
        block("总则", page=None, kind="heading", level=1),
        block("细则", page=None, order=1, kind="heading", level=2),
        block("第一段内容。\n第二段内容。\n第三段内容。", page=None, order=2),
        block("附录", page=None, order=3, kind="heading", level=1),
        block("附录内容。", page=None, order=4, source="ocr"),
    ]
    chunks = LengthChunker(max_chars=14, overlap=6).split(blocks)

    assert [(c.heading, c.content) for c in chunks] == [
        ("总则 / 细则", "第一段内容。\n第二段内容。"),
        ("总则 / 细则", "第二段内容。\n第三段内容。"),
        ("附录", "附录内容。"),
    ]
    assert chunks[-1].sources == ["ocr"]
    assert chunks[0].page_start is None


def test_create_chunker_detects_document_style():
    articles = [block("第一条 甲。\n第二条 乙。")]
    notice = [block("一、甲。\n二、乙。")]
    prose = [block("普通说明文字。\n第一条规定的内容。")]
    # 条文体例优先于一级序号
    mixed = [block("第一条 总则：\n一、甲；\n二、乙。\n第二条 附则。")]

    assert create_chunker("auto", articles, OPTIONS).name == "article"
    assert create_chunker("auto", notice, OPTIONS).name == "item"
    assert create_chunker("auto", prose, OPTIONS).name == "length"
    assert create_chunker("auto", mixed, OPTIONS).name == "article"
    assert create_chunker("article", prose, OPTIONS).name == "article"
    assert create_chunker("length", articles, OPTIONS).name == "length"
    with pytest.raises(ValueError):
        create_chunker("semantic", prose, OPTIONS)


def test_create_chunker_applies_options():
    options = ParseOptions(max_chunk_chars=120, chunk_overlap=30)
    article = create_chunker("article", [], options)
    length = create_chunker("length", [], options)
    assert article.max_chars == 120
    assert (length.max_chars, length.overlap) == (120, 30)


def test_registered_structured_chunkers_match_their_names():
    for name, chunker in STRUCTURED_CHUNKERS.items():
        assert issubclass(chunker, StructuredChunker)
        assert chunker.name == name

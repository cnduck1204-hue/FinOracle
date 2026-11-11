"""列表搜索的共用规则。"""

# 配合 like(..., escape=LIKE_ESCAPE) 使用
LIKE_ESCAPE = "\\"


def contains_pattern(text: str) -> str:
    """把搜索词转为按字面匹配的 LIKE 模式：%、_ 和反斜杠不作通配符。"""
    escaped = (
        text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", LIKE_ESCAPE + "%")
        .replace("_", LIKE_ESCAPE + "_")
    )
    return f"%{escaped}%"

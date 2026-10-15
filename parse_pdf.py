"""parse_pdf.py — 批量处理 PDF 解析 + 切片

用法：
    1. 改下面的 pdf_folder
    2. 把 PDF 都扔进这个文件夹
    3. 运行：python parse_pdf.py
"""
import json
import os
import re
import unicodedata
import pdfplumber
from pathlib import Path

# === 字符映射：CJK 部首补充区（NFKC 不处理） ===
# 例：⺠ → 民，⻔ → 门
char_map = {
    "⺠": "民",
    "⻔": "门",
    "⻓": "长",
    # 后续如有新发现，继续加
}


def normalize_chars(text: str) -> str:
    """规范化字符：先 NFKC，再手动映射"""
    text = unicodedata.normalize('NFKC', text)
    for old, new in char_map.items():
        text = text.replace(old, new)
    return text

# === 你只要改这一行：你的 PDF 文件夹 ===
pdf_folder = r"E:\RAG-basedFinancialKnowledgeQASystemDesign\database"
# ====================================

# === 噪声关键词清单 ===
noise_words = [
    # === 通用防御规则（任何站都可能有） ===
    # 浏览器打印时加的日期戳（多年度，避免"今年能过滤去年不能"）
    "2023/", "2024/", "2025/", "2026/", "2027/",
    # 页码（"1/7"这种浏览器自动加的）
    # 这类规则放后面用正则处理（见下面）
    # 通用翻页导航
    "上一页", "下一页", "尾页", "末页", "首页", "返回首页",
    # 通用订阅/分享/操作
    "RSS订阅", "RSS", "邮件订阅", "分享到", "转发", "收藏",
    "加入收藏", "设为主页", "打印此页",
    # 联系信息
    "E-mail", "Email:", "电话：", "联系电话", "工作时间",
    # 主办/版权
    "主办单位", "承办单位", "协办单位", "技术支持", "网站地图", "联系方式",
    # 浏览器工具栏（打印 PDF 必带）
    "本站", "打印 关闭",
    # 站内搜索
    "您要搜索", "输入您要", "请输入", "高级搜索",
    # 字体选择器
    "【字体", "字体：大", "字体:大", "字号", "字体大小",
    # 顶部工具栏（无障碍/语言/分享）
    "无障碍浏览", " 繁 EN", "繁EN",
    # 顶部导航菜单（机构通用栏目）
    "机构概况", "新闻资讯", "政务信息", "在线服务", "互动交流", "统计数据", "专题专栏",
    # 文章元信息（发布时间/来源/类型/分享按钮）
    "发布时间：", "来源：", "文章类型：", " 微博 微信 ", "打印 微博",
    # 底部（QR码/版权/分享）
    "手机扫一扫", "版权与免责声明", "文章来源", "官微",

    # === 央行网站 pbc.gov.cn 专属 ===
    "新闻发布", "法律法规", "货币政策", "http", "www.", "pbc.gov.cn",
    "术语表", "信息公开", "金融科技",
    "政务公开", "服务互动", "网送文告", "我的位置", "法律声明",
    "网站标识码", "网站主办单位", "打印本页",

    # === CSRC 法规数据库 专属 ===
    "证券期货法规数据库系统", "Securities and futures law database",
    "最新法律资讯", "法规体系查询", "首页 >",
    "法规详情", "效力状态", "失效日期", "发文单位", "发文日期",
    "实施日期", "历次修订信息", "全部条文", "起草说明",
    "条款查询", "关键字查询", "附件下载", "关联文件",
    "正文下载", "相关案例", "显示注释",

    # === CSRC 通用页脚 ===
    "版权所有", "网站识别码", "京ICP备", "京公网安备",
]


def parse_and_chunk(pdf_path):
    """处理单个 PDF：抽文字、过滤、切片、生成 .txt 和 .json。返回切片数。"""

    # 第一步：抽文字 + 过滤
    pdf = pdfplumber.open(pdf_path)
    all_text = ""
    for page in pdf.pages:
        text = page.extract_text()
        if text is None:
            continue
        # 规范化字符：把康熙部首等异体字还原成标准字
        # 例：第⼀条 → 第一条，银⾏ → 银行，⺠ → 民
        text = normalize_chars(text)
        for line in text.split("\n"):
            if line.strip() == "":
                continue
            is_noise = False
            for w in noise_words:
                if w in line:
                    is_noise = True
                    break
            if "文号" in line and "日期" in line:
                is_noise = True
            # 正则：纯页码行（"1/7"、"10 / 100"）
            if re.match(r"^\d+\s*/\s*\d+\s*$", line):
                is_noise = True
            if is_noise:
                continue
            all_text = all_text + line + "\n"
    pdf.close()

    # 第二步：写 .txt
    txt_path = pdf_path.replace(".pdf", ".txt")
    file = open(txt_path, "w", encoding="utf-8")
    file.write(all_text)
    file.close()

    # 第三步：按"条"切片
    chunks = []
    current_chapter = ""
    current_article = ""
    current_content = ""
    chapter_when_started = ""

    for line in all_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("第") and "章" in line and "条" not in line:
            current_chapter = line
            continue
        if line.startswith("第") and "条" in line:
            if current_article:
                chunks.append({
                    "chapter": chapter_when_started,
                    "article": current_article,
                    "content": current_content.strip()
                })
            parts = line.split(maxsplit=1)
            current_article = parts[0]
            chapter_when_started = current_chapter
            current_content = parts[1] if len(parts) > 1 else ""
            continue
        if current_article:
            current_content = current_content + "\n" + line

    if current_article:
        chunks.append({
            "chapter": chapter_when_started,
            "article": current_article,
            "content": current_content.strip()
        })

    # 第四步：写 .json
    doc_id = os.path.splitext(os.path.basename(pdf_path))[0]
    output_chunks = []
    for i, c in enumerate(chunks, 1):
        output_chunks.append({
            "doc_id": doc_id,
            "chunk_id": doc_id + "_" + str(i).zfill(3),
            "chapter": c["chapter"],
            "article": c["article"],
            "content": c["content"]
        })

    json_path = pdf_path.replace(".pdf", ".json")
    file = open(json_path, "w", encoding="utf-8")
    json.dump(output_chunks, file, ensure_ascii=False, indent=2)
    file.close()

    return len(output_chunks)


# ========== 主程序：批量处理 ==========
pdf_files = sorted(Path(pdf_folder).glob("*.pdf"))
print(f"找到 {len(pdf_files)} 个 PDF 文件")

for i, pdf_path in enumerate(pdf_files, 1):
    pdf_path = str(pdf_path)
    print(f"\n[{i}/{len(pdf_files)}] {os.path.basename(pdf_path)}")

    # 已经处理过就跳过（看 .json 在不在）
    json_path = pdf_path.replace(".pdf", ".json")
    if os.path.exists(json_path):
        print("  跳过（已处理）")
        continue

    try:
        n_chunks = parse_and_chunk(pdf_path)
        print(f"  完成，{n_chunks} 个切片")
    except Exception as e:
        print(f"  失败: {e}")

print(f"\n=== 全部完成 ===")

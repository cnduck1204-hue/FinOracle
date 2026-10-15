# RAG-based Financial Knowledge QASystem Design — 数据采集模块

## 这是什么

本目录是 RAG-based 金融制度问答系统的**数据采集模块**。负责把 PDF 格式的金融法规/规章/制度文件，解析成结构化 JSON 数据，供 RAG 团队做检索用。

**当前状态**：已完成基础流水线（解析 → 过滤 → 切片 → JSON），支持批量处理。

---

## 目录结构

```
RAG-basedFinancialKnowledgeQASystemDesign/
├── database/                       # 原始 PDF + 解析产物
│   ├── *.pdf                       # 原始 PDF（手动下载/打印保存）
│   ├── *.txt                       # 解析后的纯文本（人读）
│   └── *.json                      # 按"条"切分的结构化数据（RAG 读）
├── parse_pdf.py                    # 解析脚本（核心工具）
├── samples/                        # 测试样本（备用）
└── README.md                       # 本文件
```

---

## 怎么用

### 1. 装环境（一次性）

```powershell
D:\pyenvs\pytorch-env\Scripts\python.exe -m pip install pdfplumber -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> Python 环境位置：`D:\pyenvs\pytorch-env\`
> 库：pdfplumber（处理 PDF）、json（标准库）、os（标准库）

### 2. 准备 PDF

把要处理的 PDF 放到 `database/` 目录下。

**下载建议**：
- 人民银行：`http://www.pbc.gov.cn` → 政策法规
- 国家金融监督管理总局：`http://www.nfra.gov.cn` → 政策法规
- 证监会：`http://www.csrc.gov.cn` → 政策法规
- 上海期货交易所：`http://www.shfe.com.cn` → 业务规则
- **通用招**：浏览器打开规章页面 → `Ctrl+P` → 打印机选"另存为 PDF" → 保存到 `database/`

### 3. 跑脚本

```powershell
D:\pyenvs\pytorch-env\Scripts\python.exe E:\RAG-basedFinancialKnowledgeQASystemDesign\parse_pdf.py
```

**脚本行为**：
- 自动扫描 `database/` 下所有 `.pdf` 文件
- 自动跳过**已处理**的（已有同名 `.json`）
- 每个 PDF 产出对应的 `.txt` + `.json`

---

## 输出格式

### `.txt`（人读）

完整正文，已过滤掉网页导航/页脚等噪声。

### `.json`（RAG 读）

**按"条"切分**，每条法规一个 chunk：

```json
[
  {
    "doc_id": "非银行支付机构监督管理条例(国务院令 第768号)",
    "chunk_id": "非银行支付机构监督管理条例(国务院令 第768号)_001",
    "chapter": "第一章 总则",
    "article": "第一条",
    "content": "为了规范非银行支付机构行为..."
  },
  {
    "doc_id": "非银行支付机构监督管理条例(国务院令 第768号)",
    "chunk_id": "非银行支付机构监督管理条例(国务院令 第768号)_002",
    "chapter": "第一章 总则",
    "article": "第二条",
    "content": "本条例所称非银行支付机构..."
  }
]
```

**RAG 同学**可以直接用 `chapter + article` 做精确引用（如"依据《XX》第六条"）。

---

## 已支持的来源

| 来源 | URL | 状态 |
|---|---|---|
| 中国人民银行 | pbc.gov.cn | ✅ 已测试 |
| CSRC 法规数据库 | csrc.gov.cn | ✅ 已测试 |
| 上海期货交易所 | shfe.com.cn | ✅ 已测试 |
| 中国证券业协会 | sac.net.cn | ✅ 已测试 |
| 国务院（via PBOC 转发） | 国务院令 PDF | ✅ 已测试 |
| 国家金融监督管理总局 | nfra.gov.cn | ✅ 已测试 |

> 新来源可能需要往 `noise_words` 加新关键词（见下文）。

---

## 怎么扩展

### 加新来源的噪声规则

打开 `parse_pdf.py`，找到 `noise_words = [...]` 列表，**加一行**：

```python
noise_words = [
    # 已有的...
    "新网站导航词",   # ← 加这行
]
```

怎么找该加什么词？跑完一次后打开 .txt，看哪些行不像正文，就是噪声。

### 重新处理某个文件

直接删它的 `.json`（和 `.txt` 如果想重抽文本），再跑脚本。脚本会自动重处理。

### 加新的处理逻辑

`parse_pdf.py` 是单文件，结构清晰：

| 段 | 干什么 | 改这里 |
|---|---|---|
| `noise_words` 列表 | 噪声关键词 | 加新规则 |
| `parse_and_chunk()` 函数 | 核心处理逻辑 | 加新功能 |
| 末尾的主循环 | 批量调度 | 调整处理策略 |

---

## 已知限制

- **扫描型 PDF（图片）** 需要先 OCR，本脚本不直接处理
- **复杂表格** 可能解析错位，需要单独校对
- **章/条识别** 依赖"第X章"和"第X条"格式，特殊编排的文档要改脚本
- **来源适配**：换源时大概率要在 `noise_words` 加规则

---

## 维护建议

- 每次跑完抽检 1-2 个 .json，看章节和条文是否对得上
- 新的来源先用 1 份文件测试，加好 `noise_words` 再批量
- 反馈给 RAG 同学时附上 doc_id（前缀），方便他们定位

---

## 团队协作

- 本模块作者：数据采集同学
- 主要用户：RAG 检索同学
- 协作约定：JSON 格式有改动前先沟通

---

## 维护日志

| 日期 | 改动 | 备注 |
|---|---|---|
| 2026-10-14 | 初版：解析 + 过滤 + 切片 + 批量 | 央行/CSRC/上期所 三源测试通过 |
| 2026-10-15 | 加 SAC 源（反垄断法）+ 国务院（证券公司风险处置条例）| 4 PDF 批量通过；新发现"您要搜索的内容"、"【字体..."噪声模式 |
| 2026-10-15 | 补底部噪声 | 加 `"本站"`、`"打印 关闭"`；4 PDF 全干净 |
| 2026-10-15 | 加 NFRA 源（商业银行法）+ 正则页码 | 5 PDF 全干净；新发现"无障碍浏览"、"发布时间："、"手机扫一扫"等 NFRA 特有噪声 |
| 2026-10-15 | 字符规范化 | 加 `unicodedata.normalize('NFKC', text)` + 手动映射表（⺠/⻔/⻓ → 民/门/长）；消除 PDF 异体字问题 |

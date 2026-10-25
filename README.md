# 金融制度数据采集模块

本模块交付原始文件、来源清单、按条切分的 JSON、质量记录和联调样例。首批已有 2 份经过全文文本对照的制度，共 123 个条文切片；另提供 1 份真实扫描 PDF 供 OCR 验证。其他问题原件保留并隔离。

## 先看哪些文件

1. documents.json：全部 7 份登记原件的标题、来源、版本、哈希及质量状态。
2. DATA_CONTRACT.md：字段定义及 RAG 接入约定。
3. processed/release_documents.json 与 processed/release_chunks.json：首批联调数据。
4. qa_examples.json：10 个问题、参考答案、条文引用与 PDF 页码。
5. QUALITY_REPORT.md：检查证据、已修复问题和剩余工作。
6. HANDOFF.md：你与 RAG 同学的交接步骤。

已有 database 下的 TXT/JSON 为旧产物，仅留作对照；RAG 不应继续读取这些旧 JSON。新产物放在 processed，原始 PDF 没有被覆盖。

## 本地运行

需要 Python 3.10 或更新版本。PowerShell 中进入本目录，先安装依赖：

```powershell
python -m pip install -r requirements.txt
python parse_pdf.py
python verify_sources.py
python validate_delivery.py
python -m unittest discover -s tests -v
```

如果使用已有 Python 环境，可以将 python 替换为：

```powershell
& 'D:\pyenvs\pytorch-env\Scripts\python.exe' parse_pdf.py
```

本次验证使用 Codex 附带的 Python 和 pdfplumber 0.11.10；尚未在上述 D 盘环境重复安装或运行。依赖文件固定了本次验证的 pdfplumber 版本。

默认文件位置以脚本目录为基准，不要求从固定目录启动。指定不同清单及输出位置：

```powershell
python parse_pdf.py --manifest documents.json --output-dir processed
```

每次重新解析，不再按 JSON 是否存在跳过。不会覆盖 database 中的旧产物。处理失败返回退出码 1；已明确登记的扫描件或 DOC 不属于执行异常，状态写入报告并从发布数据中排除。

运行中 quality_report.json 为 running；完成后为 completed。不要同时运行多个写入同一输出目录的进程。RAG 应在处理完成后读取发布文件。

## 目录

```text
based_knowledge/
  database/                  原有 5 份 PDF 与旧解析结果
  sources/                   新下载原件及全文比对依据
  documents.json             原件清单
  parse_pdf.py               命令行入口
  collection_pipeline.py     清洗、切片、质检和发布逻辑
  requirements.txt           Python 依赖
  schemas/                   JSON Schema
  tests/                     有针对性的回归测试
  processed/
    candidates/              候选 JSON、原始文本、正文、清理记录
    release_chunks.json      唯一推荐的首批入库条文
    release_documents.json   配套文档信息
    quality_report.json      本次处理结果
  qa_examples.json           联调问题和引用
  validate_delivery.py       原件及交付数据关联检查
  verify_sources.py          离线重做与已保存官方来源的全文比对
  DATA_CONTRACT.md            字段约定
  QUALITY_REPORT.md          本批质量说明
  HANDOFF.md                 交接步骤
```

## 新增文件

将原件保存到 database 或 sources，在 documents.json 增加记录；先保持 approved_for_integration=false。首次处理后检查候选、被排除行、原件页面、条号和来源。确认完整后再审批，并记录该次 reviewed_parser_version。不能只因非空或条数正确就判定合格。

扫描件必须经过 OCR 和逐页校对；原 PDF 已被裁切时应重新采集，不得让语言模型补写。当前正式切片策略是每条一块，后续长条再分块由 RAG 同学协商维护。

本批是指定版本的联调样本，不是完整、现行的金融法规库。validity_status=unknown 不能解释为现行有效；需要回答现行制度问题时，必须先核验版本与时效。

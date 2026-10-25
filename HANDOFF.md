# 数据采集第一批交接

这批文件让 RAG 同学立即开始验证导入、检索、问答及引用。你负责原件、来源、版本清单和数据质量记录；正式解析、OCR 及后续切片维护归属仍需团队明确。本文件仅供你交接使用，没有自动向队友发送消息。

## 按顺序交付

1. **先交格式和清单。** documents.json、DATA_CONTRACT.md、schemas/。说明本批有 7 份登记原件，但只有 2 份被批准用于条文联调。
2. **再交正文。** processed/release_chunks.json 和 processed/release_documents.json 必须成对提供；保留 database/ 中这两份文档对应的 PDF，页码才能回溯。不要把 database/ 下旧 JSON 当成本批正文。
3. **再交验证材料。** qa_examples.json、QUALITY_REPORT.md、processed/quality_report.json。让 RAG 先按 scope_doc_id 限定文档验证，再开展跨文档检索。
4. **再交扫描样本。** sources/aml_2012_178_scanned.pdf；来源与文件哈希已登记。它是待 OCR 输入，不是可直接入库文本。
5. **最后交可复现程序。** parse_pdf.py、collection_pipeline.py、requirements.txt、validate_delivery.py、tests/ 及 README.md。ZIP 已将运行需要的目录一起保留，解压后可按说明运行。

## 联调验收清单

- [ ] RAG 读取 2 个文档和 123 个条文，无重复 chunk_id。
- [ ] 答案引用可返回制度正式名称、版本、条号、来源和 PDF 页码。
- [ ] 10 个联调问题能找到对应条文；答案保留限定条件和例外。
- [ ] 原扫描件、裁切件和待转换 DOC 不出现在首批正文检索中。
- [ ] unknown 时效未被显示为“现行有效”。
- [ ] 页码跳转对准 file_path 对应原件，而不是同名的其他版本。
- [ ] 团队确认正式解析与 OCR 的维护人，以及新增元信息字段的读取方式。

这些项目需要真实 RAG 系统参与，因此当前保留未勾选。数据文件校验和解析回归测试已经完成，详见质量报告。

## 可直接转述给队友

本批提供两份有来源且经过全文对照的金融制度，共123条，并附PDF页码和10个问答联调样例。请读取processed中的release文件，通过doc_id关联文档信息；旧database JSON和candidates用于对照排错，不直接入库。另有一份真实扫描PDF用于OCR验证。请共同确认正式解析、OCR及子切片的维护归属，引用需要保留制度标题、版本、条号、来源和PDF页码。

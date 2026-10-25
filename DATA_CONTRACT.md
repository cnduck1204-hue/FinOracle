# 数据采集交接格式

本批数据用于验证指定版本的解析、检索、问答和引用，不把未知时效标记为现行有效。所有 JSON/TXT 均为 UTF-8。此格式是可运行的交接提案，尚未声称其他成员已确认。

## 文档清单

documents.json 顶层包含 schema_version、as_of_date、documents。documents 为数组，每个对象对应一份待处理原件。原有五份文件保持 doc_id 不变，新扫描件和重新下载的 DOC 使用独立 ID。日期格式为 YYYY-MM-DD；未知值用 null。

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| doc_id / title / issuer | string | 文档标识、正式名称、发文机构；网站转载单位不等于发文机构 |
| document_number | string 或 null | 文号；原公布文号与修订文号在文字中明确区分 |
| source_url / download_url | string 或 null | 发布依据页面或原件地址、下载地址；重新找回的来源在 evidence 说明 |
| published_date / effective_date | string 或 null | 当前登记版本的公布、生效日期；不使用转载页面日期替代 |
| validity_status | string | unknown 或 not_yet_effective；本批没有将未知版本自动标为现行有效 |
| version_note / validity_checked_at | string 或 null | 版本说明、效力核查日期 |
| collected_at / registered_at / verified_at | string 或 null | 原件取得日期、登记日期、来源核对日期；已有文件的采集日期不猜测 |
| file_path / file_type / sha256 | string | 相对于 documents.json 的原件路径、扩展名、SHA-256 |
| extraction_type | string | text、scanned 或 native_document |
| quality_status | string | approved_for_integration、recollect、needs_ocr、needs_conversion 等 |
| approved_for_integration | boolean | 是否已完成本批联调数据审批；与法律效力无关 |
| expected_articles | integer 或 null | 该版本应有条数；扫描通知尚未确定时为 null |
| issues / evidence | array | 已知问题与来源、检查依据 |
| parse_profile | object | 针对本原件的整行排除规则和正文结束标记 |
| reviewed_parser_version | string，可选 | 本次校对对应解析器版本；版本变化后须重新校对 |

## RAG 应读取的文件

先确认 processed/quality_report.json 的 status 为 completed，再读取 processed/release_documents.json 和 processed/release_chunks.json。通过 doc_id 关联。不要扫描 database 下遗留 JSON 或 candidates 下全部文件直接入库。

条文数据保持原有五个字段，另增加两个页码字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| doc_id | string | 与发布文档清单关联 |
| chunk_id | string | doc_id 加顺序号；本批保持原有编号。文件内容变化后应按版本管理，不跨版本混用 |
| chapter | string | 当前章名；未识别则为空字符串 |
| article | string | 原条号，例如第七条 |
| content | string | 条文正文，保留排版换行，做 NFKC 和明确的部首映射，不擅自补字 |
| page_start / page_end | integer | 当前 file_path 所指 PDF 从 1 起算的页序号，包含跨页续文；不是印刷页码 |

向模型提供的引用至少包含 title、version_note、article、source_url、page_start、page_end。正文较长时由 RAG 同学拆分子切片，保留父 chunk_id 与条号，不截掉条件或例外。

## 质量与更新

每次运行重新解析；文件哈希、解析器版本、条号顺序、预期条数、空正文、无文字页面和审批状态共同控制发布资格。自动检查无法识别所有裁切和错字，人工发现的问题必须写入 issues，不能仅凭“解析成功”批准。

批准新文件前，需要核对原件、来源、正文及解析结果，设置 quality_status 和 approved_for_integration，并将 reviewed_parser_version 设为实际校对版本。更新文件后先撤销审批，再校对；不能仅更新哈希来绕过复核。

候选正文和原始提取文本为排错材料；*.excluded.json 保留排除行及理由，包含首页元数据和页脚。正式标题、日期等应从原件和来源清单读取，不从“被排除”推断其没有价值。

当前程序面向按章条编排的文字 PDF。扫描、复杂表格、附件、条之一、非连续条号及按“一、（一）”组织的通知需要专门适配；失败或无文字输入不能视为已完成采集。

## 联调样例

qa_examples.json 的问题均针对本批指定版本。expected_answer 为人工整理的参考答案，references 为可定位原文。它是冒烟测试集，不是独立评测集，不构成检索准确率或模型效果结论。

# 验证记录

## 1.0 最终验证（2026-11-20）

- 14 项离线测试全部通过，包含新增分节、跨页、重复标题、空页及 CLI 测试。
- 独立 parse + verify 对 4 份真实 PDF 通过：119 个候选块，0 发布。
- 新解析结果完整保留 pdfplumber 提取的全部非空行，含前言和页眉页脚；不等同全文人工审核。
- PowerShell 一键运行流程成功；候选文件使用相对路径。
- 下方为开发过程中的历史验证，82 条是旧解析器条文口径，119 块是新版包含前言和分节的口径。

日期：2026-11-20；环境：Windows、Python 3.12.10。

执行 `python -m unittest discover -s tests -v`：7 项全部通过。

覆盖：静态链接发现、片段 URL 去重、同内容文件复用、新旧版本保留、
待审清单导出、伪 PDF 隔离、robots 禁止访问、429 Retry-After 持久化、
有限重试、中断任务恢复、跨主机重定向拦截、原件篡改检查和下载大小限制。

另在临时目录完成第一阶段解析器桥接验证：读取现有 documents.json 的首个 PDF，
复制到临时爬虫存储，导出清单，调用现有 parse_pdf.py 并使用临时输出目录。
结果：63 条候选切片、0 份正式发布文档；源 PDF SHA-256 前后一致。
临时测试数据已由 TemporaryDirectory 自动清理，正式 processed 未写入。

CLI `python -m crawler --help` 正常返回。

## 同日后续实抓验证

新增深交所脚本变量适配与任务上限统计测试，共 9 项测试全部通过。
config/szse.json 已实抓官方行政法规栏目：1 个 HTML、4 份 PDF，0 下载失败。
任务上限跳过 16 个链接，未声称完整采集。普通重复运行新增下载为 0。
4 个原件 SHA-256 均通过核验；正式 release 文档及切片均为 0。

| 文件 | 候选条数 | 结果 |
|---|---:|---|
| 证券公司风险处置条例（2023年修订） | 63 | parsed_candidate |
| 国务院关于加强监管防范风险推动资本市场高质量发展的若干意见 | 0 | no_articles，待分节解析 |
| 国务院办公厅关于加强监管防范风险促进私募投资基金高质量发展的指导意见 | 0 | no_articles，待分节解析 |
| 国务院关于规范中介机构为公司公开发行股票提供服务的规定 | 19 | parsed_candidate |

来源：https://investor.szse.cn/lawrules/rules/regu/index.html
机器核验记录：data/szse/verification.json。
未验证项：完整栏目分页、真实网站限流行为、多站点规模、动态渲染、OCR、全文人工审核及现行效力。
config/example.json 仍是占位模板；实际可运行配置为 config/szse.json。

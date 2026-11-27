# crawler 1.3.0 最终交付版

独立 Python 金融制度采集工具，位于 E:\RAG\crawler。完成采集、下载、去重、版本保留、断点恢复、PDF 条文及分节解析、来源完整性标记、候选核验和 RAG 隔离数据导出。

## 直接运行

```powershell
cd E:\RAG\crawler
python -m pip install -r requirements.txt
python run.py --evaluate
```

不用执行 .ps1，不需要修改 PowerShell 执行策略。默认离线处理附带的真实采集样本。首次解压时先运行 `python check_package.py` 检查文件完整性。

重新联网采集并检查内容变化：

```powershell
python run.py --online --refresh --evaluate
```

新站点应使用独立配置和数据目录；固定 10 问样本仅适用于附带数据，不要对其他站点使用 --evaluate。

## 交付结果

- 4 份官方 PDF，33 页；126 个候选块，111 条隔离正文记录。
- 25 项测试通过；10 个固定问题 Top 1 / Top 5 均为 10/10。
- 2 份资料标为公开删减版，2 份完整性 unknown，全部 needs_review、0 发布。
- 软件版本 1.3.0，解析器版本 1.1.1，隔离契约 rag-sandbox-1.0。

## 阅读入口

- [交付说明](DELIVERY.md)：安装、运行、故障处理和验收。
- [RAG 接口说明](RAG_HANDOFF.md)：字段映射、隔离约束、消费方法。
- [最终验证记录](FINAL_VALIDATION.md)：测试范围与证据。
- [样本审核](review/20261120/审核报告.md)及[来源完整性复核](review/20261120/来源完整性复核.md)。

## 数据位置

`data/szse/raw/` 原件；`staging/parsed_v1/` 候选正文、分页文本和审核清单；`sandbox/records.jsonl` 下游隔离接入记录；`sandbox/evaluation.json` 固定问题的引用与排名。

## 交付范围

已验证深交所行政法规栏目 1 个页面和 4 个 PDF，未覆盖完整栏目或多站点生产规模。结构、哈希和词面检索测试不是全文人工签字审核、现行效力核验或大模型回答验收。尚未连接外部向量库或回答模型；本项目交付可接入的数据和测试基线，不宣称整个 RAG 问答系统完成。

第一阶段目录与历史 ZIP 均保留。正式入库需要独立审核、版本选择和发布流程，不能直接将 sandbox 数据视为已批准法规。

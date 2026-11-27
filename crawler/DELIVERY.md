# crawler 1.3.0 交付说明

## 交付内容

源码、站点配置、25 项测试、Python 一键入口、4 份官方 PDF 样本、HTML 来源证据、SQLite 状态、候选与隔离数据、10 问检索样例、审核记录、文件 SHA-256 清单。所有内容在 crawler 目录中，不依赖第一阶段代码。

## 安装

要求 Python 3.10+，实测 Windows Python 3.12.10。PDF 依赖固定 pdfplumber==0.11.10；网络与 SQLite 使用标准库。安装依赖时需要网络，附带样本的日常复验无需网络。

```powershell
python check_package.py
python -m pip install -r requirements.txt
python run.py --evaluate
```

可选独立环境，无需激活脚本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --evaluate
```

## 运行模式

- `python run.py`：离线导出、解析、核验、隔离适配。
- `python run.py --evaluate`：以上流程加固定 10 问检索基线。
- `python run.py --online --refresh --evaluate`：先重新采集检查远端变化，再执行完整流程。
- `python -m unittest discover -s tests -v`：本地 HTTP 服务及解析/接口回归测试，无需官网访问。

run.py 使用当前 Python 解释器，从项目目录运行子命令；任何步骤退出非零即停止。--refresh 必须配合 --online。离线无数据时报错，不生成假的成功结果。

## 采集配置和边界

config/szse.json 是已验证小样本配置，max_tasks=5（1 页+4 PDF），depth=1；首轮另外 16 个匹配链接因上限未采集。不宣称完整分页覆盖。扩大范围前须验证分页链接规则；新站点使用独立 --data 目录，避免来源混用。

已有 URL 普通运行不重抓；--refresh 才刷新完成任务。retry 到期后再次运行；quarantined 保留错误，需要检查原因后用新的数据目录重采。状态保存在 state.db，单进程文件锁防止并发写入。

HTML a[href] 与深交所静态 curHref/curTitle 支持；不执行脚本，不处理登录和验证码。支持 robots、允许主机（含重定向）、间隔、超时、大小限制、有限退避及 Retry-After。

## 数据契约

staging/documents.json 是原始候选登记；staging/parsed_v1/documents.json 为带完整性状态的候选清单。file_path 相对各自清单目录，解压移动后仍有效。

解析 kind 包括 article、section、structure、preamble；retrieval_eligible 是结构用途，不是批准状态。章结构/前言不单独输出到隔离正文。页码为 PDF 物理页，从 1 起。页尾页码只在实际页号与底部位置均符合时排除，排除项留存在 report.json。保留其余提取文本及跨页范围；heading_is_first_line 明确标题仅为首行，完整文字查看 content。

完整性为 abridged 或 unknown，不能把未发现声明当作完整。所有记录 needs_review、approved_for_integration=false。正式发布为 0。RAG 字段详见 RAG_HANDOFF.md。

sandbox/manifest.json 只有 status=completed 才可消费，且 records.jsonl 哈希必须与 records_sha256 一致。开始适配时旧快照会置为 running；失败时不能继续使用旧记录作为本轮成功产物。数据源变化后重新运行，禁止只读取旧 evaluation.json 推断当前结果。

## 故障处理

- PowerShell 不允许运行脚本：使用 python run.py，不需要解除执行策略。
- 依赖缺失：使用同一解释器执行 python -m pip install -r requirements.txt。
- 采集返回 1：检查 data/站点/report.json 及 state.db 的 status/error，临时失败等待 due 到期再试。
- manifest.status=running：上次操作未成功完成，修复后重新运行，不导入旧隔离记录。
- 原件哈希不符：停止使用该数据，恢复可信备份或新目录重采；不得直接修改登记哈希来绕过核验。
- 本地已有样本 downloaded=0：正常复用已完成任务。

## 验收和审核

首先校验交付文件，再运行测试及一键流程。预期附带样本为 4 文档、126 候选、111 隔离记录、0 发布。固定 10 问命中是词面基线，不代表实际向量库或大模型质量。

全文人工签字、效力核查、OCR、复杂表格、统一法律身份、正式发布/撤回及多站点生产调度不在本版已完成范围。不得将公开删减版升级为完整全文。

历史开发记录保留在 VALIDATION.md 和 review/ 中，数量/版本以本说明及最终验收为准。正式交付包为 crawler-1.3.0.zip，旧版包不覆盖。

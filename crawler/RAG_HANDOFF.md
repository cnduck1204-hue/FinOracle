# RAG 隔离接入说明

范围：将爬虫待审数据转换为统一 JSONL，并验证固定样本的本地词面检索。没有连接正式 RAG、向量库或大模型，不生成金融法律结论。第一阶段项目未改动。

## 运行

```powershell
python -m crawler sandbox --data data/szse
python -m crawler evaluate --data data/szse --cases config/qa_cases.json
```

两条命令均先调用原件和候选核验；evaluate 重新生成隔离数据，避免使用过期候选。
输出位于 data/szse/sandbox，不使用 release 文件名。
消费前必须验证 manifest.status=completed，并对 records.jsonl 计算 SHA-256 与 records_sha256 比较。开始重建时旧 manifest 会失效；失败后不得将旧记录当作当前快照。

## 产物

- records.jsonl：111 条正文记录，一行一条；过滤 11 个结构块、4 个前言块。
- manifest.json：输入文件与输出文件 SHA-256、数量和 environment=sandbox。
- evaluation.json：10 个固定问题、预期文档与条文/分节、Top 5 证据及命中排名。
- config/qa_cases.json：预期引用由样本内容确定，固定保存，不从检索排名反向生成。

## 统一字段及第一阶段映射

| 字段 | 含义 / 下游处理 |
|---|---|
| id | 索引唯一键，包含文档版本、解析器版本、原切片 ID 和正文内容的哈希 |
| source_chunk_id | 回溯原候选切片；不是跨解析器稳定主键 |
| doc_id / revision_id | 文档候选身份 / 原件内容版本，不等同统一法律身份 |
| text | 完整候选正文；对应第一阶段 content，不能截断后丢失引用 |
| kind | article 或 section；第一阶段 article 不能直接替代 section |
| context_heading | 章/节上下文；对应第一阶段 chapter 的语义，但可为空 |
| citation | title、source_url、download_url、PDF 物理页码起止、原件 sha256 |
| source_completeness / completeness_notice | abridged 或 unknown 及展示提示；引用删减版时必须保留 |
| validity_status | 当前 unknown，不得解释为现行有效 |
| review_status / approved_for_integration | needs_review / false，不能因为检索命中而改成批准 |
| environment | 固定 sandbox，供下游路由到隔离集合 |

该表给出统一消费接口和字段映射，不修改第一阶段文件，也不声称已有下游服务接受此接口。第一阶段旧切片如需合并，应通过单独适配器补充完整性与审批字段，不能默认为已核验版本。

## 检索验收结果

本轮固定 10 个问题，Top 1=10/10、Top 5=10/10。覆盖两份条文文件及两份删减版意见。方法是中文二元字符 TF-IDF 余弦相似度，无额外依赖。
这一结果只说明样本证据可被基础词面检索命中，不能外推到语义召回、长问题、无答案问题、历史版本冲突或大模型回答准确率。没有自动拒答阈值；任意非零分数不代表能回答。

## 正式 RAG 接入前需要完成

1. 明确下游向量库/embedding 模型、接口字段及索引命名，只导入 sandbox 集合。
2. 用户审核批准、版本选择与法律效力核查后，才可建立独立正式发布流程。
3. 新旧解析器/内容会生成不同 ID；下游需按发布快照替换或撤回旧索引，不能只追加。
4. 回答时带文档、页码、原件链接与删减提示；检索不到不能断言法规没有规定。
5. 加入真实用户问题、无答案与跨版本冲突测试，再评价生成模型回答。

# 抽取与规则阶段计划清单

更新日期：2026-10-21。评测用途和代码入口见 [评测分工](evaluation_layout.md)，规则清单与输入契约规范见 [规则清单与契约规范](rule_inventory_and_contracts.md)，**LLM 抽取评估集搭建指南见 [LLM 抽取评估集搭建指南](extraction_eval_guide.md)**。

## 已完成的基础工作

- [x] 将原 50 条需求及必要证据标注明确保留为 RAG 评测数据。
- [x] 分离下游流程诊断入口、助手合规参考和运行统计；保留旧命令兼容。
- [x] 实现原始模型响应的离线回放，支持不调用 API 地验证代码变更。
- [x] 完成正文、目标表达、人员关联和双侧冲突引用的第一批修复。
- [x] 92 项代码回归测试通过；整理前后 50 条抽取和规则报告逐项一致。

以上是实现和流程验证，不代表已达到全部跨项目端到端泛化准确率要求。

## 后续执行顺序

按规则类别逐项推进：先确定规则判断什么、需要什么输入，再实现和验证规则，随后设计对应的 LLM 抽取提示词。无需等待整个引擎全部完成才验证原文能否提供输入，但不再以先建抽取评测集作为起点。

### 1. 梳理规则清单（已完成）

- [x] 从招标要求明确需要判断的条件：资格等级、证书时效、人员数量、业绩、材料组成、报价、工期、承诺与评分等（基于 S604 全量 359 条标注要求 `T0001`–`T0359` 梳理为 9 大形式化规则族 `R01`–`R09` 共 308 条，以及 4 类不可形式化/人工复核条款共 51 条）。
- [x] 为每条规则记录招标依据、适用范围、已有实现和缺口；八分类结果只用于组织需求，不代替具体规则。
- [x] 拆分复合条件，明确且／或关系、人数要求和例外；无法形式化的主观判断明确交由人工。

验收产物：
- 规则清单与规范说明：[`docs/rule_inventory_and_contracts.md`](rule_inventory_and_contracts.md)（第一部分）
- 机器可读 359 条需求到规则的完整映射：[`SystemCode/data/rule_eval/s604_clause_rule_mapping.json`](../SystemCode/data/rule_eval/s604_clause_rule_mapping.json)

### 2. 定义规则输入与判断逻辑（已完成）

- [x] 为每条规则定义输入字段、类型、单位、主体／岗位、项目／时期及必需引用。
- [x] 区分招标约束、投标事实、招标上下文和项目配置的来源；评审基准日期等信息缺失时不能让 LLM 猜测。
- [x] 明确比较运算、评分边界、时效和延期依据，以及多条件合并方式。
- [x] 明确输入缺失、相互冲突或规则未支持时的输出及原因，禁止默认为通过。
- [x] 据此审视现有抽取字段结构，标明保留、调整和新增字段及兼容方式（见 [`SystemCode/src/extraction/schema.py`](../SystemCode/src/extraction/schema.py)）。

验收产物：
- 规则输入契约、判断表、Schema 演进审计表和结构化样例：[`docs/rule_inventory_and_contracts.md`](rule_inventory_and_contracts.md)（第二部分）

### 3. 实现规则并独立验证（已完成）

- [x] **优先处理证书时效**：历史项目使用对应评审日期（`review_reference_date`，如 S604 为 `2022-11-11`），支持官方资质统一延续有效期（`extension_valid_to`），并拦截仅凭自填《基本情况表》的假阳性（修复 `T0004` 仅等级满足即算通过的漏洞）。
- [x] 完善人员身份、拟任角色与证书的关联，保持姓名、专业和证书持有人一致（扩展支持附录 6 全部 7 类关键岗位与公路工程相关专业集合）。
- [x] 支持多人岗位的数量及逐人条件（`R07_STAFFING_COUNT_AND_QUALIFICATION`），区分“至少 $N$ 人合格 (`AT_LEAST_N_QUALIFIED`)”和“每人满足 (`ALL_PROPOSED_QUALIFIED`)”，按姓名去重并严禁拼接不同人的资格。
- [x] 建立业绩材料链（`R08_PERFORMANCE_CHAIN_AND_SCORING`）与多字段公式净基数核算（`R09_FORMULA_BOUND`）：验证合同、中标通知书、四方盖章交竣工验收证明与当前拟任人员、业主人员变更批复之间的对应，以及多项目去重累加计分与安全生产费 1.5% 净基数扣减公式。
- [x] 使用人工构造、预期结果明确的结构化输入验证规则，不依赖 RAG 或 LLM。
- [x] 为每类规则准备正例、反例、等于阈值／刚越界、单位不一致、缺失信息、主体不符及冲突用例（共 69 条结构化验证用例）。
- [x] 将合规结论和评分数值分别标注；证据不足不当作不合规，零分不自动当作不合规。
- [x] 建立独立入口和结果报告，单列错误放行（`false_pass`）、错误拒绝（`false_reject`）、合理弃判（`justified_abstain`）及评分错误（`score_error`）。

验收产物：
- 可执行符号规则引擎：[`SystemCode/src/rules/rule_engine.py`](../SystemCode/src/rules/rule_engine.py)
- 独立规则评测器与 CLI 入口：[`SystemCode/src/evaluation/rule_eval.py`](../SystemCode/src/evaluation/rule_eval.py)、[`SystemCode/evaluate_rule_engine.py`](../SystemCode/evaluate_rule_engine.py)
- 独立规则验证基准集（69 条）：[`SystemCode/data/rule_eval/s604_rule_validation_suite.json`](../SystemCode/data/rule_eval/s604_rule_validation_suite.json)
- 自动比较报告（JSON 与 Markdown）：[`SystemCode/data/rule_eval/s604_rule_eval_report.json`](../SystemCode/data/rule_eval/s604_rule_eval_report.json)、[`docs/experiments/s604_rule_engine_validation.md`](experiments/s604_rule_engine_validation.md)
- 单元回归测试：[`SystemCode/tests/test_rule_contracts_and_suite.py`](../SystemCode/tests/test_rule_contracts_and_suite.py)

### 4. 根据规则输入设计 LLM 提示词（已完成）

- [x] 按已明确的规则输入契约设计抽取格式和提示词，只提取所需约束、事实、关联和原文引用（实现于 [`SystemCode/src/extraction/llm_extractor.py`](../SystemCode/src/extraction/llm_extractor.py) 的 `SYSTEM_EXTRACTION_PROMPT` 与契约投影后处理 `_normalize_llm_extraction()`）。
- [x] LLM 不代替规则引擎完成比较、计算和最终合规判断；缺失信息保留未知。
- [x] 使用少量原文检查输入是否可获得；若原文不能支持某字段，调整契约或补充上下文来源，不补造事实。
- [x] 记录契约与提示词版本（`CONTRACT_VERSION = "passage-facts-v11-arch"`），处理已有缓存和下游规则的兼容关系，且不含任何项目特化硬编码或答案泄露。

验收产物：
- 与规则输入契约一致的提示词、Schema 活跃槽位投影与原文引用对齐实现：[`SystemCode/src/extraction/llm_extractor.py`](../SystemCode/src/extraction/llm_extractor.py)

### 5. 建立抽取评测并联调（已完成，金标准待人工审定升级）

- [x] 按规则输入契约标注应抽出的约束、事实、主体、引用及应保持未知的字段（由 [`SystemCode/scripts/build_s604_extraction_benchmark.py`](../SystemCode/scripts/build_s604_extraction_benchmark.py) 构建 50 条基准集）。
- [x] 选取有完整固定证据的代表例，独立衡量 LLM 抽取；不把检索漏证据混入抽取能力判断。
- [x] 区分开发样例（10 条 `dev` 覆盖 `R01`–`R09`）和留出的测试样例（40 条 `test`），记录来源及重叠；本轮反复调试的 50 条不作为跨项目独立泛化测试。
- [x] 助手预标注与人工审定分别记录（当前 50 条 `review_status="assistant_preannotated"`，后续人工逐条核对无误后可改为 `"human_verified"`）。
- [x] 建立独立计分入口（[`SystemCode/src/evaluation/extraction_eval.py`](../SystemCode/src/evaluation/extraction_eval.py)、[`SystemCode/evaluate_extraction.py`](../SystemCode/evaluate_extraction.py)），独立报告抽取正确性、遗漏、主体关联、引用支持和无依据补值，并将下游规则联动一致性作为附加诊断指标单独列出。
- [x] 冻结输入与标准，使用真实 `deepseek-chat` API 完成 `dev`（10 条）、`test`（40 条）与 `all`（50 条）在线抽取与规则联调验证：
  - `dev`（10 条）：`constraint_slot_accuracy=94.34%`、`evidence_slot_accuracy=86.49%`、`subject_binding_accuracy=100.00%`、`citation_support_rate=70.00%`、`unknown_abstention_rate=100.00%`（下游联动：`downstream_verdict_agreement=80.00%`、`downstream_score_agreement=100.00%`）。
  - `test`（40 条）：`constraint_slot_accuracy=78.36%`、`evidence_slot_accuracy=81.20%`、`subject_binding_accuracy=75.00%`、`citation_support_rate=75.00%`、`unknown_abstention_rate=95.74%`（下游联动：`downstream_verdict_agreement=75.00%`、`downstream_score_agreement=95.00%`）。
  - `all`（50 条）：`constraint_slot_accuracy=82.14%`、`evidence_slot_accuracy=82.84%`、`subject_binding_accuracy=81.82%`、`citation_support_rate=74.00%`、`unknown_abstention_rate=96.49%`（下游联动：`downstream_verdict_agreement=76.00%`、`downstream_score_agreement=96.00%`）。

验收产物：
- 独立抽取基准集（50 条）：[`SystemCode/data/extraction_eval/s604_extraction_benchmark.json`](../SystemCode/data/extraction_eval/s604_extraction_benchmark.json)
- 独立抽取评测器与 CLI 入口：[`SystemCode/src/evaluation/extraction_eval.py`](../SystemCode/src/evaluation/extraction_eval.py)、[`SystemCode/evaluate_extraction.py`](../SystemCode/evaluate_extraction.py)
- 真实 API 在线评测报告：[`SystemCode/reports/extraction_eval/dev_report.json`](../SystemCode/reports/extraction_eval/dev_report.json)、[`SystemCode/reports/extraction_eval/test_report.json`](../SystemCode/reports/extraction_eval/test_report.json)、[`SystemCode/reports/extraction_eval/all_report.json`](../SystemCode/reports/extraction_eval/all_report.json)
- 单元回归测试：[`SystemCode/tests/test_extraction_benchmark_and_eval.py`](../SystemCode/tests/test_extraction_benchmark_and_eval.py)

### 持续执行：分层复测与项目阶段验收

- [ ] 原 50 条继续单独报告 RAG 命中、必要证据召回、完整证据率和排序指标。
- [ ] 对 `s604_extraction_benchmark.json` 的 50 条预标注槽位真值进行人工复核，将 `review_status` 从 `assistant_preannotated` 升级为 `human_verified`。
- [ ] 端到端流程另报运行统计、缺失条件和错误来源；没有依据时不强行将“证据不足”归因到某一环节。
- [ ] 引入未参与调试的新项目（如已解压的其它 79 个项目数据集）或保留样本后，再讨论泛化能力与下一阶段准入。

验收结论必须列明未覆盖条件和人工核对需求，不以单个综合通过率替代各阶段评估。

## 提交记录说明

代码修复、流程诊断拆分、测试、原件引用修正记录、助手参考及实验摘要已在 `afd7583` 本地提交。
Step 1–3（规则清单映射、契约规范、规则引擎扩展及 69 条独立基准集）与 Step 4–5（LLM 抽取契约对齐、50 条抽取评测集、独立评测器与在线 DeepSeek API 评测报告）按阶段独立提交。
本地原始 PDF、模型权重、索引、OCR／抽取缓存、完整 API 响应和运行输出继续按现有忽略规则保留在本机。

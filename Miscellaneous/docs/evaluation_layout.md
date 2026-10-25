# 评测与流程诊断分工

更新日期：2026-10-21。

## 入口与数据

| 用途 | 入口或位置 | 输入／标准答案 | 当前状态 |
|---|---|---|---|
| RAG 评测 | `SystemCode/evaluate_evidence_rag.py` | 原 50 条需求＋投标原文必要证据组 | 已有检索参考，报告命中率、证据召回、完整证据率、MRR |
| RAG 漏检诊断 | `SystemCode/diagnose_evidence_rag.py` | 冻结检索结果＋同版本父段落＋证据标注 | 区分排序未命中和索引文本不匹配 |
| 下游流程诊断 | `SystemCode/run_reasoning_diagnostics.py` | 冻结检索实际返回段落；默认无结论标准答案 | 检查抽取、引用校验和规则能否运行，统计输出状态 |
| 离线回放 | `SystemCode/replay_compliance_responses.py` | 已保存的原始模型响应＋冻结输入 | 不调用模型，观察代码变更对流程输出的影响 |
| 抽取模块独立评测 | `SystemCode/evaluate_extraction.py` | `SystemCode/data/extraction_eval/s604_extraction_benchmark.json`（50 条固定证据＋契约化约束、事实、主体、引用及应留空字段；当前为 `assistant_preannotated` 预标注态，待人工逐条复核升级为 `human_verified`） | **已建立独立入口与 50 条抽取评测集（10 dev / 40 test）**，完成 `deepseek-chat` 在线真实 API 评测（5 项核心槽位/引用/弃权指标与下游规则联动一致性分开统计） |
| 规则模块正式评测 | `SystemCode/evaluate_rule_engine.py` | `SystemCode/data/rule_eval/s604_rule_validation_suite.json`（已确认的结构化约束／事实＋预期结论和分数） | **已建立独立入口与 69 条覆盖 R01–R09 的正例/反例/边界/单位不符/缺失/主体错配/冲突基准集**（不调用 RAG 或 LLM，单列错误放行、错误拒绝、合理弃判与评分错误） |

`SystemCode/evaluate_compliance_reasoning.py` 保留为旧命令兼容入口，执行相同的流程诊断并提示迁移。旧名称不再意味着存在正式规则评测集。

## 目录

```text
SystemCode/data/evidence_annotations/s604/
  annotations.json                  # 原 50 条 RAG 必要证据标注
  sources.json                      # 原件身份与定位
  pending.json / progress.md         # RAG 标注状态
  quote_repairs.json
  visual_reference_repairs.json     # 证据引用修正记录

SystemCode/data/reasoning_diagnostics/s604/
  compliance_verdict_labels.json    # 历史助手结论参考，保留原内容
  compliance_calibrated_reference.json
  README.md                         # 仅供诊断，不是正式金标准

SystemCode/data/rule_eval/
  s604_clause_rule_mapping.json     # S604 全量 359 条招标需求到形式化规则/人工复核的映射
  s604_rule_validation_suite.json   # 69 条独立于 RAG/LLM 的结构化规则验证基准集
  s604_rule_eval_report.json        # 规则引擎独立验证自动比较报告 (JSON)

SystemCode/data/extraction_eval/
  s604_extraction_benchmark.json    # 50 条固定证据的 LLM 抽取评测集 (10 dev / 40 test)

SystemCode/reports/extraction_eval/
  dev_report.json                   # 10 条 dev 集真实 DeepSeek API 抽取评测报告
  test_report.json                  # 40 条 test 集真实 DeepSeek API 抽取评测报告
  all_report.json                   # 50 条全集真实 DeepSeek API 抽取评测报告

SystemCode/tests/
  test_evidence_metrics.py          # 检索计分实现的测试
  test_extraction_semantics.py
  test_extraction_rules_v6.py       # 抽取校验的正反例回归
  test_compliance_rules.py          # 规则实现的回归测试
  test_rule_contracts_and_suite.py  # Step 1-3 规则清单映射、契约与 69 条独立基准集回归测试
  test_extraction_benchmark_and_eval.py # Step 4-5 抽取基准集、契约对齐与评测器回归测试
  test_pipeline_diagnostics.py      # 防止混用标签、指标与统计
```

两份助手结论参考已从 RAG 标注目录迁出，文件内容和哈希不变。此次整理不修改原 50 条需求、检索标注和冻结实验输入。

完整实验输出继续保存在 Git 忽略的 `SystemCode/reports/rag_experiments/`，保持旧路径以便复现已有记录。历史路径名不决定评测用途；新报告以 `evaluation_scope` 标识用途，不改写旧运行快照。

## 报告口径

- RAG 的“证据完整”指已找齐需求必需的证据组，不要求穷尽所有重复段落，也不意味着条款合规。
- 流程诊断的 `verdict_counts` 和 `decision_coverage` 仅描述输出数量与判断覆盖率。
- 默认报告不产生准确率字段，也不把原 50 条标成“50 条待标注合规样本”。`prepare` 只生成 `cases.json`。
- “证据不足”可能来自检索、抽取、引用校验或规则条件缺失，不能仅凭总状态确定错误环节。逐条原文、抽取问题和规则解释用于排查。
- 助手结论比较只有显式传 `--reference_labels` 才执行，单独输出 `reference_comparison.json`；一致率不进入主诊断统计。
- 未来若用 `--labels` 做人工结论比较，标签必须另行提供，具有 `evaluation_scope=end_to_end_compliance`、案例哈希和人工核对记录；结果单独写入 `human_end_to_end_comparison.json`。它仍是端到端结论指标，不能解释为单独的抽取或规则准确率。
- 单元测试通过数量不等于真实数据集准确率。正式规则模块评测（`SystemCode/evaluate_rule_engine.py`）使用独立于 RAG 与 LLM 的结构化正例、反例、边界、缺失、主体错配及冲突案例，单独报告错误放行、错误拒绝、合理弃判及评分错误。

操作命令见 [流程诊断说明](evidence_reasoning.md)，RAG 指标见 [检索评测说明](passage_rag_evaluation.md)，规则清单与输入契约见 [规则清单与契约规范](rule_inventory_and_contracts.md)，后续工作见 [计划清单](extraction_rules_plan.md)。

## 本次整理验证

- 92 项单元／回归测试通过，旧命令入口仍可使用。
- 独立规则评测基准集（69 条）100% 通过（0 错误放行、0 错误拒绝、17 合理弃判、0 过度弃判、0 评分错误）。
- 独立 LLM 抽取评测集（50 条，固定检索证据，`deepseek-chat`，契约版本 `passage-facts-v11-arch`）在线 API 评测结果（当前金标准为 `assistant_preannotated` 预标注态）：
  - **LLM 抽取核心指标（全集 50 条）**：招标约束槽位准确率 `82.14%`、投标证据槽位准确率 `82.84%`、主体关联准确率 `81.82%`、原文引用支持率 `74.00%`、未知字段弃权率（防幻觉）`96.49%`。
  - **下游规则联动附加诊断（全集 50 条）**：将 LLM 抽取结果送入规则引擎后的结论一致率 `76.00%`、计分一致率 `96.00%`（主要差异为 LLM 抽取或引用未命中时规则引擎触发保守弃判 `INSUFFICIENT_EVIDENCE`）。
- 原 50 条 RAG 标注哈希保持 `993c0e964e0030c04787bdb81cfb35e278191427b125c49cb60f2c982e6d1c87`。
- 两份助手参考迁移前后内容一致。

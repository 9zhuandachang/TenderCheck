# 评测与流程诊断分工

更新日期：2026-10-20。

## 入口与数据

| 用途 | 入口或位置 | 输入／标准答案 | 当前状态 |
|---|---|---|---|
| RAG 评测 | `SystemCode/evaluate_evidence_rag.py` | 原 50 条需求＋投标原文必要证据组 | 已有检索参考，报告命中率、证据召回、完整证据率、MRR |
| RAG 漏检诊断 | `SystemCode/diagnose_evidence_rag.py` | 冻结检索结果＋同版本父段落＋证据标注 | 区分排序未命中和索引文本不匹配 |
| 下游流程诊断 | `SystemCode/run_reasoning_diagnostics.py` | 冻结检索实际返回段落；默认无结论标准答案 | 检查抽取、引用校验和规则能否运行，统计输出状态 |
| 离线回放 | `SystemCode/replay_compliance_responses.py` | 已保存的原始模型响应＋冻结输入 | 不调用模型，观察代码变更对流程输出的影响 |
| 抽取模块正式评测 | 待建立独立数据和入口 | 固定证据＋人工核对的事实、约束、主体和引用 | **尚未建立**，不能用合规状态统计替代 |
| 规则模块正式评测 | 待建立独立数据和入口 | 已确认的结构化约束／事实＋预期结论和分数 | **尚未建立**；现有单元测试是代码回归检查 |

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

SystemCode/tests/
  test_evidence_metrics.py          # 检索计分实现的测试
  test_extraction_semantics.py
  test_extraction_rules_v6.py       # 抽取校验的正反例回归
  test_compliance_rules.py          # 规则实现的回归测试
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
- 单元测试通过数量不等于真实数据集准确率。正式模块评测应包含独立于本轮调试样本的正例、反例、边界、缺失及冲突案例。

操作命令见 [流程诊断说明](evidence_reasoning.md)，RAG 指标见 [检索评测说明](passage_rag_evaluation.md)，后续工作见 [计划清单](extraction_rules_plan.md)。

## 本次整理验证

- 81 项单元／回归测试通过，旧命令入口仍可使用。
- 原 50 条 RAG 标注哈希保持 `993c0e964e0030c04787bdb81cfb35e278191427b125c49cb60f2c982e6d1c87`。
- 两份助手参考迁移前后内容一致。
- 零 API 回放与整理前的 v6 回放比较，全部 50 条抽取对象和规则报告逐项相同；本轮只调整组织和统计口径。
- 新诊断输出位于本地 `SystemCode/reports/rag_experiments/s604_pipeline_diagnostics_layout_check/`，默认主报告不含准确率或“待标注合规样本数”。
- 正式抽取、规则评测数据集和入口仍待按计划另行建立。该验证记录描述整理时的状态，不代表远程发布。

# LLM 抽取模块评估集搭建指南（5080 节点接续）

本指南用于指导在 5080 机器上完成 **Step 4（LLM 抽取 Prompt 对齐）** 与 **Step 5（LLM 抽取模块独立评估集构建）**。请保持设计简洁，避免过度工程化。

## 1. 数据来源与定位

- **基准样本**：直接复用 S604 项目的 50 条 RAG 评估集条款（[`SystemCode/data/evidence_annotations/s604/annotations.json`](../SystemCode/data/evidence_annotations/s604/annotations.json)）。
- **规则与槽位契约**：严格对齐 Step 1–3 已完成的规则映射 [`SystemCode/data/rule_eval/s604_clause_rule_mapping.json`](../SystemCode/data/rule_eval/s604_clause_rule_mapping.json) 与槽位定义 [`SystemCode/src/extraction/schema.py`](../SystemCode/src/extraction/schema.py)（规范说明见 [`docs/rule_inventory_and_contracts.md`](rule_inventory_and_contracts.md)）。
- **输出位置**：建议保存为 `SystemCode/data/extraction_eval/s604_extraction_benchmark.json`。

## 2. 输入构造（利用 5080 本地 RAG 数据）

- **固定原文证据输入**：不要只用 `annotations.json` 里的短 `quote`；请从 5080 本地的 RAG 父段落索引（`parent_chunks.jsonl`）或冻结检索结果中，提取每条需求对应的完整父段落上下文（`evidence_context`，含正文、表格文本及印章元数据）作为 LLM 抽取的固定输入，从而将检索误差与抽取能力解耦。
- **项目级上下文**：评审基准日统一使用配置传入（S604 为 `review_reference_date: "2022-11-11"`），不让 LLM 猜测。

## 3. 每条样本标注要素

每条样本只需标注以下核心信息：
1. **`gold_constraint_slots`**：按 `TenderConstraintSlots` 契约从招标条款提取的目标约束。
2. **`gold_evidence_slots`**：按 `BidEvidenceSlots` 契约从给定投标父段落提取的事实、主体/岗位绑定及必需的原文引用。
3. **`must_remain_unknown`**：给定段落中确实未提供、LLM 必须保持 `null`（严禁脑补或默认合规）的关键字段（例如 `T0004` 仅有《基本情况表》自报而无证书有效期 `valid_to`）。
4. **`split` 标记**：50 条内部按 `R01`–`R09` 选约 10 条标为 `"dev"`（用于调试第 4 步抽取 Prompt），其余约 40 条标为 `"test"`（用于独立评测抽取效果）。

## 4. 评测与推进顺序

1. **先标数据**：基于 5080 本地的完整段落完成上述 50 条抽取评估集标注，并验证将 `gold_constraint_slots` + `gold_evidence_slots` 喂给 [`rule_engine.py`](../SystemCode/src/rules/rule_engine.py) 能得出预期的合规结论。
2. **再改 Prompt（Step 4）**：对照 `dev` 集和新版 `schema.py` 更新 [`llm_extractor.py`](../SystemCode/src/extraction/llm_extractor.py) 的提示词与解析逻辑（LLM 只抽事实与引用，不做合规推理和算分）。
3. **独立计分（Step 5）**：在固定证据输入下评测 LLM 抽取的槽位正确率、主体关联率、引用支持率以及无依据补值（幻觉）率。

# Neuro-Symbolic Compliance Reasoning Evaluation Report (S604)

## 1. Benchmark Overview
- **Total Test Cases**: `100` (`50` Real S604 Compliant Cases + `50` Counterfactual Violation Cases)
- **LLM Slot Extractor**: `deepseek-chat` (JSON Schema Mode, `temperature=0.0`)
- **Symbolic Rule Engine**: `ComplianceRuleEngine` (`R01_NUMERIC_BOUND` .. `R06_QUALITATIVE_AND_SCORING`)

## 2. Three-Way Reasoning Ablation Results

| Experiment | Slot Extractor | Compliance Reasoner | Overall Accuracy | Compliant Acc | Violation Precision | Violation Recall | Violation F1 |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Exp 1: Regex + Symbolic Rules** | `HeuristicSlotExtractor` | `ComplianceRuleEngine` | `76.00%` | `68.00%` | `72.41%` | `84.00%` | `0.7778` |
| **Exp 2: Pure LLM Direct Verdict** | `deepseek-chat` (End-to-End) | None (Black-Box LLM) | `98.00%` | `96.00%` | `96.15%` | `100.00%` | `0.9804` |
| **Exp 3: Ours (LLM Extraction + Symbolic Rules)** | `deepseek-chat` (`src/extraction`) | `ComplianceRuleEngine` (`src/rules`) | **`99.00%`** | **`98.00%`** | **`98.04%`** | **`100.00%`** | **`0.9901`** |

## 3. Per-Category Accuracy (`Exp 3: Ours`)

| Requirement Category | Accuracy |
| :--- | :---: |
| `Commercial` | `100.00%` |
| `Consistency` | `100.00%` |
| `Document` | `100.00%` |
| `Integrity` | `100.00%` |
| `Qualification` | `100.00%` |
| `Schedule` | `100.00%` |
| `Scoring` | `91.67%` |
| `Technical` | `100.00%` |

## 4. Per-Symbolic-Rule Accuracy (`Exp 3: Ours`)

| Symbolic Rule ID | Accuracy |
| :--- | :---: |
| `R01_NUMERIC_BOUND` | `100.00%` |
| `R02_QUALIFICATION_HIERARCHY` | `100.00%` |
| `R03_RMB_UPPER_AND_CONSISTENCY` | `100.00%` |
| `R04_DOCUMENT_AND_SEAL` | `100.00%` |
| `R05_PROHIBITION_AND_COMMITMENT` | `100.00%` |
| `R06_QUALITATIVE_AND_SCORING` | `93.75%` |

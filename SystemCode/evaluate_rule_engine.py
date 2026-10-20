"""
CLI Runner for Standalone Symbolic Rule Engine Evaluation (`SystemCode/evaluate_rule_engine.py`).

Runs `ComplianceRuleEngine` on deterministic, human-constructed structured test cases
without invoking RAG retrieval or LLM extraction, reporting:
  - False Passes (`false_pass`, 错误放行)
  - False Rejections (`false_reject`, 错误拒绝)
  - Justified Abstentions (`justified_abstain`, 合理弃判)
  - Unjustified Abstentions (`unjustified_abstain`, 过度弃判)
  - Scoring Errors (`score_error`, 评分错误)
"""

import argparse
import json
from pathlib import Path

from src.evaluation.rule_eval import evaluate_rule_suite


def format_markdown_report(result: dict) -> str:
    b = result["diagnostic_buckets"]
    lines = [
        "# 规则引擎独立验证报告（Step 3 自动比较报告）",
        "",
        f"- **评测范围 (`evaluation_scope`)**: `{result['evaluation_scope']}`（纯符号规则独立验证，不依赖 RAG 检索或 LLM 抽取）",
        f"- **基准项目**: `{result['project_id']}`",
        f"- **用例审定状态 (`review_status`)**: `{result['review_status']}`",
        f"- **总用例数**: **{result['total_cases']}** 条（通过 **{result['total_passed']}** 条，通过率 **{result['overall_pass_rate']*100:.2f}%**）",
        f"- **合规判定用例 (`COMPLIANT`/`NON_COMPLIANT`/`INSUFFICIENT_EVIDENCE`/`CONFLICTING_EVIDENCE`/`UNSUPPORTED_RULE`)**: {result['compliance_verdict_exact_match']}/{result['compliance_cases_total']} (**{result['compliance_verdict_accuracy']*100:.2f}%**)",
        f"- **独立计分用例 (`SCORED` 含 0 分边界与累加封顶)**: {result['scoring_exact_match']}/{result['scoring_cases_total']} (**{result['scoring_accuracy']*100:.2f}%**)",
        "",
        "## 1. 关键安全与错误分桶统计（单列项）",
        "",
        "| 诊断指标 | 数量 | 用例编号列表 | 说明 |",
        "| --- | ---: | --- | --- |",
        f"| **错误放行 (`false_pass`)** | **{b['false_pass_count']}** | `{b['false_pass_cases']}` | 预期非通过却被判为 `COMPLIANT`（安全红线，必须为 0） |",
        f"| **错误拒绝 (`false_reject`)** | **{b['false_reject_count']}** | `{b['false_reject_cases']}` | 预期通过/证据不足/0分计分却被判为 `NON_COMPLIANT` |",
        f"| **合理弃判 (`justified_abstain`)** | **{b['justified_abstain_count']}** | `{b['justified_abstain_cases']}` | 缺失时效、单位不符、缺变更批复、缺公式扣减项或仅自述时正确返回 `INSUFFICIENT_EVIDENCE` |",
        f"| **过度弃判 (`unjustified_abstain`)** | **{b['unjustified_abstain_count']}** | `{b['unjustified_abstain_cases']}` | 证据完整明确时误判为 `INSUFFICIENT_EVIDENCE` |",
        f"| **评分错误 (`score_error`)** | **{b['score_error_count']}** | `{b['score_error_cases']}` | 评分条款结论或分值与预期不一致 |",
        "",
        "## 2. 按规则族 (`rule_family`) 统计",
        "",
        "| 规则族 | 用例总数 | 通过数 | 通过率 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for rfamily, stats in sorted(result["by_rule_family"].items()):
        rate = stats["passed"] / stats["total"] * 100 if stats["total"] else 0.0
        lines.append(f"| `{rfamily}` | {stats['total']} | {stats['passed']} | {rate:.2f}% |")

    lines.extend([
        "",
        "## 3. 按测试类型 (`case_type`) 统计",
        "",
        "| 用例类型 | 用例总数 | 通过数 | 通过率 |",
        "| --- | ---: | ---: | ---: |",
    ])
    for ctype, stats in sorted(result["by_case_type"].items()):
        rate = stats["passed"] / stats["total"] * 100 if stats["total"] else 0.0
        lines.append(f"| `{ctype}` | {stats['total']} | {stats['passed']} | {rate:.2f}% |")

    lines.extend([
        "",
        "## 4. 逐条用例执行轨迹明细",
        "",
        "| 用例ID | 条款 | 规则族 | 类型 | 预期结论(分值) | 实际结论(分值) | 状态 | 符号推理轨迹 (`symbolic_trace`) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for d in result["details"]:
        exp_s = f"{d['expected_verdict']}" + (f" ({d['expected_score']})" if d["expected_score"] is not None else "")
        pred_s = f"{d['predicted_verdict']}" + (f" ({d['predicted_score']})" if d["predicted_score"] is not None else "")
        status = "PASS" if d["case_passed"] else "FAIL"
        trace = str(d["symbolic_trace"]).replace("|", "\\|")
        lines.append(
            f"| `{d['case_id']}` | `{d['clause_id']}` | `{d['rule_family']}` | `{d['case_type']}` | `{exp_s}` | `{pred_s}` | **{status}** | `{trace}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path("SystemCode/data/rule_eval/s604_rule_validation_suite.json"),
        help="Path to standalone rule validation suite JSON.",
    )
    parser.add_argument(
        "--output_json",
        type=Path,
        default=Path("SystemCode/data/rule_eval/s604_rule_eval_report.json"),
        help="Path to write JSON evaluation report.",
    )
    parser.add_argument(
        "--output_md",
        type=Path,
        default=Path("docs/experiments/s604_rule_engine_validation.md"),
        help="Optional path to write Markdown evaluation report.",
    )
    args = parser.parse_args()

    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    result = evaluate_rule_suite(suite)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(format_markdown_report(result), encoding="utf-8")

    summary = {k: v for k, v in result.items() if k != "details"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["total_passed"] == result["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

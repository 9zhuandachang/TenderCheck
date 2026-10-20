"""
Standalone Deterministic Rule Engine Evaluation (`src/evaluation/rule_eval.py`).

Evaluates `ComplianceRuleEngine` on human-constructed, deterministic structured inputs
completely independent of RAG retrieval and LLM slot extraction.
Separates compliance verdicts from score values and explicitly itemizes:
  - `false_pass` (错误放行)
  - `false_reject` (错误拒绝)
  - `justified_abstain` (合理弃判)
  - `unjustified_abstain` (过度弃判)
  - `score_error` (评分错误)
"""

from collections import defaultdict
from typing import Any, Dict, List

from src.extraction.schema import StructuredExtractionResult
from src.rules.rule_engine import ComplianceRuleEngine


def evaluate_rule_suite(suite: Dict[str, Any]) -> Dict[str, Any]:
    if suite.get("schema_version") != 1 or suite.get("evaluation_scope") != "standalone_rule_engine_verification":
        raise ValueError("Invalid rule validation suite schema or scope")

    cases: List[Dict[str, Any]] = suite.get("cases") or []
    if not cases:
        raise ValueError("Rule validation suite must contain at least one test case")

    engine = ComplianceRuleEngine()
    case_ids = [c["case_id"] for c in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Duplicate case_id in rule validation suite")

    details = []
    false_pass_cases = []
    false_reject_cases = []
    justified_abstain_cases = []
    unjustified_abstain_cases = []
    score_error_cases = []

    by_rule_family = defaultdict(lambda: {"total": 0, "passed": 0})
    by_case_type = defaultdict(lambda: {"total": 0, "passed": 0})

    scoring_total = 0
    scoring_exact = 0
    compliance_total = 0
    compliance_exact = 0

    for case in cases:
        cid = case["case_id"]
        clause_id = case["clause_id"]
        rfamily = case["rule_family"]
        ctype = case["case_type"]
        exp_verdict = case["expected_verdict"]
        exp_score = case.get("expected_score")
        exp_rule = case.get("expected_rule_id")

        extracted = StructuredExtractionResult.from_dict(case["input"])
        report = engine.evaluate(extracted)

        pred_verdict = report.verdict
        pred_score = report.awarded_score
        pred_rule = report.triggered_rule_id

        verdict_ok = pred_verdict == exp_verdict
        rule_id_ok = (exp_rule is None) or (pred_rule == exp_rule)
        if exp_verdict == "SCORED":
            scoring_total += 1
            score_ok = (
                pred_verdict == "SCORED"
                and pred_score is not None
                and exp_score is not None
                and abs(float(pred_score) - float(exp_score)) < 1e-6
            )
            if score_ok:
                scoring_exact += 1
            else:
                score_error_cases.append(cid)
            case_passed = verdict_ok and score_ok and rule_id_ok
        else:
            compliance_total += 1
            score_ok = pred_score is None
            if verdict_ok:
                compliance_exact += 1
            case_passed = verdict_ok and score_ok and rule_id_ok

        # Diagnostic buckets required by Step 3
        if exp_verdict != "COMPLIANT" and pred_verdict == "COMPLIANT":
            false_pass_cases.append(cid)
        if exp_verdict != "NON_COMPLIANT" and pred_verdict == "NON_COMPLIANT":
            false_reject_cases.append(cid)
        if exp_verdict == "INSUFFICIENT_EVIDENCE" and pred_verdict == "INSUFFICIENT_EVIDENCE":
            justified_abstain_cases.append(cid)
        if exp_verdict != "INSUFFICIENT_EVIDENCE" and pred_verdict == "INSUFFICIENT_EVIDENCE":
            unjustified_abstain_cases.append(cid)

        by_rule_family[rfamily]["total"] += 1
        by_rule_family[rfamily]["passed"] += int(case_passed)
        by_case_type[ctype]["total"] += 1
        by_case_type[ctype]["passed"] += int(case_passed)

        details.append({
            "case_id": cid,
            "clause_id": clause_id,
            "rule_family": rfamily,
            "case_type": ctype,
            "description": case.get("description", ""),
            "expected_verdict": exp_verdict,
            "predicted_verdict": pred_verdict,
            "verdict_match": verdict_ok,
            "expected_score": exp_score,
            "predicted_score": pred_score,
            "score_match": score_ok,
            "expected_rule_id": exp_rule,
            "triggered_rule_id": pred_rule,
            "rule_id_match": rule_id_ok,
            "case_passed": case_passed,
            "symbolic_trace": report.symbolic_trace,
        })

    total_passed = sum(1 for d in details if d["case_passed"])
    return {
        "evaluation_scope": "standalone_rule_engine_verification",
        "project_id": suite.get("project_id", ""),
        "review_status": suite.get("review_status", ""),
        "total_cases": len(cases),
        "total_passed": total_passed,
        "overall_pass_rate": total_passed / len(cases),
        "compliance_cases_total": compliance_total,
        "compliance_verdict_exact_match": compliance_exact,
        "compliance_verdict_accuracy": compliance_exact / compliance_total if compliance_total else None,
        "scoring_cases_total": scoring_total,
        "scoring_exact_match": scoring_exact,
        "scoring_accuracy": scoring_exact / scoring_total if scoring_total else None,
        "diagnostic_buckets": {
            "false_pass_count": len(false_pass_cases),
            "false_pass_cases": false_pass_cases,
            "false_reject_count": len(false_reject_cases),
            "false_reject_cases": false_reject_cases,
            "justified_abstain_count": len(justified_abstain_cases),
            "justified_abstain_cases": justified_abstain_cases,
            "unjustified_abstain_count": len(unjustified_abstain_cases),
            "unjustified_abstain_cases": unjustified_abstain_cases,
            "score_error_count": len(score_error_cases),
            "score_error_cases": score_error_cases,
        },
        "by_rule_family": dict(by_rule_family),
        "by_case_type": dict(by_case_type),
        "details": details,
    }

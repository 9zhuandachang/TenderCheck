"""Standalone evaluator for the LLM slot extraction module (Step 5)."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.extraction.evidence import ground_extraction
from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules.rule_engine import (
    ComplianceRuleEngine,
    canonical_role,
    normalize_rmb_upper_text,
    parse_qualification_rank,
)

DEFAULT_BENCHMARK_PATH = (
    Path(__file__).resolve().parents[2]
    / "data/extraction_eval/s604_extraction_benchmark.json"
)

_TENDER_IGNORED_FIELDS = {"target_field", "source_quotes"}
_BID_IGNORED_FIELDS = {
    "evidence_summary",
    "evidence_references",
    "subject_bindings",
    "evidence_pages",
}


def load_extraction_benchmark(path: Optional[Path | str] = None) -> Dict[str, Any]:
    bench_path = Path(path) if path else DEFAULT_BENCHMARK_PATH
    return json.loads(bench_path.read_text(encoding="utf-8"))


def _is_empty(val: Any) -> bool:
    return val is None or val == "" or val == [] or val == {}


def _canon_str(s: str) -> str:
    return (
        s.strip()
        .rstrip("。；;，,")
        .replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
    )


def _values_match(gold_val: Any, pred_val: Any, field_name: str = "") -> bool:
    if isinstance(gold_val, (int, float)) and not isinstance(gold_val, bool):
        if not isinstance(pred_val, (int, float)) or isinstance(pred_val, bool):
            return False
        return math.isclose(float(gold_val), float(pred_val), rel_tol=1e-6, abs_tol=1e-4)
    if isinstance(gold_val, str) and isinstance(pred_val, str):
        if _canon_str(gold_val) == _canon_str(pred_val):
            return True
        if field_name in {"required_role", "subject_role", "role", "performed_role"}:
            return canonical_role(gold_val) == canonical_role(pred_val)
        if field_name == "upper_amount_text":
            return normalize_rmb_upper_text(gold_val) == normalize_rmb_upper_text(pred_val)
        if field_name in {
            "expected_text",
            "actual_text",
            "qualification_text",
            "required_highway_grade",
            "highway_grade",
        }:
            gf, gr = parse_qualification_rank(gold_val)
            pf, pr = parse_qualification_rank(pred_val)
            if gr != 0 and (gf, gr) == (pf, pr):
                return True
        return False
    if isinstance(gold_val, list) and isinstance(pred_val, list):
        if len(gold_val) != len(pred_val):
            return False
        return all(_values_match(g, p, field_name) for g, p in zip(gold_val, pred_val))
    if isinstance(gold_val, dict) and isinstance(pred_val, dict):
        # For structured dicts (e.g. personnel_records, performance_records, scoring_bands, component_amounts),
        # compare all non-empty gold keys.
        for k, gv in gold_val.items():
            if _is_empty(gv):
                continue
            if k not in pred_val or not _values_match(gv, pred_val[k], k):
                return False
        return True
    return gold_val == pred_val


def evaluate_clause_extraction(
    item: Dict[str, Any],
    prediction: StructuredExtractionResult,
    engine: Optional[ComplianceRuleEngine] = None,
) -> Dict[str, Any]:
    """Evaluate a single grounded StructuredExtractionResult against a benchmark item."""
    engine = engine or ComplianceRuleEngine()
    cid = item["clause_id"]
    ev_ctx_json = json.dumps(item["evidence_context"], ensure_ascii=False)
    grounded = ground_extraction(copy.deepcopy(prediction), ev_ctx_json)

    gold_t = item["gold_constraint_slots"]
    pred_t = grounded.tender_slots.to_dict()
    default_t = TenderConstraintSlots().to_dict()

    t_total = 0
    t_correct = 0
    t_mismatches: List[str] = []
    for field, g_val in gold_t.items():
        if field in _TENDER_IGNORED_FIELDS:
            continue
        p_val = pred_t.get(field)
        # Evaluate field if active in gold or non-default in prediction
        if g_val != default_t.get(field) or p_val != default_t.get(field):
            t_total += 1
            if _values_match(g_val, p_val, field):
                t_correct += 1
            else:
                t_mismatches.append(field)

    gold_b = item["gold_evidence_slots"]
    pred_b = grounded.bid_slots.to_dict()
    default_b = BidEvidenceSlots().to_dict()

    b_total = 0
    b_correct = 0
    b_mismatches: List[str] = []
    for field, g_val in gold_b.items():
        if field in _BID_IGNORED_FIELDS:
            continue
        p_val = pred_b.get(field)
        if g_val != default_b.get(field) or p_val != default_b.get(field):
            b_total += 1
            if _values_match(g_val, p_val, field):
                b_correct += 1
            else:
                b_mismatches.append(field)

    # Subject / role binding evaluation
    gold_bindings = gold_b.get("subject_bindings") or []
    pred_bindings = pred_b.get("subject_bindings") or []
    has_subject_check = bool(
        gold_bindings
        or gold_b.get("certificate_holder")
        or gold_b.get("proposed_person_name")
        or gold_b.get("personnel_records")
    )
    subject_binding_ok = True
    if has_subject_check:
        if gold_b.get("certificate_holder") and not _values_match(
            gold_b["certificate_holder"], pred_b.get("certificate_holder", "")
        ):
            subject_binding_ok = False
        if gold_b.get("proposed_person_name") and not _values_match(
            gold_b["proposed_person_name"], pred_b.get("proposed_person_name", "")
        ):
            subject_binding_ok = False
        if gold_bindings:
            gold_set = {
                (b["field"], b["subject_name"], canonical_role(b["role"]), b["evidence_id"], b["role_evidence_id"])
                for b in gold_bindings
            }
            pred_set = {
                (
                    b.get("field"),
                    b.get("subject_name"),
                    canonical_role(b.get("role", "")),
                    b.get("evidence_id"),
                    b.get("role_evidence_id"),
                )
                for b in pred_bindings
            }
            if not gold_set.issubset(pred_set):
                subject_binding_ok = False
        if gold_b.get("personnel_records"):
            gold_people = {(p["name"], canonical_role(p["role"])) for p in gold_b["personnel_records"]}
            pred_people = {
                (p.get("name"), canonical_role(p.get("role", ""))) for p in (pred_b.get("personnel_records") or [])
            }
            if gold_people != pred_people:
                subject_binding_ok = False

    # Citation support & evidence ID coverage
    raw_refs = prediction.bid_slots.evidence_references or []
    verified_refs = grounded.bid_slots.evidence_references or []
    gold_eids = {r["evidence_id"] for r in (gold_b.get("evidence_references") or [])}
    pred_eids = {r["evidence_id"] for r in verified_refs}
    citation_supported = (
        len(verified_refs) == len(raw_refs)
        and len(verified_refs) > 0
        and not grounded.extraction_issues
        and gold_eids.issubset(pred_eids)
    )

    # Abstention / anti-hallucination check on must_remain_unknown fields
    unknown_fields = item.get("must_remain_unknown") or []
    unknown_total = len(unknown_fields)
    unknown_abstained = 0
    hallucinated_fields: List[str] = []
    for u_field in unknown_fields:
        val = pred_b[u_field] if u_field in pred_b else pred_t.get(u_field)
        if _is_empty(val):
            unknown_abstained += 1
        else:
            hallucinated_fields.append(u_field)

    # Downstream rule engine verdict & score agreement
    report = engine.evaluate(grounded)
    verdict_match = report.verdict == item["expected_verdict"]
    score_match = report.awarded_score == item["expected_score"]

    return {
        "clause_id": cid,
        "split": item.get("split", "test"),
        "rule_family": item["rule_family"],
        "constraint_total": t_total,
        "constraint_correct": t_correct,
        "constraint_mismatches": t_mismatches,
        "evidence_total": b_total,
        "evidence_correct": b_correct,
        "evidence_mismatches": b_mismatches,
        "has_subject_check": has_subject_check,
        "subject_binding_ok": subject_binding_ok,
        "citation_supported": citation_supported,
        "extraction_issues": grounded.extraction_issues,
        "unknown_total": unknown_total,
        "unknown_abstained": unknown_abstained,
        "hallucinated_fields": hallucinated_fields,
        "predicted_verdict": report.verdict,
        "expected_verdict": item["expected_verdict"],
        "verdict_match": verdict_match,
        "predicted_score": report.awarded_score,
        "expected_score": item["expected_score"],
        "score_match": score_match,
    }


def evaluate_extraction_benchmark(
    benchmark: Dict[str, Any],
    predictor: Callable[[Dict[str, Any]], StructuredExtractionResult],
    split: str = "all",
) -> Dict[str, Any]:
    """Run predictor across benchmark clauses (filtered by split) and aggregate metrics."""
    engine = ComplianceRuleEngine()
    clauses = [
        item
        for item in benchmark["clauses"]
        if split == "all" or item.get("split") == split
    ]
    per_clause = [
        evaluate_clause_extraction(item, predictor(item), engine=engine)
        for item in clauses
    ]

    t_tot = sum(c["constraint_total"] for c in per_clause)
    t_cor = sum(c["constraint_correct"] for c in per_clause)
    b_tot = sum(c["evidence_total"] for c in per_clause)
    b_cor = sum(c["evidence_correct"] for c in per_clause)
    subj_cases = [c for c in per_clause if c["has_subject_check"]]
    unk_tot = sum(c["unknown_total"] for c in per_clause)
    unk_abs = sum(c["unknown_abstained"] for c in per_clause)
    n = len(per_clause) or 1

    abstention_rate = (unk_abs / unk_tot) if unk_tot else 1.0
    return {
        "benchmark_id": benchmark.get("benchmark_id", "s604_extraction_benchmark_v1"),
        "split": split,
        "evaluated_clauses": len(per_clause),
        "constraint_slot_accuracy": round(t_cor / t_tot, 4) if t_tot else 1.0,
        "evidence_slot_accuracy": round(b_cor / b_tot, 4) if b_tot else 1.0,
        "clause_exact_slot_accuracy": round(
            sum(
                1
                for c in per_clause
                if not c["constraint_mismatches"] and not c["evidence_mismatches"]
            )
            / n,
            4,
        ),
        "subject_binding_accuracy": round(
            sum(1 for c in subj_cases if c["subject_binding_ok"]) / len(subj_cases), 4
        )
        if subj_cases
        else 1.0,
        "citation_support_rate": round(
            sum(1 for c in per_clause if c["citation_supported"]) / n, 4
        ),
        "unknown_abstention_rate": round(abstention_rate, 4),
        "hallucination_rate": round(1.0 - abstention_rate, 4),
        "downstream_verdict_agreement": round(
            sum(1 for c in per_clause if c["verdict_match"]) / n, 4
        ),
        "downstream_score_agreement": round(
            sum(1 for c in per_clause if c["score_match"]) / n, 4
        ),
        "clause_reports": per_clause,
    }

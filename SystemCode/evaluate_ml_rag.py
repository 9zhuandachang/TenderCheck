"""
End-to-End ML + RAG Joint Evaluation & Ground-Truth Benchmark Suite for TenderCheck.

Evaluates:
1. Holdout ML Classification Accuracy & Macro-F1 on all tender clauses.
2. Ground-Truth Entity Ledger Slot Accuracy (comparing extracted `ledger.json`
   against verified Ground-Truth bid facts in `s604_rag_benchmark.json`).
3. Ground-Truth Retrieval IR Metrics (`Hit@1`, `Hit@3`, `Hit@5`, `MRR@5`) and
   a 4-Way Ablation Study:
   - Exp 1: Unrouted BM25 Baseline (Sparse Only, No Category Routing)
   - Exp 2: Unrouted Hybrid RRF (BM25 + Dense, No Category Routing)
   - Exp 3: ML-Routed Category Retriever (Chunk Index Only)
   - Exp 4: Ours Full (ML-Routed Dual-Index: Entity Ledger + Hybrid Chunks + Reranker)
4. Routing Consistency: Top-1 Match & Top-K Evidence Overlap between
   ML-predicted category routing and Gold-label category routing.

Usage on RTX 5080 (after running `run_rag_pipeline.py --mode ingest`):
    python SystemCode/evaluate_ml_rag.py `
      --holdout_json "SystemCode/data/holdout_eval/2022__11.11annotations.json" `
      --benchmark_json "SystemCode/data/holdout_eval/s604_rag_benchmark.json" `
      --index_dir "SystemCode/data/rag_index/s604_full" `
      --reranker_model "BAAI/bge-reranker-v2-m3"
"""

import argparse
import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR.parent) not in sys.path:
    # Older saved TF-IDF pipelines reference SystemCode.src.* custom transformers.
    sys.path.insert(0, str(BASE_DIR.parent))

from src.classification import load_classifier
from src.data import LABEL2ID, REQUIREMENT_TYPES, RequirementSample
from src.evaluation import evaluate_predictions
from src.rag import (
    BidEntityLedger,
    CategoryRouterRetriever,
    EntityLedgerBuilder,
    HybridBidIndex,
    RetrievedChunkHit,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("evaluate_ml_rag")


def load_holdout_samples(json_path: Path) -> List[RequirementSample]:
    """Load a holdout tender annotation JSON file into RequirementSample objects."""
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    samples: List[RequirementSample] = []
    project_id = json_path.stem
    for idx, item in enumerate(raw):
        label = str(item.get("Requirement Type") or item.get("type") or "").strip()
        if label not in LABEL2ID:
            continue
        item_id = str(item.get("ID") or item.get("id") or f"T{idx + 1:04d}").strip()
        section = str(item.get("Section") or item.get("section") or "").strip()
        requirement = str(item.get("Requirement") or item.get("text") or "").strip()
        page_raw = item.get("Page", 1)
        try:
            page = int(page_raw)
        except (ValueError, TypeError):
            page = 1
        samples.append(
            RequirementSample(
                sample_id=f"{project_id}::{item_id}",
                item_id=item_id,
                project_id=project_id,
                folder="holdout",
                page=page,
                section=section,
                requirement=requirement,
                label=label,
                label_id=LABEL2ID[label],
            )
        )
    return samples


def resolve_model_dir(models_dir: Path, preferred: Optional[Path] = None) -> Path:
    """Resolve the best available trained ML classifier checkpoint."""
    if preferred and preferred.exists():
        return preferred
    roberta_dir = models_dir / "roberta_wwm_ext"
    if (
        (roberta_dir / "model.safetensors").exists()
        or (roberta_dir / "model.safetensors.part0").exists()
        or (roberta_dir / "pytorch_model.bin").exists()
    ):
        return roberta_dir
    tfidf_dir = models_dir / "tfidf_svm"
    if (tfidf_dir / "tfidf_pipeline.joblib").exists():
        return tfidf_dir
    raise FileNotFoundError(
        f"No trained ML model found in {models_dir}. Expected roberta_wwm_ext or tfidf_svm."
    )


def summarize_ledger_completeness(ledger: BidEntityLedger) -> Dict[str, Any]:
    """Check whether key structured tables in the BidEntityLedger are populated."""
    cp = ledger.company_profile or {}
    cs = ledger.commercial_schedule_ledger or {}
    company_filled_fields = [
        k
        for k in (
            "company_name",
            "legal_representative",
            "credit_code",
            "qualifications",
            "safety_permit_valid",
        )
        if cp.get(k)
    ]
    commercial_filled_fields = [
        k
        for k in (
            "bid_price_yuan",
            "bid_price_upper",
            "duration_days",
            "validity_days",
            "quality_target",
        )
        if cs.get(k) is not None
    ]
    return {
        "company_name": cp.get("company_name") or "",
        "legal_representative": cp.get("legal_representative") or "",
        "credit_code": cp.get("credit_code") or "",
        "qualifications": cp.get("qualifications") or [],
        "bid_price_yuan": cs.get("bid_price_yuan"),
        "bid_price_upper": cs.get("bid_price_upper") or "",
        "duration_days": cs.get("duration_days") or "",
        "quality_target": cs.get("quality_target") or "",
        "company_filled_slots": len(company_filled_fields),
        "company_filled_field_names": company_filled_fields,
        "commercial_filled_slots": len(commercial_filled_fields),
        "commercial_filled_field_names": commercial_filled_fields,
        "personnel_records_count": len(ledger.personnel_ledger),
        "performance_records_count": len(ledger.performance_ledger),
        "seal_registry_categories_count": len(ledger.document_seal_registry),
        "field_mentions_count": len(ledger.field_mentions),
    }


def evaluate_gold_ledger_facts(
    ledger: BidEntityLedger,
    gold_facts: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare extracted BidEntityLedger slots against Ground-Truth Bid Facts."""
    cp = ledger.company_profile or {}
    cs = ledger.commercial_schedule_ledger or {}
    pm_names = [
        str(p.get("name", ""))
        for p in ledger.personnel_ledger
        if p.get("role") == "项目经理" and p.get("name")
    ]

    checks: List[Dict[str, Any]] = []
    for slot_key, gold_val in gold_facts.items():
        if slot_key == "company_name":
            ext_val = cp.get("company_name")
            matched = bool(ext_val and str(gold_val) in str(ext_val))
        elif slot_key == "legal_representative":
            ext_val = cp.get("legal_representative")
            matched = bool(ext_val and str(gold_val) == str(ext_val))
        elif slot_key == "credit_code":
            ext_val = cp.get("credit_code")
            matched = bool(ext_val and str(gold_val).upper() == str(ext_val).upper())
        elif slot_key == "required_qualification":
            quals = cp.get("qualifications") or []
            matched_q = [str(q) for q in quals if str(gold_val) in str(q)]
            ext_val = matched_q[0] if matched_q else ", ".join(quals[:3])
            matched = bool(matched_q)
        elif slot_key == "safety_permit_valid":
            ext_val = cp.get("safety_permit_valid")
            matched = bool(ext_val) is bool(gold_val)
        elif slot_key == "project_manager_name":
            matched_pm = [n for n in pm_names if str(gold_val) in n]
            ext_val = matched_pm[0] if matched_pm else (pm_names[0] if pm_names else None)
            matched = bool(matched_pm)
        elif slot_key == "bid_price_yuan":
            ext_val = cs.get("bid_price_yuan")
            try:
                matched = ext_val is not None and abs(float(ext_val) - float(gold_val)) < 1.0
            except (TypeError, ValueError):
                matched = False
        elif slot_key == "bid_price_upper":
            ext_val = cs.get("bid_price_upper")
            matched = bool(ext_val and str(gold_val) in str(ext_val))
        elif slot_key == "duration_days":
            ext_val = cs.get("duration_days")
            # VLM JSON may store numeric days while the reference includes a unit.
            # Do not equate months or working days with calendar days.
            def calendar_days(value: Any) -> Optional[float]:
                match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:日历天|天)?\s*", str(value))
                return float(match.group(1)) if match else None

            actual_days = calendar_days(ext_val)
            expected_days = calendar_days(gold_val)
            matched = (
                actual_days is not None
                and expected_days is not None
                and actual_days == expected_days
            )
        elif slot_key == "quality_target":
            ext_val = cs.get("quality_target")
            matched = bool(ext_val and str(gold_val) in str(ext_val))
        else:
            ext_val = cp.get(slot_key) or cs.get(slot_key)
            matched = str(ext_val) == str(gold_val)

        checks.append(
            {
                "slot": slot_key,
                "ground_truth": gold_val,
                "extracted": ext_val,
                "matched": matched,
            }
        )

    matched_count = sum(1 for c in checks if c["matched"])
    total_count = max(len(checks), 1)
    return {
        "matched_slots": matched_count,
        "total_slots": len(checks),
        "slot_accuracy": round(matched_count / total_count, 4),
        "details": checks,
    }


def _is_chunk_relevant(
    hit: RetrievedChunkHit,
    target_cats: List[str],
    target_kws: List[str],
) -> bool:
    """
    Determine whether a retrieved chunk is a Ground-Truth True Positive hit:
    Matches if any ground-truth keyword appears in the chunk/parent text or section path,
    OR if both the document category matches AND the section path matches key terms.
    """
    combined_text = (
        f"{hit.child_chunk.section_path}\n{hit.child_chunk.text}\n{hit.parent_chunk.text}"
    )
    kw_match = any(kw in combined_text for kw in target_kws)
    cat_match = (not target_cats) or (hit.child_chunk.doc_category in target_cats)
    if kw_match and cat_match:
        return True
    # Also count as relevant if at least 2 target keywords appear in the chunk text directly
    kw_count = sum(1 for kw in target_kws if kw in combined_text)
    return kw_count >= 2 or (kw_match and len(target_kws) == 1)


def _is_ledger_relevant(
    ledger_evidence: Dict[str, Any],
    target_kws: List[str],
) -> bool:
    """Check whether the structured ledger evidence returned by the router directly contains the ground-truth answer."""
    if not ledger_evidence:
        return False
    serialized = json.dumps(ledger_evidence, ensure_ascii=False)
    return any(kw in serialized for kw in target_kws)


def evaluate_gold_retrieval_ablation(
    gold_queries: List[Dict[str, Any]],
    clf: Any,
    router: CategoryRouterRetriever,
    index: HybridBidIndex,
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    Run the 4-Way Retrieval Ablation Study on Ground-Truth Clause-to-Evidence pairs:
    1. `bm25_unrouted`: Sparse BM25 only over all chunks (no category routing)
    2. `hybrid_unrouted`: BM25 + Dense RRF over all chunks (no category routing)
    3. `ml_routed_chunks`: ML Category Router + Hybrid Chunk Search
    4. `ours_full_dual_index`: ML Category Router + Dual-Index (Entity Ledger + Hybrid Chunks)
    """
    methods = (
        "bm25_unrouted",
        "hybrid_unrouted",
        "ml_routed_chunks",
        "ours_full_dual_index",
    )
    stats: Dict[str, Dict[str, float]] = {
        m: {"hit1": 0.0, "hit3": 0.0, "hit5": 0.0, "mrr5": 0.0} for m in methods
    }
    per_cat_ours: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"count": 0, "hit1": 0.0, "hit3": 0.0, "hit5": 0.0, "mrr5": 0.0}
    )
    query_details: List[Dict[str, Any]] = []

    for q in gold_queries:
        req_text = q["requirement"]
        sec_text = q.get("section", "")
        gold_cat = q["gold_category"]
        target_cats = q.get("target_doc_categories", [])
        target_kws = q.get("target_keywords", [])

        # Predict category with trained ML classifier
        pred_res = clf.predict_single(requirement_text=req_text, section_text=sec_text)
        pred_cat = str(pred_res["label"])

        # 1. Exp 1: Unrouted BM25 only (no dense, no reranker)
        hits_bm25 = index.search(
            query=req_text,
            top_k=top_k,
            dense_weight=0.0,
            sparse_weight=1.0,
            use_reranker=False,
        )
        # 2. Exp 2: Unrouted Hybrid RRF (BM25 + Dense, no category router, no reranker)
        hits_hyb = index.search(
            query=req_text,
            top_k=top_k,
            dense_weight=0.55,
            sparse_weight=0.45,
            use_reranker=False,
        )
        # 3 & 4. Exp 3 & 4: ML-Routed Category Retriever
        bundle = router.retrieve(
            requirement_text=req_text,
            section_text=sec_text,
            requirement_type=pred_cat,
            requirement_id=q["clause_id"],
            top_k=top_k,
        )
        hits_routed = bundle.chunk_hits

        method_hits: List[Tuple[str, List[RetrievedChunkHit], bool]] = [
            ("bm25_unrouted", hits_bm25, False),
            ("hybrid_unrouted", hits_hyb, False),
            ("ml_routed_chunks", hits_routed, False),
            (
                "ours_full_dual_index",
                hits_routed,
                _is_ledger_relevant(bundle.ledger_evidence, target_kws),
            ),
        ]

        query_result: Dict[str, Any] = {
            "clause_id": q["clause_id"],
            "requirement": req_text,
            "gold_category": gold_cat,
            "predicted_category": pred_cat,
            "target_doc_categories": target_cats,
            "target_keywords": target_kws,
            "methods": {},
        }
        for m_name, h_list, ledger_hit in method_hits:
            first_rank: Optional[int] = None
            for rank_idx, h in enumerate(h_list[:top_k], start=1):
                if _is_chunk_relevant(h, target_cats, target_kws):
                    first_rank = rank_idx
                    break
            if ledger_hit and (first_rank is None or first_rank > 1):
                first_rank = 1
            query_result["methods"][m_name] = {
                "first_relevant_rank": first_rank,
                "ledger_keyword_hit": ledger_hit,
                "hits": [h.to_dict() for h in h_list[:top_k]],
            }

            if first_rank is not None:
                if first_rank <= 1:
                    stats[m_name]["hit1"] += 1.0
                if first_rank <= 3:
                    stats[m_name]["hit3"] += 1.0
                if first_rank <= 5:
                    stats[m_name]["hit5"] += 1.0
                stats[m_name]["mrr5"] += 1.0 / float(first_rank)

            if m_name == "ours_full_dual_index":
                cat_entry = per_cat_ours[gold_cat]
                cat_entry["count"] += 1
                if first_rank is not None:
                    if first_rank <= 1:
                        cat_entry["hit1"] += 1.0
                    if first_rank <= 3:
                        cat_entry["hit3"] += 1.0
                    if first_rank <= 5:
                        cat_entry["hit5"] += 1.0
                    cat_entry["mrr5"] += 1.0 / float(first_rank)

        query_details.append(query_result)

    n_total = max(len(gold_queries), 1)
    ablation_summary = {}
    for m_name in methods:
        ablation_summary[m_name] = {
            "num_queries": len(gold_queries),
            "hit_at_1": round(stats[m_name]["hit1"] / n_total, 4),
            "hit_at_3": round(stats[m_name]["hit3"] / n_total, 4),
            "hit_at_5": round(stats[m_name]["hit5"] / n_total, 4),
            "mrr_at_5": round(stats[m_name]["mrr5"] / n_total, 4),
        }

    per_category_ir = {}
    for cat in REQUIREMENT_TYPES:
        c_entry = per_cat_ours.get(cat)
        if not c_entry or c_entry["count"] == 0:
            continue
        cnt = c_entry["count"]
        per_category_ir[cat] = {
            "queries": int(cnt),
            "hit_at_1": round(c_entry["hit1"] / cnt, 4),
            "hit_at_3": round(c_entry["hit3"] / cnt, 4),
            "hit_at_5": round(c_entry["hit5"] / cnt, 4),
            "mrr_at_5": round(c_entry["mrr5"] / cnt, 4),
        }

    return {
        "ablation_table": ablation_summary,
        "per_category_ir_metrics": per_category_ir,
        "query_details": query_details,
    }


def evaluate_joint_pipeline(
    samples: List[RequirementSample],
    clf: Any,
    router: CategoryRouterRetriever,
    ledger: BidEntityLedger,
    benchmark_data: Optional[Dict[str, Any]] = None,
    top_k: int = 5,
    max_eval_clauses: int = 0,
) -> Dict[str, Any]:
    """Run ML classification followed by Category-Routed RAG retrieval on holdout clauses."""
    pred_ids = clf.predict_ids(samples)
    true_ids = [s.label_id for s in samples]
    ml_metrics = evaluate_predictions(
        y_true=true_ids,
        y_pred=pred_ids,
        split_name="holdout",
        model_name=getattr(clf, "model_name", clf.__class__.__name__),
    )

    eval_indices = list(range(len(samples)))
    if max_eval_clauses > 0 and len(eval_indices) > max_eval_clauses:
        eval_indices = eval_indices[:max_eval_clauses]

    top1_agreements = 0
    topk_overlap_ratios: List[float] = []
    per_category_stats: Dict[str, Dict[str, Any]] = {
        cat: {
            "count": 0,
            "ml_correct": 0,
            "structured_hit": 0,
            "chunk_hit": 0,
            "top1_routing_match": 0,
            "mean_topk_overlap": 0.0,
            "mean_top1_score": 0.0,
        }
        for cat in REQUIREMENT_TYPES
    }
    case_studies: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    logger.info(
        "Running ML -> RAG joint retrieval evaluation on %d clauses (top_k=%d)...",
        len(eval_indices),
        top_k,
    )

    for idx in eval_indices:
        sample = samples[idx]
        gold_cat = sample.label
        pred_cat = REQUIREMENT_TYPES[pred_ids[idx]]

        pkg_pred = router.retrieve(
            requirement_text=sample.requirement,
            section_text=sample.section,
            requirement_type=pred_cat,
            requirement_id=sample.item_id,
            top_k=top_k,
        )

        if pred_cat == gold_cat:
            is_top1_match = True
            overlap_ratio = 1.0
        else:
            pkg_gold = router.retrieve(
                requirement_text=sample.requirement,
                section_text=sample.section,
                requirement_type=gold_cat,
                requirement_id=sample.item_id,
                top_k=top_k,
            )
            pred_chunk_ids = [h.child_chunk.chunk_id for h in pkg_pred.chunk_hits]
            gold_chunk_ids = [h.child_chunk.chunk_id for h in pkg_gold.chunk_hits]
            is_top1_match = (
                bool(pred_chunk_ids and gold_chunk_ids)
                and pred_chunk_ids[0] == gold_chunk_ids[0]
            )
            if gold_chunk_ids:
                overlap_ratio = len(set(pred_chunk_ids) & set(gold_chunk_ids)) / float(
                    len(gold_chunk_ids)
                )
            else:
                overlap_ratio = 1.0

        top1_agreements += int(is_top1_match)
        topk_overlap_ratios.append(overlap_ratio)

        cat_stat = per_category_stats[gold_cat]
        cat_stat["count"] += 1
        cat_stat["ml_correct"] += int(pred_cat == gold_cat)
        has_struct = bool(pkg_pred.ledger_evidence)
        has_chunks = bool(pkg_pred.chunk_hits)
        cat_stat["structured_hit"] += int(has_struct)
        cat_stat["chunk_hit"] += int(has_chunks)
        cat_stat["top1_routing_match"] += int(is_top1_match)
        cat_stat["mean_topk_overlap"] += overlap_ratio
        top1_score = float(pkg_pred.chunk_hits[0].score) if has_chunks else 0.0
        cat_stat["mean_top1_score"] += top1_score

        if len(case_studies[gold_cat]) < 2 and len(sample.requirement) >= 15:
            top_hit = pkg_pred.chunk_hits[0] if has_chunks else None
            case_studies[gold_cat].append(
                {
                    "clause_id": sample.item_id,
                    "section": sample.section,
                    "clause_text": sample.requirement[:160],
                    "gold_category": gold_cat,
                    "ml_predicted_category": pred_cat,
                    "routing_strategy": pkg_pred.routing_strategy,
                    "ledger_keys": list(pkg_pred.ledger_evidence.keys()),
                    "top1_chunk_id": top_hit.child_chunk.chunk_id if top_hit else "",
                    "top1_page": f"p.{top_hit.child_chunk.page_start}" if top_hit else "N/A",
                    "top1_section": top_hit.child_chunk.section_path if top_hit else "",
                    "top1_score": round(top1_score, 4),
                    "top1_snippet": (
                        str(top_hit.child_chunk.text)[:180].replace("\n", " ")
                        if top_hit
                        else ""
                    ),
                }
            )

    for cat, stat in per_category_stats.items():
        n = stat["count"]
        if n > 0:
            stat["ml_accuracy"] = round(stat["ml_correct"] / n, 4)
            stat["structured_hit_rate"] = round(stat["structured_hit"] / n, 4)
            stat["chunk_hit_rate"] = round(stat["chunk_hit"] / n, 4)
            stat["top1_routing_agreement"] = round(stat["top1_routing_match"] / n, 4)
            stat["mean_topk_overlap"] = round(stat["mean_topk_overlap"] / n, 4)
            stat["mean_top1_score"] = round(stat["mean_top1_score"] / n, 4)

    total_eval = max(len(eval_indices), 1)
    result: Dict[str, Any] = {
        "ml_classification_summary": {
            "num_clauses": ml_metrics["num_samples"],
            "accuracy": round(ml_metrics["accuracy"], 4),
            "macro_f1": round(ml_metrics["macro_f1"], 4),
            "weighted_f1": round(ml_metrics["weighted_f1"], 4),
        },
        "ledger_completeness": summarize_ledger_completeness(ledger),
        "ml_rag_routing_consistency": {
            "evaluated_clauses": len(eval_indices),
            "top1_evidence_agreement_rate": round(top1_agreements / total_eval, 4),
            "mean_topk_evidence_overlap": round(
                sum(topk_overlap_ratios) / total_eval, 4
            ),
        },
        "per_category_rag_performance": per_category_stats,
        "case_studies": dict(case_studies),
    }

    if benchmark_data:
        if "gold_ledger_facts" in benchmark_data:
            result["gold_ledger_evaluation"] = evaluate_gold_ledger_facts(
                ledger, benchmark_data["gold_ledger_facts"]
            )
        if "gold_retrieval_queries" in benchmark_data:
            result["gold_retrieval_benchmark"] = evaluate_gold_retrieval_ablation(
                gold_queries=benchmark_data["gold_retrieval_queries"],
                clf=clf,
                router=router,
                index=router.index,
                top_k=top_k,
            )

    return result


def format_markdown_report(report: Dict[str, Any], project_name: str) -> str:
    """Format the joint ML + RAG evaluation into a readable Markdown report."""
    ml = report["ml_classification_summary"]
    led = report["ledger_completeness"]
    rc = report["ml_rag_routing_consistency"]

    lines = [
        f"# TenderCheck ML + RAG Joint Evaluation Report (`{project_name}`)",
        "",
        "## 1. End-to-End Summary Metrics",
        f"- **Holdout Tender Clauses Evaluated**: `{ml['num_clauses']}`",
        f"- **ML Classifier Holdout Accuracy**: **`{ml['accuracy'] * 100:.2f}%`** (Macro-F1: `{ml['macro_f1'] * 100:.2f}%`)",
        f"- **ML vs. Gold Routing Top-1 Evidence Agreement**: **`{rc['top1_evidence_agreement_rate'] * 100:.2f}%`**",
        f"- **ML vs. Gold Routing Top-K Evidence Overlap**: **`{rc['mean_topk_evidence_overlap'] * 100:.2f}%`**",
    ]

    if "gold_ledger_evaluation" in report:
        gle = report["gold_ledger_evaluation"]
        lines.extend(
            [
                f"- **Ground-Truth Entity Ledger Slot Accuracy**: **`{gle['slot_accuracy'] * 100:.1f}%`** (`{gle['matched_slots']} / {gle['total_slots']}` verified bid facts matched)",
                "",
                "## 2. Ground-Truth Entity Ledger Slot Verification",
                "| Bid Entity Slot | Ground-Truth Bid Value | Extracted Ledger Value | Match |",
                "| :--- | :--- | :--- | :---: |",
            ]
        )
        for item in gle["details"]:
            mark = "PASS" if item["matched"] else "MISS"
            lines.append(
                f"| `{item['slot']}` | `{item['ground_truth']}` | `{item['extracted']}` | **{mark}** |"
            )
        lines.append("")

    if "gold_retrieval_benchmark" in report:
        grb = report["gold_retrieval_benchmark"]
        abl = grb["ablation_table"]
        lines.extend(
            [
                "## 3. Ground-Truth RAG Retrieval Benchmark & Ablation Study (50 Verified Clauses)",
                "| Retrieval Architecture | Hit@1 (Recall@1) | Hit@3 (Recall@3) | Hit@5 (Recall@5) | MRR@5 |",
                "| :--- | ---: | ---: | ---: | ---: |",
                f"| **Exp 1: Unrouted BM25 Baseline** (No Category Router) | {abl['bm25_unrouted']['hit_at_1']*100:.1f}% | {abl['bm25_unrouted']['hit_at_3']*100:.1f}% | {abl['bm25_unrouted']['hit_at_5']*100:.1f}% | {abl['bm25_unrouted']['mrr_at_5']:.4f} |",
                f"| **Exp 2: Unrouted Hybrid RRF** (BM25 + Dense, No Router) | {abl['hybrid_unrouted']['hit_at_1']*100:.1f}% | {abl['hybrid_unrouted']['hit_at_3']*100:.1f}% | {abl['hybrid_unrouted']['hit_at_5']*100:.1f}% | {abl['hybrid_unrouted']['mrr_at_5']:.4f} |",
                f"| **Exp 3: ML-Routed Category Retriever** (Chunk Index Only) | {abl['ml_routed_chunks']['hit_at_1']*100:.1f}% | {abl['ml_routed_chunks']['hit_at_3']*100:.1f}% | {abl['ml_routed_chunks']['hit_at_5']*100:.1f}% | {abl['ml_routed_chunks']['mrr_at_5']:.4f} |",
                f"| **Exp 4: Ours Full Dual-Index** (ML Router + Ledger + Chunks) | **{abl['ours_full_dual_index']['hit_at_1']*100:.1f}%** | **{abl['ours_full_dual_index']['hit_at_3']*100:.1f}%** | **{abl['ours_full_dual_index']['hit_at_5']*100:.1f}%** | **{abl['ours_full_dual_index']['mrr_at_5']:.4f}** |",
                "",
                "### Per-Category Ground-Truth Retrieval Metrics (`Ours Full Dual-Index`)",
                "| Category | Gold Queries | Hit@1 | Hit@3 | Hit@5 | MRR@5 |",
                "| :--- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for cat in REQUIREMENT_TYPES:
            c_ir = grb["per_category_ir_metrics"].get(cat)
            if not c_ir:
                continue
            lines.append(
                f"| `{cat}` | {c_ir['queries']} | {c_ir['hit_at_1']*100:.1f}% | "
                f"{c_ir['hit_at_3']*100:.1f}% | {c_ir['hit_at_5']*100:.1f}% | {c_ir['mrr_at_5']:.4f} |"
            )
        lines.append("")

    lines.extend(
        [
            "## 4. Structured Bid Entity Ledger Summary",
            f"- **Bidder Company Name**: `{led['company_name'] or '(Missing)'}`",
            f"- **Legal Representative**: `{led['legal_representative'] or '(Missing)'}`",
            f"- **Unified Social Credit Code**: `{led['credit_code'] or '(Missing)'}`",
            f"- **Extracted Qualifications**: `{', '.join(led['qualifications']) or 'N/A'}`",
            f"- **Bid Total Price**: `{led['bid_price_yuan']}` (`{led['bid_price_upper']}`)",
            f"- **Duration & Quality Target**: `{led['duration_days']}` | `{led['quality_target']}`",
            f"- **Personnel Certificates (`personnel_ledger`)**: `{led['personnel_records_count']}` records",
            f"- **Historical Projects (`performance_ledger`)**: `{led['performance_records_count']}` records",
            f"- **Seal & Signature Registry (`document_seal_registry`)**: `{led['seal_registry_categories_count']}` document categories",
            f"- **Cross-Page Tracked Entity Mentions (`field_mentions`)**: `{led['field_mentions_count']}` mentions",
            "",
            "## 5. Full Holdout Set (359 Clauses) Category Routing Consistency",
            "| Category | Clauses | ML Acc | ML vs Gold Top-K Overlap | Mean Top-1 Score |",
            "| :--- | ---: | ---: | ---: | ---: |",
        ]
    )

    for cat in REQUIREMENT_TYPES:
        st = report["per_category_rag_performance"].get(cat, {})
        cnt = st.get("count", 0)
        if cnt == 0:
            continue
        lines.append(
            f"| `{cat}` | {cnt} | {st.get('ml_accuracy', 0)*100:.1f}% | "
            f"{st.get('mean_topk_overlap', 0)*100:.1f}% | "
            f"{st.get('mean_top1_score', 0):.4f} |"
        )

    lines.extend(["", "## 6. Representative Case Studies (ML -> RAG Chain)"])
    for cat in REQUIREMENT_TYPES:
        cases = report["case_studies"].get(cat, [])
        if not cases:
            continue
        lines.append(f"### Category: `{cat}`")
        for c in cases:
            lines.append(
                f"- **Clause `{c['clause_id']}`** ({c['section']}): {c['clause_text']}"
            )
            lines.append(
                f"  - **ML Prediction**: `{c['ml_predicted_category']}` (Gold: `{c['gold_category']}`) | **Strategy**: `{c['routing_strategy']}`"
            )
            lines.append(
                f"  - **Top-1 Evidence**: `{c['top1_chunk_id']}` ({c['top1_page']}, score=`{c['top1_score']}`) — _{c['top1_snippet']}_"
            )
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate ML Requirement Classifier + RAG Pipeline End-to-End"
    )
    parser.add_argument(
        "--holdout_json",
        type=Path,
        default=BASE_DIR / "data" / "holdout_eval" / "2022__11.11annotations.json",
        help="Path to holdout tender annotations JSON file.",
    )
    parser.add_argument(
        "--benchmark_json",
        type=Path,
        default=BASE_DIR / "data" / "holdout_eval" / "s604_rag_benchmark.json",
        help="Path to Ground-Truth RAG Evaluation Benchmark JSON file.",
    )
    parser.add_argument(
        "--index_dir",
        type=Path,
        required=True,
        help="Path to the ingested RAG index directory (e.g., SystemCode/data/rag_index/s604_full).",
    )
    parser.add_argument(
        "--model_dir",
        type=Path,
        default=None,
        help="Optional path to trained ML classifier (auto-detects roberta_wwm_ext or tfidf_svm if omitted).",
    )
    parser.add_argument(
        "--reranker_model",
        type=str,
        default="BAAI/bge-reranker-v2-m3",
        help="Cross-Encoder reranker model (default: BAAI/bge-reranker-v2-m3).",
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="Top-K chunks to retrieve per clause (default: 5).",
    )
    parser.add_argument(
        "--max_eval_clauses",
        type=int,
        default=0,
        help="Maximum clauses to run retrieval on (0 = all clauses in holdout file).",
    )
    parser.add_argument(
        "--output_prefix",
        type=str,
        default=None,
        help="Output filename prefix in SystemCode/reports/ (default: derived from index_dir).",
    )
    args = parser.parse_args()

    samples = load_holdout_samples(args.holdout_json)
    model_path = resolve_model_dir(BASE_DIR / "models", args.model_dir)
    logger.info("Loaded %d holdout clauses from %s", len(samples), args.holdout_json.name)
    logger.info("Loading ML classifier from %s", model_path)
    clf = load_classifier(model_path)

    ledger_path = args.index_dir / "ledger.json"
    meta_path = args.index_dir / "index_meta.json"
    if not ledger_path.exists() or not meta_path.exists():
        raise FileNotFoundError(
            f"RAG index incomplete at {args.index_dir}. Run `run_rag_pipeline.py --mode ingest` first."
        )

    benchmark_data = None
    if args.benchmark_json and args.benchmark_json.exists() and "11.11" in args.holdout_json.name:
        benchmark_data = json.loads(args.benchmark_json.read_text(encoding="utf-8"))
        logger.info(
            "Loaded Ground-Truth RAG Benchmark (%d queries, %d ledger slots) from %s",
            len(benchmark_data.get("gold_retrieval_queries", [])),
            len(benchmark_data.get("gold_ledger_facts", {})),
            args.benchmark_json.name,
        )

    ledger = EntityLedgerBuilder.load_ledger(ledger_path)
    hybrid_index = HybridBidIndex.load(
        args.index_dir,
        reranker_model_name=args.reranker_model,
    )
    router = CategoryRouterRetriever(index=hybrid_index, ledger=ledger)

    report = evaluate_joint_pipeline(
        samples=samples,
        clf=clf,
        router=router,
        ledger=ledger,
        benchmark_data=benchmark_data,
        top_k=args.top_k,
        max_eval_clauses=args.max_eval_clauses,
    )
    report["execution_config"] = {
        "classifier_model_dir": str(model_path),
        "embedding_model": hybrid_index.embedding_model_name,
        "reranker_model": args.reranker_model,
        "top_k": args.top_k,
        "benchmark_json": str(args.benchmark_json),
        "relevance_criterion": "Keyword/category proxy; ledger keyword matches are assigned rank 1.",
    }

    reports_dir = BASE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_prefix or f"ml_rag_joint_{args.index_dir.name}"
    json_out = reports_dir / f"{prefix}.json"
    md_out = reports_dir / f"{prefix}.md"

    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_text = format_markdown_report(report, project_name=args.index_dir.name)
    md_out.write_text(md_text, encoding="utf-8")

    print("\n" + md_text)
    logger.info("Saved joint ML+RAG evaluation report to %s and %s", json_out, md_out)


if __name__ == "__main__":
    main()

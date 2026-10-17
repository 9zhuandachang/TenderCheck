"""
End-to-End ML + RAG Joint Evaluation Pipeline for TenderCheck.

Evaluates how the trained 8-Class Requirement Classifier (RoBERTa / TF-IDF+SVM)
and the Dual-Index Category-Routed RAG Pipeline work together on holdout tender
and bid projects.

Metrics computed:
1. Holdout ML Classification Accuracy & Macro-F1 on the tender clauses.
2. Bid Entity Ledger Completeness (Company Profile, Personnel, Performance,
   Commercial/Schedule, Seal Registry, Cross-page Field Mentions).
3. Routing Consistency: Top-1 Match & Top-K Evidence Overlap between
   ML-predicted category routing and Gold-label category routing.
4. Category-wise Evidence Hit Rate (Structured Ledger Hit % + Chunk Hit %)
   and representative Case Study Audit Table across all 8 categories.

Usage on RTX 5080 (after running `run_rag_pipeline.py --mode ingest`):
    python SystemCode/evaluate_ml_rag.py `
      --holdout_json "SystemCode/data/holdout_eval/2022__11.11annotations.json" `
      --index_dir "SystemCode/data/rag_index/s604_full" `
      --reranker_model "BAAI/bge-reranker-v2-m3"
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.classification import load_classifier
from src.data import LABEL2ID, REQUIREMENT_TYPES, RequirementSample
from src.evaluation import evaluate_predictions
from src.rag import (
    BidEntityLedger,
    CategoryRouterRetriever,
    EntityLedgerBuilder,
    HybridBidIndex,
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
    if (roberta_dir / "model.safetensors").exists() or (roberta_dir / "pytorch_model.bin").exists():
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
        for k in ("company_name", "credit_code", "qualifications", "safety_permit_valid")
        if cp.get(k)
    ]
    commercial_filled_fields = [
        k
        for k in ("bid_price_yuan", "bid_price_upper", "duration_days", "validity_days", "quality_target")
        if cs.get(k) is not None
    ]
    return {
        "company_name": cp.get("company_name") or "",
        "qualifications": cp.get("qualifications") or [],
        "company_filled_slots": len(company_filled_fields),
        "company_filled_field_names": company_filled_fields,
        "commercial_filled_slots": len(commercial_filled_fields),
        "commercial_filled_field_names": commercial_filled_fields,
        "personnel_records_count": len(ledger.personnel_ledger),
        "performance_records_count": len(ledger.performance_ledger),
        "seal_registry_categories_count": len(ledger.document_seal_registry),
        "field_mentions_count": len(ledger.field_mentions),
    }


def evaluate_joint_pipeline(
    samples: List[RequirementSample],
    clf: Any,
    router: CategoryRouterRetriever,
    ledger: BidEntityLedger,
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
    return {
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
        "",
        "## 2. Structured Bid Entity Ledger Completeness",
        f"- **Bidder Company Name**: `{led['company_name'] or '(Run with --vision_backend qwen_vl for scanned certificates)'}`",
        f"- **Extracted Qualifications**: `{', '.join(led['qualifications']) or 'N/A'}`",
        f"- **Company Profile Filled Slots**: `{led['company_filled_slots']} / 4` ({', '.join(led['company_filled_field_names'])})",
        f"- **Commercial/Schedule Filled Slots**: `{led['commercial_filled_slots']} / 5` ({', '.join(led['commercial_filled_field_names'])})",
        f"- **Personnel Certificates (`personnel_ledger`)**: `{led['personnel_records_count']}` records",
        f"- **Historical Projects (`performance_ledger`)**: `{led['performance_records_count']}` records",
        f"- **Seal & Signature Registry (`document_seal_registry`)**: `{led['seal_registry_categories_count']}` document categories",
        f"- **Cross-Page Tracked Entity Mentions (`field_mentions`)**: `{led['field_mentions_count']}` mentions",
        "",
        "## 3. Category-Wise ML + RAG Performance Breakdown",
        "| Category | Clauses | ML Acc | Structured Ledger Hit % | Chunk Evidence Hit % | ML vs Gold Top-K Overlap | Mean Top-1 Score |",
        "| :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for cat in REQUIREMENT_TYPES:
        st = report["per_category_rag_performance"].get(cat, {})
        cnt = st.get("count", 0)
        if cnt == 0:
            continue
        lines.append(
            f"| `{cat}` | {cnt} | {st.get('ml_accuracy', 0)*100:.1f}% | "
            f"{st.get('structured_hit_rate', 0)*100:.1f}% | "
            f"{st.get('chunk_hit_rate', 0)*100:.1f}% | "
            f"{st.get('mean_topk_overlap', 0)*100:.1f}% | "
            f"{st.get('mean_top1_score', 0):.4f} |"
        )

    lines.extend(["", "## 4. Representative Case Studies (ML -> RAG Chain)"])
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
        default=None,
        help="Optional Cross-Encoder reranker model (e.g., BAAI/bge-reranker-v2-m3).",
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
        top_k=args.top_k,
        max_eval_clauses=args.max_eval_clauses,
    )

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

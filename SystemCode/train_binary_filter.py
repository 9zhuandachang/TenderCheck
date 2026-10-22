"""
Build Positive/Negative dataset, Train & Evaluate the Binary Tender Requirement Filter.

Workflow:
1. Matches historical annotated projects in `SystemCode/data/annotation` with their
   corresponding raw tender PDFs in `data_extracted/`, strictly separating the
   two holdout projects (`2022/11.11` S604 and `2024/2024.1.11` Guangde) into a
   zero-leakage Holdout Test Set.
2. Segments each tender PDF using `segment_tender_pdf()` and aligns candidates
   against human annotations using `align_candidates_with_annotations()`.
3. Performs project-grouped splitting (`Train` / `Val` / `Test` + `Holdout_S604_2024_1_11`)
   so no sentences from the same tender document ever cross splits.
4. Trains `BinaryRequirementFilter`, saves the checkpoint to `SystemCode/models/binary_filter/`,
   and outputs detailed evaluation metrics (including full-book S604 holdout evaluation
   and Gold Clause Recall) to `SystemCode/reports/binary_filter_evaluation.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pymupdf
from sklearn.model_selection import GroupShuffleSplit

from src.classification.binary_filter import (
    BinaryCandidateSample,
    BinaryRequirementFilter,
    align_candidates_with_annotations,
    compute_clause_overlap,
)
from src.extraction.tender_segmenter import CandidateClause, segment_tender_pdf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("train_binary_filter")

BASE_DIR = Path(__file__).resolve().parent
DATA_EXTRACTED_DIR = BASE_DIR.parent / "data_extracted"
ANNOTATIONS_DIR = BASE_DIR / "data" / "annotation"

FOLDER_PAIRS: List[Tuple[str, str]] = [
    ("2022年投标项目", "2022"),
    ("2023年投标项目", "2023"),
    ("2024年投标项目", "2024"),
    ("2025年投标项目", "2025"),
    ("202601", "2026_01"),
    ("202602", "2026_02"),
    ("202603", "2026-03"),
    ("202604", "2026_04"),
]

# Strictly reserved holdout annotation files (never used in train or val)
HOLDOUT_FILES = {
    "11.11annotations.json",
    "2024.1.11-annotations.json",
}


def discover_aligned_projects(
    max_projects: int = 40,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Discover content-verified (annotation_json, tender_pdf) project pairs,
    returning `(train_val_test_projects, holdout_projects)`.
    """
    non_holdout: List[Dict] = []
    holdout: List[Dict] = []

    for y_dir_name, ann_sub in FOLDER_PAIRS:
        y_dir = DATA_EXTRACTED_DIR / y_dir_name
        a_dir = ANNOTATIONS_DIR / ann_sub
        if not y_dir.exists() or not a_dir.exists():
            continue

        projs = sorted([p for p in y_dir.iterdir() if p.is_dir()])
        anns = sorted([f for f in a_dir.glob("*.json")])

        # Pre-index candidate tender PDFs in this year folder
        proj_pdfs: List[Tuple[Path, Path, int]] = []
        for p in projs:
            t_dir = p / "01_招标文件"
            pdfs = list(t_dir.rglob("*.pdf")) if t_dir.exists() else list(p.rglob("*.pdf"))
            cands = [
                x for x in pdfs
                if ("正文" in x.name or "招标文件" in x.name)
                and "评审" not in x.name
                and "清单" not in x.name
            ]
            if not cands:
                cands = [x for x in pdfs if "清单" not in x.name and "公告" not in x.name] or pdfs
            for mpdf in cands:
                try:
                    with pymupdf.open(str(mpdf)) as doc:
                        if len(doc) >= 30:
                            proj_pdfs.append((p, mpdf, len(doc)))
                except Exception:
                    pass

        for a_file in anns:
            try:
                adata = json.loads(a_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(adata, list) or not adata:
                continue

            step = max(1, len(adata) // 20)
            sample_clauses = [
                x for x in adata[::step]
                if "答疑" not in str(x.get("Section", ""))
                and "澄清" not in str(x.get("Section", ""))
            ][:20]

            best_entry = None
            best_ratio = 0.0
            for p_folder, mpdf, doc_len in proj_pdfs:
                hits = 0
                with pymupdf.open(str(mpdf)) as doc:
                    for item in sample_clauses:
                        try:
                            pno = int(item.get("Page", 1) or 1)
                        except (ValueError, TypeError):
                            pno = 1
                        if 1 <= pno <= doc_len:
                            ptxt = doc[pno - 1].get_text("text")
                            rc = set(re.findall(r"[\u4e00-\u9fff0-9]", str(item.get("Requirement", ""))))
                            pc = set(re.findall(r"[\u4e00-\u9fff0-9]", ptxt))
                            if len(rc & pc) / max(len(rc), 1) >= 0.65:
                                hits += 1
                ratio = hits / max(len(sample_clauses), 1)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_entry = (p_folder, mpdf, doc_len)

            if best_entry and best_ratio >= 0.80:
                info = {
                    "project_id": f"{ann_sub}/{a_file.stem}",
                    "ann_file": a_file,
                    "pdf_file": best_entry[1],
                    "proj_folder": best_entry[0].name,
                    "pages": best_entry[2],
                    "num_clauses": len(adata),
                    "align_confidence": round(best_ratio, 3),
                }
                if a_file.name in HOLDOUT_FILES:
                    holdout.append(info)
                else:
                    non_holdout.append(info)

    # Select up to `max_projects` diverse non-holdout projects across all years
    if len(non_holdout) > max_projects:
        step = len(non_holdout) / max_projects
        selected = [non_holdout[int(i * step)] for i in range(max_projects)]
    else:
        selected = non_holdout

    return selected, holdout


def evaluate_end_to_end_gold_recall(
    model: BinaryRequirementFilter,
    candidates: List[CandidateClause],
    annotations: List[Dict],
    threshold: float,
    chapter_aware: bool = False,
) -> Dict:
    """
    Evaluate how many of the human-annotated gold clauses in a tender document
    are covered by the candidates retained after BinaryRequirementFilter (`pred == 1`).
    """
    kept_pairs = model.filter_candidates(candidates, threshold=threshold, chapter_aware=chapter_aware)
    kept_cands = [c for c, _ in kept_pairs]

    by_page_all: Dict[int, List[CandidateClause]] = {}
    for c in candidates:
        by_page_all.setdefault(c.page, []).append(c)

    by_page_kept: Dict[int, List[CandidateClause]] = {}
    for c in kept_cands:
        by_page_kept.setdefault(c.page, []).append(c)

    main_anns = [
        a for a in annotations
        if "答疑" not in str(a.get("Section", ""))
        and "澄清" not in str(a.get("Section", ""))
        and "补遗" not in str(a.get("Section", ""))
    ]

    seg_covered = 0
    filter_covered = 0

    for a in main_anns:
        try:
            pno = int(a.get("Page", 1) or 1)
        except (ValueError, TypeError):
            pno = 1
        gold_req = str(a.get("Requirement", "")).strip()
        if not gold_req:
            continue

        # Check coverage before binary filtering (segmenter upper bound)
        pool_all = by_page_all.get(pno, []) + by_page_all.get(pno - 1, []) + by_page_all.get(pno + 1, [])
        best_all = max(
            (
                max(
                    compute_clause_overlap(c.text, gold_req)[0],
                    compute_clause_overlap(c.raw_text, gold_req)[0],
                )
                for c in pool_all
            ),
            default=0.0,
        )
        if best_all >= 0.45:
            seg_covered += 1

        # Check coverage after binary filtering
        pool_kept = by_page_kept.get(pno, []) + by_page_kept.get(pno - 1, []) + by_page_kept.get(pno + 1, [])
        best_kept = max(
            (
                max(
                    compute_clause_overlap(c.text, gold_req)[0],
                    compute_clause_overlap(c.raw_text, gold_req)[0],
                )
                for c in pool_kept
            ),
            default=0.0,
        )
        if best_kept >= 0.45:
            filter_covered += 1

    noise_reduction = 1.0 - (len(kept_cands) / max(len(candidates), 1))
    return {
        "total_raw_candidates": len(candidates),
        "retained_candidates": len(kept_cands),
        "filtered_out_noise_candidates": len(candidates) - len(kept_cands),
        "noise_reduction_rate": round(noise_reduction, 4),
        "total_gold_clauses": len(main_anns),
        "segmenter_gold_covered": seg_covered,
        "segmenter_gold_recall": round(seg_covered / max(len(main_anns), 1), 4),
        "post_filter_gold_covered": filter_covered,
        "post_filter_gold_recall": round(filter_covered / max(len(main_anns), 1), 4),
        "gold_retention_ratio": round(filter_covered / max(seg_covered, 1), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and evaluate Binary Requirement Filter")
    parser.add_argument("--max_projects", type=int, default=30, help="Number of non-holdout projects to build dataset from")
    parser.add_argument("--model_dir", type=Path, default=BASE_DIR / "models" / "binary_filter")
    parser.add_argument("--report_path", type=Path, default=BASE_DIR / "reports" / "binary_filter_evaluation.json")
    args = parser.parse_args()

    logger.info("Step 1: Discovering aligned non-holdout and holdout tender PDFs...")
    non_holdout_projs, holdout_projs = discover_aligned_projects(max_projects=args.max_projects)
    logger.info(
        "Found %d non-holdout projects and %d holdout projects (%s)",
        len(non_holdout_projs),
        len(holdout_projs),
        [h["project_id"] for h in holdout_projs],
    )

    all_samples: List[BinaryCandidateSample] = []
    project_stats: List[Dict] = []

    logger.info("Step 2: Segmenting tender PDFs and constructing positive/negative samples...")
    for idx, pinfo in enumerate(non_holdout_projs, 1):
        pid = pinfo["project_id"]
        cands = segment_tender_pdf(pinfo["pdf_file"])
        anns = json.loads(pinfo["ann_file"].read_text(encoding="utf-8"))
        samples, st = align_candidates_with_annotations(cands, anns, project_id=pid, keep_ambiguous=False)
        if st["gold_coverage_rate"] < 0.78:
            logger.warning(
                "  [%02d/%02d] %-35s | SKIPPED (low page alignment %.1f%%)",
                idx,
                len(non_holdout_projs),
                pid[:35],
                st["gold_coverage_rate"] * 100,
            )
            continue
        all_samples.extend(samples)
        st["project_id"] = pid
        project_stats.append(st)
        logger.info(
            "  [%02d/%02d] %-35s | cands=%4d | pos=%3d | neg=%4d | gold_cov=%.1f%%",
            idx,
            len(non_holdout_projs),
            pid[:35],
            st["total_candidates"],
            st["positive_samples"],
            st["negative_samples"],
            st["gold_coverage_rate"] * 100,
        )

    # Group split non-holdout projects into Train (75%), Val (12.5%), Test (12.5%)
    groups = [s.project_id for s in all_samples]
    gss1 = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=42)
    train_idx, temp_idx = next(gss1.split(all_samples, groups=groups))
    train_samples = [all_samples[i] for i in train_idx]
    temp_samples = [all_samples[i] for i in temp_idx]

    temp_groups = [s.project_id for s in temp_samples]
    gss2 = GroupShuffleSplit(n_splits=1, test_size=0.50, random_state=42)
    val_idx, test_idx = next(gss2.split(temp_samples, groups=temp_groups))
    val_samples = [temp_samples[i] for i in val_idx]
    test_samples = [temp_samples[i] for i in test_idx]

    logger.info(
        "Dataset Split Summary -> Train: %d (pos=%d, neg=%d) | Val: %d (pos=%d, neg=%d) | Test: %d (pos=%d, neg=%d)",
        len(train_samples),
        sum(s.label for s in train_samples),
        sum(1 - s.label for s in train_samples),
        len(val_samples),
        sum(s.label for s in val_samples),
        sum(1 - s.label for s in val_samples),
        len(test_samples),
        sum(s.label for s in test_samples),
        sum(1 - s.label for s in test_samples),
    )

    # Step 3: Train BinaryRequirementFilter (SVM & LR comparison)
    svm_model = BinaryRequirementFilter(classifier_type="svm", c_value=0.8, random_state=42)
    val_report_svm = svm_model.fit(train_samples, val_samples=val_samples)
    test_report_svm = svm_model.evaluate(test_samples, split_name="in_domain_test")

    lr_model = BinaryRequirementFilter(classifier_type="lr", c_value=1.5, random_state=42)
    val_report_lr = lr_model.fit(train_samples, val_samples=val_samples)
    test_report_lr = lr_model.evaluate(test_samples, split_name="in_domain_test")

    logger.info(
        "In-Domain Test Comparison -> SVM: Acc=%.4f, Macro-F1=%.4f, Pos-F1=%.4f, AUC=%.4f | LR: Acc=%.4f, Macro-F1=%.4f, Pos-F1=%.4f, AUC=%.4f",
        test_report_svm["accuracy"],
        test_report_svm["macro_f1"],
        test_report_svm["positive_class"]["f1"],
        test_report_svm["roc_auc"],
        test_report_lr["accuracy"],
        test_report_lr["macro_f1"],
        test_report_lr["positive_class"]["f1"],
        test_report_lr["roc_auc"],
    )

    best_model = svm_model if test_report_svm["positive_class"]["f1"] >= test_report_lr["positive_class"]["f1"] else lr_model
    best_model.save(args.model_dir)

    # Step 4: Evaluate on strictly unseen Holdout Projects (S604 `2022/11.11` & `2024/2024.1.11`)
    holdout_samples_clean: List[BinaryCandidateSample] = []
    holdout_samples_full: List[BinaryCandidateSample] = []
    holdout_book_reports: Dict[str, Dict] = {}

    for hinfo in holdout_projs:
        hpid = hinfo["project_id"]
        hcands = segment_tender_pdf(hinfo["pdf_file"])
        hanns = json.loads(hinfo["ann_file"].read_text(encoding="utf-8"))
        hs_clean, hst = align_candidates_with_annotations(hcands, hanns, project_id=hpid, keep_ambiguous=False)
        hs_full, _ = align_candidates_with_annotations(hcands, hanns, project_id=hpid, keep_ambiguous=True)
        holdout_samples_clean.extend(hs_clean)
        holdout_samples_full.extend(hs_full)

        # Evaluate at optimal F1 threshold, high-recall threshold (0.20), and chapter-aware high-recall mode
        clf_eval_opt = best_model.evaluate(hs_clean, split_name=f"{hpid}_clean", threshold=best_model.threshold)
        e2e_opt = evaluate_end_to_end_gold_recall(best_model, hcands, hanns, threshold=best_model.threshold, chapter_aware=False)
        e2e_high_recall = evaluate_end_to_end_gold_recall(best_model, hcands, hanns, threshold=0.20, chapter_aware=True)

        holdout_book_reports[hpid] = {
            "alignment_stats": hst,
            "binary_classification_metrics": clf_eval_opt,
            "end_to_end_extraction_at_optimal_threshold": e2e_opt,
            "end_to_end_extraction_at_chapter_aware_high_recall": e2e_high_recall,
        }
        logger.info(
            "Holdout [%s] -> Acc=%.4f, AUC=%.4f, Pos-F1=%.4f | Seg Cov=%.1f%% | Default Recall=%.1f%% (Kept=%d, NoiseReduced=%.1f%%) | Chapter-Aware High-Recall=%.1f%% (Kept=%d, NoiseReduced=%.1f%%)",
            hpid,
            clf_eval_opt["accuracy"],
            clf_eval_opt["roc_auc"],
            clf_eval_opt["positive_class"]["f1"],
            e2e_opt["segmenter_gold_recall"] * 100,
            e2e_opt["post_filter_gold_recall"] * 100,
            e2e_opt["retained_candidates"],
            e2e_opt["noise_reduction_rate"] * 100,
            e2e_high_recall["post_filter_gold_recall"] * 100,
            e2e_high_recall["retained_candidates"],
            e2e_high_recall["noise_reduction_rate"] * 100,
        )

    holdout_overall_clean = best_model.evaluate(holdout_samples_clean, split_name="holdout_2_projects_clean")
    holdout_overall_full = best_model.evaluate(holdout_samples_full, split_name="holdout_2_projects_all_candidates")

    full_report = {
        "dataset_summary": {
            "num_non_holdout_projects": len(non_holdout_projs),
            "num_holdout_projects": len(holdout_projs),
            "train_samples": len(train_samples),
            "val_samples": len(val_samples),
            "test_samples": len(test_samples),
            "holdout_clean_samples": len(holdout_samples_clean),
            "holdout_full_samples": len(holdout_samples_full),
        },
        "models_comparison_on_test": {
            "calibrated_linear_svc": test_report_svm,
            "logistic_regression": test_report_lr,
        },
        "validation_metrics": val_report_svm if best_model is svm_model else val_report_lr,
        "in_domain_test_metrics": test_report_svm if best_model is svm_model else test_report_lr,
        "holdout_overall_clean_metrics": holdout_overall_clean,
        "holdout_overall_full_metrics": holdout_overall_full,
        "holdout_per_project_end_to_end": holdout_book_reports,
    }

    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved full evaluation report to %s", args.report_path)


if __name__ == "__main__":
    main()

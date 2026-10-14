"""
Unified Command-Line Entrypoint for TenderCheck Requirement Classifier.

Supports:
1. `split_only`: Load annotations and preview/export group-based Train/Val/Test splits
   without starting model training.
2. `train`: Train and evaluate either the TF-IDF baseline (`--model tfidf`),
   the fine-tuned Chinese RoBERTa model (`--model roberta`), or both (`--model all`).
3. `predict`: Run interactive/single-clause inference using a saved model checkpoint.

Usage Examples (from `SystemCode/` directory):
    # 1. Preview dataset group split only (no training)
    python train_classifier.py --mode split_only

    # 2. Train lightweight TF-IDF + SVM baseline
    python train_classifier.py --mode train --model tfidf

    # 3. Fine-tune Chinese RoBERTa-wwm-ext
    python train_classifier.py --mode train --model roberta --epochs 5 --batch_size 32

    # 4. Run full dual-model comparison (Baseline + RoBERTa)
    python train_classifier.py --mode train --model all

    # 5. Predict on a single requirement clause
    python train_classifier.py --mode predict --model_dir models/tfidf_svm \\
        --section "第三章 评标办法" --text "施工组织设计满分15分，缺项得0分。"
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List

from src.classification import (
    DEFAULT_PRETRAINED_MODEL,
    BertRequirementClassifier,
    TfidfRequirementClassifier,
    load_classifier,
)
from src.data import (
    REQUIREMENT_TYPES,
    RequirementSample,
    export_splits_to_jsonl,
    group_split_dataset,
    load_annotations_from_dir,
    summarize_splits,
)
from src.evaluation import (
    evaluate_predictions,
    format_metrics_table,
    save_evaluation_report,
)

# Configure UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("train_classifier")

BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TenderCheck 8-Class Requirement Classifier Pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["split_only", "train", "predict"],
        default="train",
        help="Pipeline execution mode (default: train).",
    )
    parser.add_argument(
        "--model",
        choices=["tfidf", "roberta", "all"],
        default="all",
        help="Model architecture to train when --mode=train (default: all).",
    )
    parser.add_argument(
        "--annotations_dir",
        type=Path,
        default=BASE_DIR / "data" / "annotations",
        help="Directory containing year-organized JSON annotation files.",
    )
    parser.add_argument(
        "--processed_dir",
        type=Path,
        default=BASE_DIR / "data" / "processed",
        help="Directory to export train/val/test JSONL splits.",
    )
    parser.add_argument(
        "--models_dir",
        type=Path,
        default=BASE_DIR / "models",
        help="Directory to save trained model checkpoints.",
    )
    parser.add_argument(
        "--reports_dir",
        type=Path,
        default=BASE_DIR / "reports",
        help="Directory to save evaluation metric reports.",
    )

    # Feature & Split options
    parser.add_argument(
        "--no_section_context",
        action="store_true",
        help="Disable prepending '[Section] <section> [SEP]' to requirement text.",
    )
    parser.add_argument(
        "--val_ratio",
        type=float,
        default=0.10,
        help="Validation set ratio for group split (default: 0.10).",
    )
    parser.add_argument(
        "--test_ratio",
        type=float,
        default=0.15,
        help="Test set ratio for group split (default: 0.15).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible splitting and training.",
    )

    # TF-IDF Baseline hyperparameters
    parser.add_argument(
        "--tfidf_clf",
        choices=["svm", "logreg"],
        default="svm",
        help="Linear classifier type for TF-IDF baseline (default: svm).",
    )
    parser.add_argument(
        "--tfidf_c",
        type=float,
        default=1.0,
        help="Regularization parameter C for TF-IDF classifier (default: 1.0).",
    )

    # RoBERTa hyperparameters
    parser.add_argument(
        "--pretrained_model",
        type=str,
        default=DEFAULT_PRETRAINED_MODEL,
        help=f"HuggingFace model name or path (default: {DEFAULT_PRETRAINED_MODEL}).",
    )
    parser.add_argument(
        "--max_length",
        type=int,
        default=256,
        help="Maximum sequence token length for RoBERTa (default: 256).",
    )
    parser.add_argument(
        "--batch_size",
        type=int,
        default=32,
        help="Mini-batch size for RoBERTa training/inference (default: 32).",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=2e-5,
        help="Peak learning rate for RoBERTa AdamW (default: 2e-5).",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=5,
        help="Maximum fine-tuning epochs for RoBERTa (default: 5).",
    )

    # Single-clause prediction arguments
    parser.add_argument(
        "--model_dir",
        type=Path,
        default=None,
        help="Path to saved model directory for --mode=predict.",
    )
    parser.add_argument(
        "--section",
        type=str,
        default="",
        help="Section header string for single-clause prediction.",
    )
    parser.add_argument(
        "--text",
        type=str,
        default="",
        help="Requirement clause text for single-clause prediction.",
    )

    return parser.parse_args()


def print_split_summary(summary: Dict[str, Dict[str, object]]) -> None:
    """Print a clean overview of the grouped Train / Val / Test splits."""
    print("\n" + "=" * 86)
    print("DATASET GROUP SPLIT SUMMARY (Grouped by Tender Project File)")
    print("=" * 86)
    header = f"{'Split':<8} {'Projects':>9} {'Samples':>9}  |  " + " ".join(
        f"{t[:6]:>7}" for t in REQUIREMENT_TYPES
    )
    print(header)
    print("-" * 86)
    for split_name in ("train", "val", "test"):
        info = summary[split_name]
        dist = info["label_distribution"]
        counts_str = " ".join(f"{dist[t]:>7d}" for t in REQUIREMENT_TYPES)
        print(
            f"{split_name:<8} {info['num_projects']:>9d} {info['num_samples']:>9d}  |  {counts_str}"
        )
    print("=" * 86 + "\n")


def run_tfidf_experiment(
    splits: Dict[str, List[RequirementSample]],
    args: argparse.Namespace,
) -> Dict[str, object]:
    """Train, evaluate, and persist the TF-IDF baseline classifier."""
    use_section = not args.no_section_context
    model_tag = f"tfidf_{args.tfidf_clf}"

    clf = TfidfRequirementClassifier(
        use_section_context=use_section,
        classifier_type=args.tfidf_clf,
        c_value=args.tfidf_c,
        random_state=args.seed,
    )
    clf.fit(splits["train"], splits["val"])

    # Evaluate on Validation and Test sets
    reports: Dict[str, object] = {}
    for split_name in ("val", "test"):
        preds = clf.predict_ids(splits[split_name])
        trues = [s.label_id for s in splits[split_name]]
        rep = evaluate_predictions(
            y_true=trues,
            y_pred=preds,
            split_name=split_name,
            model_name=model_tag,
        )
        print("\n" + format_metrics_table(rep))
        save_evaluation_report(
            rep, args.reports_dir / f"{model_tag}_{split_name}_metrics.json"
        )
        reports[split_name] = rep

    clf.save(args.models_dir / model_tag)
    return reports


def run_roberta_experiment(
    splits: Dict[str, List[RequirementSample]],
    args: argparse.Namespace,
) -> Dict[str, object]:
    """Fine-tune, evaluate, and persist the Chinese RoBERTa classifier."""
    use_section = not args.no_section_context
    model_tag = "roberta_wwm_ext"

    clf = BertRequirementClassifier(
        pretrained_model_name_or_path=args.pretrained_model,
        use_section_context=use_section,
        max_length=args.max_length,
        batch_size=args.batch_size,
        learning_rate=args.lr,
        num_epochs=args.epochs,
        random_state=args.seed,
    )
    clf.fit(splits["train"], splits["val"])

    reports: Dict[str, object] = {}
    for split_name in ("val", "test"):
        preds = clf.predict_ids(splits[split_name])
        trues = [s.label_id for s in splits[split_name]]
        rep = evaluate_predictions(
            y_true=trues,
            y_pred=preds,
            split_name=split_name,
            model_name=model_tag,
        )
        print("\n" + format_metrics_table(rep))
        save_evaluation_report(
            rep, args.reports_dir / f"{model_tag}_{split_name}_metrics.json"
        )
        reports[split_name] = rep

    clf.save(args.models_dir / model_tag)
    return reports


def main() -> None:
    args = parse_args()

    if args.mode == "predict":
        if not args.model_dir or not args.text:
            raise ValueError("Both --model_dir and --text are required when --mode=predict.")
        clf = load_classifier(args.model_dir)
        result = clf.predict_single(requirement_text=args.text, section_text=args.section)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # Load and group-split annotations
    samples = load_annotations_from_dir(args.annotations_dir)
    splits = group_split_dataset(
        samples,
        val_ratio=args.val_ratio,
        test_ratio=args.test_ratio,
        random_state=args.seed,
    )
    summary = summarize_splits(splits)
    print_split_summary(summary)

    saved_paths = export_splits_to_jsonl(splits, args.processed_dir)
    logger.info("Exported train/val/test JSONL splits to %s", saved_paths["summary"].parent)

    if args.mode == "split_only":
        return

    if args.model in ("tfidf", "all"):
        run_tfidf_experiment(splits, args)

    if args.model in ("roberta", "all"):
        run_roberta_experiment(splits, args)


if __name__ == "__main__":
    main()

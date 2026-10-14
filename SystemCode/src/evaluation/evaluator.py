"""
Evaluation metrics and structured reporting for TenderCheck 8-class classification.

Computes overall Accuracy, Macro-F1, Weighted-F1, per-category Precision/Recall/F1,
and confusion matrices, formatting results for both console display and JSON archival.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
)

from ..data.schema import ID2LABEL, LABEL2ID, LABEL_CN_NAMES, REQUIREMENT_TYPES


def evaluate_predictions(
    y_true: Sequence[Union[int, str]],
    y_pred: Sequence[Union[int, str]],
    split_name: str = "test",
    model_name: str = "classifier",
) -> Dict[str, object]:
    """
    Compute comprehensive 8-class evaluation metrics.

    Args:
        y_true: Ground-truth labels (either integer class IDs `0..7` or label strings).
        y_pred: Predicted labels (either integer class IDs `0..7` or label strings).
        split_name: Split identifier (e.g., `"val"`, `"test"`).
        model_name: Identifier of the evaluated model.

    Returns:
        Dictionary containing overall metrics, per-class breakdown, and confusion matrix.
    """
    true_ids = [LABEL2ID[y] if isinstance(y, str) else int(y) for y in y_true]
    pred_ids = [LABEL2ID[y] if isinstance(y, str) else int(y) for y in y_pred]
    label_indices = list(range(len(REQUIREMENT_TYPES)))

    acc = float(accuracy_score(true_ids, pred_ids))
    macro_p, macro_r, macro_f1, _ = precision_recall_fscore_support(
        true_ids, pred_ids, labels=label_indices, average="macro", zero_division=0
    )
    weighted_p, weighted_r, weighted_f1, _ = precision_recall_fscore_support(
        true_ids, pred_ids, labels=label_indices, average="weighted", zero_division=0
    )
    per_p, per_r, per_f1, per_sup = precision_recall_fscore_support(
        true_ids, pred_ids, labels=label_indices, average=None, zero_division=0
    )
    cm = confusion_matrix(true_ids, pred_ids, labels=label_indices)

    per_class_metrics: Dict[str, Dict[str, Union[float, int, str]]] = {}
    for idx, label_name in enumerate(REQUIREMENT_TYPES):
        per_class_metrics[label_name] = {
            "cn_name": LABEL_CN_NAMES[label_name],
            "precision": round(float(per_p[idx]), 4),
            "recall": round(float(per_r[idx]), 4),
            "f1": round(float(per_f1[idx]), 4),
            "support": int(per_sup[idx]),
        }

    return {
        "model_name": model_name,
        "split": split_name,
        "num_samples": len(true_ids),
        "accuracy": round(acc, 4),
        "macro_precision": round(float(macro_p), 4),
        "macro_recall": round(float(macro_r), 4),
        "macro_f1": round(float(macro_f1), 4),
        "weighted_precision": round(float(weighted_p), 4),
        "weighted_recall": round(float(weighted_r), 4),
        "weighted_f1": round(float(weighted_f1), 4),
        "per_class": per_class_metrics,
        "confusion_matrix": {
            "labels": list(REQUIREMENT_TYPES),
            "matrix": cm.tolist(),
        },
    }


def format_metrics_table(report: Dict[str, object]) -> str:
    """
    Format an evaluation report dictionary into a human-readable ASCII table.
    """
    lines: List[str] = []
    header = (
        f"=== Evaluation Report | Model: {report['model_name']} | "
        f"Split: {report['split']} (N={report['num_samples']}) ==="
    )
    lines.append("=" * len(header))
    lines.append(header)
    lines.append("=" * len(header))
    lines.append(
        f"Overall Accuracy : {report['accuracy']:.4f}  |  "
        f"Macro-F1 : {report['macro_f1']:.4f}  |  "
        f"Weighted-F1 : {report['weighted_f1']:.4f}"
    )
    lines.append("-" * len(header))
    lines.append(
        f"{'Requirement Type':<16} {'Chinese Name':<12} "
        f"{'Precision':>10} {'Recall':>10} {'F1-Score':>10} {'Support':>9}"
    )
    lines.append("-" * len(header))

    per_class = report.get("per_class", {})
    if isinstance(per_class, dict):
        for label_name in REQUIREMENT_TYPES:
            m = per_class.get(label_name, {})
            lines.append(
                f"{label_name:<16} {m.get('cn_name', ''):<10} "
                f"{m.get('precision', 0.0):>10.4f} "
                f"{m.get('recall', 0.0):>10.4f} "
                f"{m.get('f1', 0.0):>10.4f} "
                f"{m.get('support', 0):>9d}"
            )
    lines.append("=" * len(header))
    return "\n".join(lines)


def save_evaluation_report(report: Dict[str, object], output_path: Path) -> Path:
    """Save the evaluation report dictionary to a formatted JSON file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return output_path

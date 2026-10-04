import json
import sys
from pathlib import Path
from typing import Dict, List, Any
import numpy as np
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score, precision_score, recall_score

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

def evaluate_predictions(
    y_true: List[int],
    y_pred: List[int],
    target_names: List[str] = None
) -> Dict[str, Any]:
    """
    Computes comprehensive evaluation metrics:
    Precision, Recall, Macro-F1, Accuracy, and Confusion Matrix.
    """
    acc = accuracy_score(y_true, y_pred)
    macro_f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
    micro_f1 = f1_score(y_true, y_pred, average="micro", zero_division=0)
    macro_p = precision_score(y_true, y_pred, average="macro", zero_division=0)
    macro_r = recall_score(y_true, y_pred, average="macro", zero_division=0)
    
    report_dict = classification_report(
        y_true, 
        y_pred, 
        target_names=target_names, 
        output_dict=True, 
        zero_division=0
    )
    report_text = classification_report(
        y_true, 
        y_pred, 
        target_names=target_names, 
        digits=4, 
        zero_division=0
    )
    
    cm = confusion_matrix(y_true, y_pred).tolist()
    
    summary = {
        "accuracy": round(float(acc), 4),
        "macro_precision": round(float(macro_p), 4),
        "macro_recall": round(float(macro_r), 4),
        "macro_f1": round(float(macro_f1), 4),
        "micro_f1": round(float(micro_f1), 4),
        "confusion_matrix": cm,
        "classification_report": report_dict,
        "formatted_report": report_text
    }
    return summary

def save_metrics_report(metrics: Dict[str, Any], output_file: Path):
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved evaluation report to {output_file}")

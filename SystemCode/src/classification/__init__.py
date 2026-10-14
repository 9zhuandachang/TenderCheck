"""
Requirement classification package for TenderCheck.

Exposes:
- `BaseRequirementClassifier`: Unified classifier interface.
- `TfidfRequirementClassifier`: Fast statistical baseline (Char+Word N-gram TF-IDF + SVM/LogReg).
- `BertRequirementClassifier`: Fine-tuned `hfl/chinese-roberta-wwm-ext` deep classifier.
- `load_classifier`: Factory loader that automatically detects model type from directory.
"""

from pathlib import Path
from typing import Optional, Union

from .base import BaseRequirementClassifier
from .bert_classifier import DEFAULT_PRETRAINED_MODEL, BertRequirementClassifier
from .tfidf_classifier import TfidfRequirementClassifier


def load_classifier(
    model_dir: Union[str, Path],
    device: Optional[str] = None,
) -> BaseRequirementClassifier:
    """
    Auto-detect and load either a `TfidfRequirementClassifier` or a
    `BertRequirementClassifier` from `model_dir`.
    """
    model_dir = Path(model_dir)
    if (model_dir / "tfidf_pipeline.joblib").exists():
        return TfidfRequirementClassifier.load(model_dir)
    if (model_dir / "classifier_meta.json").exists():
        return BertRequirementClassifier.load(model_dir, device=device)
    raise FileNotFoundError(
        f"No recognized classifier checkpoint found in {model_dir}. "
        "Expected either 'tfidf_pipeline.joblib' or 'classifier_meta.json'."
    )


__all__ = [
    "BaseRequirementClassifier",
    "TfidfRequirementClassifier",
    "BertRequirementClassifier",
    "DEFAULT_PRETRAINED_MODEL",
    "load_classifier",
]

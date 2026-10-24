"""
Requirement classification package for TenderCheck.

Exposes:
- `BaseRequirementClassifier`: Unified classifier interface.
- `BertRequirementClassifier`: Fine-tuned `hfl/chinese-roberta-wwm-ext` deep classifier.
- `load_classifier`: Loader for `BertRequirementClassifier` checkpoints.
"""

from pathlib import Path
from typing import Optional, Union

from .base import BaseRequirementClassifier
from .bert_classifier import DEFAULT_PRETRAINED_MODEL, BertRequirementClassifier


def load_classifier(
    model_dir: Union[str, Path],
    device: Optional[str] = None,
) -> BaseRequirementClassifier:
    """
    Load a `BertRequirementClassifier` (`roberta_wwm_ext`) checkpoint from `model_dir`.
    """
    model_dir = Path(model_dir)
    if (model_dir / "classifier_meta.json").exists():
        return BertRequirementClassifier.load(model_dir, device=device)
    raise FileNotFoundError(
        f"No recognized classifier checkpoint found in {model_dir}. "
        "Expected 'classifier_meta.json'."
    )


__all__ = [
    "BaseRequirementClassifier",
    "BertRequirementClassifier",
    "DEFAULT_PRETRAINED_MODEL",
    "load_classifier",
]

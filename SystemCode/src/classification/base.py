"""
Abstract base interface for all TenderCheck Requirement Classifiers.

Provides a unified API across statistical baselines (TF-IDF + LinearSVC/LogReg)
and deep pretrained language models (Chinese RoBERTa-wwm-ext), enabling seamless
switching during ablation studies and downstream RAG/compliance inference.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import numpy as np

from ..data.schema import (
    ID2LABEL,
    LABEL_CN_NAMES,
    REQUIREMENT_TYPES,
    RequirementSample,
    format_model_input,
)


class BaseRequirementClassifier(ABC):
    """
    Unified interface for 8-class Chinese tender requirement classification.
    """

    def __init__(self, use_section_context: bool = True) -> None:
        """
        Args:
            use_section_context: Whether to prepend `[Section] <section> [SEP]`
                to the requirement clause during training and inference.
        """
        self.use_section_context = use_section_context

    def _prepare_texts(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> List[str]:
        """
        Convert heterogeneous inputs (`RequirementSample`, raw strings, or dicts
        with `'Requirement'` and `'Section'`) into formatted model input strings.
        """
        texts: List[str] = []
        for item in inputs:
            if isinstance(item, RequirementSample):
                texts.append(item.to_model_input(use_section_context=self.use_section_context))
            elif isinstance(item, dict):
                texts.append(
                    format_model_input(
                        requirement_text=str(item.get("Requirement", "")),
                        section_text=str(item.get("Section", "")),
                        use_section_context=self.use_section_context,
                    )
                )
            elif isinstance(item, str):
                texts.append(item.strip())
            else:
                raise TypeError(f"Unsupported input item type: {type(item)}")
        return texts

    @abstractmethod
    def fit(
        self,
        train_samples: Sequence[RequirementSample],
        val_samples: Optional[Sequence[RequirementSample]] = None,
    ) -> Dict[str, object]:
        """
        Train the classifier on `train_samples` and optionally validate on `val_samples`.

        Returns:
            Dictionary of training history or summary metrics.
        """

    @abstractmethod
    def predict_proba(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> np.ndarray:
        """
        Predict class probability distribution of shape `(N, 8)`.
        """

    def predict_ids(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> List[int]:
        """
        Predict integer class IDs in `[0, 7]` for each input item.
        """
        probs = self.predict_proba(inputs)
        return [int(idx) for idx in np.argmax(probs, axis=1)]

    def predict(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> List[str]:
        """
        Predict standard `Requirement Type` label strings (e.g., `'Qualification'`).
        """
        pred_ids = self.predict_ids(inputs)
        return [ID2LABEL[idx] for idx in pred_ids]

    def predict_single(
        self,
        requirement_text: str,
        section_text: Optional[str] = None,
    ) -> Dict[str, object]:
        """
        Convenience method to classify a single clause with confidence scores.

        Args:
            requirement_text: Atomic requirement clause text.
            section_text: Optional section header string.

        Returns:
            Dictionary with predicted `'label'`, `'cn_name'`, `'confidence'`,
            and `'probabilities'` over all 8 categories.
        """
        formatted = format_model_input(
            requirement_text=requirement_text,
            section_text=section_text,
            use_section_context=self.use_section_context,
        )
        probs = self.predict_proba([formatted])[0]
        best_id = int(np.argmax(probs))
        best_label = ID2LABEL[best_id]

        return {
            "label": best_label,
            "cn_name": LABEL_CN_NAMES[best_label],
            "confidence": round(float(probs[best_id]), 4),
            "probabilities": {
                REQUIREMENT_TYPES[i]: round(float(probs[i]), 4)
                for i in range(len(REQUIREMENT_TYPES))
            },
        }

    @abstractmethod
    def save(self, model_dir: Union[str, Path]) -> Path:
        """Persist trained model weights and configuration to `model_dir`."""

    @classmethod
    @abstractmethod
    def load(cls, model_dir: Union[str, Path]) -> "BaseRequirementClassifier":
        """Load a persisted classifier instance from `model_dir`."""

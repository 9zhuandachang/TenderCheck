"""
Lightweight Statistical Baseline Classifier for Chinese Tender Requirements.

Combines character-level (1-4 gram) and word-level (1-2 gram) sublinear TF-IDF
features with class-balanced LinearSVC (Platt-calibrated for probability output)
or Logistic Regression. Trains in seconds on CPU and provides a strong baseline
for ablation studies against pretrained language models.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from ..data.schema import NUM_LABELS, RequirementSample
from ..evaluation.evaluator import evaluate_predictions
from .base import BaseRequirementClassifier

logger = logging.getLogger(__name__)


class TfidfRequirementClassifier(BaseRequirementClassifier):
    """
    Hybrid Char+Word N-gram TF-IDF classifier with balanced class weights.
    """

    def __init__(
        self,
        use_section_context: bool = True,
        classifier_type: str = "svm",
        char_ngram_range: Tuple[int, int] = (1, 4),
        max_features: int = 60000,
        c_value: float = 1.0,
        class_weight: Optional[str] = "balanced",
        random_state: int = 42,
    ) -> None:
        """
        Args:
            use_section_context: Whether to prepend `[Section] <section> [SEP]`.
            classifier_type: `"svm"` (Calibrated LinearSVC) or `"logreg"` (LogisticRegression).
            char_ngram_range: Character n-gram range for Chinese text representation.
            max_features: Maximum vocabulary size per vectorizer.
            c_value: Regularization strength parameter `C`.
            class_weight: `"balanced"` to counter label imbalance across the 8 types,
                or `None` for uniform sample weighting.
            random_state: Random seed for reproducibility.
        """
        super().__init__(use_section_context=use_section_context)
        self.classifier_type = classifier_type.lower()
        self.char_ngram_range = char_ngram_range
        self.max_features = max_features
        self.c_value = c_value
        self.class_weight = class_weight
        self.random_state = random_state
        self.pipeline: Optional[Pipeline] = None

    def _build_pipeline(self) -> Pipeline:
        """Construct the scikit-learn FeatureUnion + Linear Classifier pipeline."""
        char_vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=self.char_ngram_range,
            max_features=self.max_features,
            sublinear_tf=True,
            min_df=2,
        )
        word_vectorizer = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(2, 4),
            max_features=self.max_features // 2,
            sublinear_tf=True,
            min_df=2,
        )
        features = FeatureUnion(
            transformer_list=[
                ("char_tfidf", char_vectorizer),
                ("word_wb_tfidf", word_vectorizer),
            ]
        )

        if self.classifier_type == "svm":
            base_svc = LinearSVC(
                C=self.c_value,
                class_weight=self.class_weight,
                random_state=self.random_state,
                dual="auto",
                max_iter=3000,
            )
            clf = CalibratedClassifierCV(estimator=base_svc, cv=3, method="sigmoid")
        elif self.classifier_type == "logreg":
            clf = LogisticRegression(
                C=self.c_value,
                class_weight=self.class_weight,
                max_iter=1000,
                random_state=self.random_state,
            )
        else:
            raise ValueError(f"Unsupported classifier_type='{self.classifier_type}'. Choose 'svm' or 'logreg'.")

        return Pipeline(
            steps=[
                ("features", features),
                ("clf", clf),
            ]
        )

    def fit(
        self,
        train_samples: Sequence[RequirementSample],
        val_samples: Optional[Sequence[RequirementSample]] = None,
    ) -> Dict[str, object]:
        """
        Fit the TF-IDF + Linear model on `train_samples`.
        """
        if not train_samples:
            raise ValueError("Training sample list cannot be empty.")

        x_train = self._prepare_texts(train_samples)
        y_train = [s.label_id for s in train_samples]

        logger.info(
            "Fitting TfidfRequirementClassifier (%s) on %d samples...",
            self.classifier_type,
            len(x_train),
        )
        self.pipeline = self._build_pipeline()
        self.pipeline.fit(x_train, y_train)

        train_preds = self.predict_ids(train_samples)
        train_metrics = evaluate_predictions(
            y_true=y_train,
            y_pred=train_preds,
            split_name="train",
            model_name=f"tfidf_{self.classifier_type}",
        )

        result: Dict[str, object] = {"train": train_metrics}
        if val_samples:
            y_val = [s.label_id for s in val_samples]
            val_preds = self.predict_ids(val_samples)
            val_metrics = evaluate_predictions(
                y_true=y_val,
                y_pred=val_preds,
                split_name="val",
                model_name=f"tfidf_{self.classifier_type}",
            )
            result["val"] = val_metrics
            logger.info(
                "TF-IDF Validation | Acc: %.4f | Macro-F1: %.4f | Weighted-F1: %.4f",
                val_metrics["accuracy"],
                val_metrics["macro_f1"],
                val_metrics["weighted_f1"],
            )

        return result

    def predict_proba(
        self,
        inputs: Sequence[Union[RequirementSample, str, Dict[str, str]]],
    ) -> np.ndarray:
        """
        Predict `(N, 8)` class probabilities.
        """
        if self.pipeline is None:
            raise RuntimeError("Model has not been fitted or loaded yet.")
        texts = self._prepare_texts(inputs)
        probs = self.pipeline.predict_proba(texts)

        # Ensure full 8-column probability array even if a tiny subset missed a class
        clf_classes = self.pipeline.named_steps["clf"].classes_
        if len(clf_classes) == NUM_LABELS:
            return probs

        full_probs = np.zeros((len(texts), NUM_LABELS), dtype=np.float64)
        for col_idx, cls_id in enumerate(clf_classes):
            full_probs[:, int(cls_id)] = probs[:, col_idx]
        return full_probs

    def save(self, model_dir: Union[str, Path]) -> Path:
        """
        Save fitted scikit-learn pipeline and metadata config to `model_dir`.
        """
        if self.pipeline is None:
            raise RuntimeError("Cannot save an unfitted TfidfRequirementClassifier.")

        model_dir = Path(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        pipeline_path = model_dir / "tfidf_pipeline.joblib"
        config_path = model_dir / "config.json"

        joblib.dump(self.pipeline, pipeline_path)
        config = {
            "model_type": "tfidf",
            "use_section_context": self.use_section_context,
            "classifier_type": self.classifier_type,
            "char_ngram_range": list(self.char_ngram_range),
            "max_features": self.max_features,
            "c_value": self.c_value,
            "class_weight": self.class_weight,
            "random_state": self.random_state,
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info("Saved TfidfRequirementClassifier to %s", model_dir)
        return model_dir

    @classmethod
    def load(cls, model_dir: Union[str, Path]) -> "TfidfRequirementClassifier":
        """
        Load a saved `TfidfRequirementClassifier` from `model_dir`.
        """
        model_dir = Path(model_dir)
        config_path = model_dir / "config.json"
        pipeline_path = model_dir / "tfidf_pipeline.joblib"

        if not config_path.exists() or not pipeline_path.exists():
            raise FileNotFoundError(f"Missing TF-IDF model artifacts in {model_dir}")

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        instance = cls(
            use_section_context=bool(config.get("use_section_context", True)),
            classifier_type=str(config.get("classifier_type", "svm")),
            char_ngram_range=tuple(config.get("char_ngram_range", [1, 4])),
            max_features=int(config.get("max_features", 60000)),
            c_value=float(config.get("c_value", 1.0)),
            class_weight=config.get("class_weight", "balanced"),
            random_state=int(config.get("random_state", 42)),
        )
        instance.pipeline = joblib.load(pipeline_path)
        return instance

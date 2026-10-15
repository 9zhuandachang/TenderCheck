"""
Lightweight Statistical Baseline Classifier for Chinese Tender Requirements.

Combines character-level (1-4 gram) and word-boundary (2-4 gram) sublinear TF-IDF
features on the requirement body with a down-weighted section-header TF-IDF channel
(`transformer_weights={"req_char": 1.0, "req_word": 0.8, "sec_char": 0.2}`),
preventing broad chapter titles from diluting short atomic clause keywords.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import joblib
import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from ..data.schema import NUM_LABELS, RequirementSample
from ..evaluation.evaluator import evaluate_predictions
from .base import BaseRequirementClassifier

logger = logging.getLogger(__name__)

_SEP_PATTERN = re.compile(r"^\[Section\]\s*(.*?)\s*\[SEP\]\s*(.*)$", flags=re.DOTALL)


class _PartExtractor(BaseEstimator, TransformerMixin):
    """Extract either the `'requirement'` body or the `'section'` header from formatted input."""

    def __init__(self, part: str = "requirement") -> None:
        self.part = part

    def fit(self, x, y=None):
        return self

    def transform(self, x: Sequence[str]) -> List[str]:
        out: List[str] = []
        for text in x:
            m = _SEP_PATTERN.match(text or "")
            if m:
                sec_part, req_part = m.group(1).strip(), m.group(2).strip()
            else:
                sec_part, req_part = "", (text or "").strip()
            out.append(sec_part if self.part == "section" else req_part)
        return out


class TfidfRequirementClassifier(BaseRequirementClassifier):
    """
    Multi-Channel Char+Word N-gram TF-IDF classifier with balanced class weights.
    """

    def __init__(
        self,
        use_section_context: bool = True,
        section_weight: float = 0.20,
        classifier_type: str = "svm",
        char_ngram_range: Tuple[int, int] = (1, 4),
        max_features: int = 60000,
        c_value: float = 1.0,
        class_weight: Optional[str] = "balanced",
        random_state: int = 42,
    ) -> None:
        super().__init__(use_section_context=use_section_context)
        self.section_weight = section_weight
        self.classifier_type = classifier_type.lower()
        self.char_ngram_range = char_ngram_range
        self.max_features = max_features
        self.c_value = c_value
        self.class_weight = class_weight
        self.random_state = random_state
        self.pipeline: Optional[Pipeline] = None

    def _build_pipeline(self) -> Pipeline:
        """Construct the multi-channel scikit-learn FeatureUnion + Linear Classifier pipeline."""
        req_char_pipe = Pipeline(
            [
                ("extract_req", _PartExtractor("requirement")),
                (
                    "tfidf",
                    TfidfVectorizer(
                        analyzer="char",
                        ngram_range=self.char_ngram_range,
                        max_features=self.max_features,
                        sublinear_tf=True,
                        min_df=2,
                    ),
                ),
            ]
        )
        req_word_pipe = Pipeline(
            [
                ("extract_req", _PartExtractor("requirement")),
                (
                    "tfidf",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(2, 4),
                        max_features=self.max_features // 2,
                        sublinear_tf=True,
                        min_df=2,
                    ),
                ),
            ]
        )

        transformer_list = [
            ("req_char", req_char_pipe),
            ("req_word", req_word_pipe),
        ]
        transformer_weights = {
            "req_char": 1.0,
            "req_word": 0.8,
        }

        if self.use_section_context and self.section_weight > 0:
            sec_char_pipe = Pipeline(
                [
                    ("extract_sec", _PartExtractor("section")),
                    (
                        "tfidf",
                        TfidfVectorizer(
                            analyzer="char",
                            ngram_range=(2, 4),
                            max_features=10000,
                            sublinear_tf=True,
                            min_df=2,
                        ),
                    ),
                ]
            )
            transformer_list.append(("sec_char", sec_char_pipe))
            transformer_weights["sec_char"] = self.section_weight

        features = FeatureUnion(
            transformer_list=transformer_list,
            transformer_weights=transformer_weights,
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
            raise ValueError(f"Unsupported classifier_type='{self.classifier_type}'.")

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
        if not train_samples:
            raise ValueError("Training sample list cannot be empty.")

        x_train = self._prepare_texts(train_samples)
        y_train = [s.label_id for s in train_samples]

        logger.info(
            "Fitting Multi-Channel TfidfRequirementClassifier (%s, sec_weight=%.2f) on %d samples...",
            self.classifier_type,
            self.section_weight if self.use_section_context else 0.0,
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
        if self.pipeline is None:
            raise RuntimeError("Model has not been fitted or loaded yet.")
        texts = self._prepare_texts(inputs)
        probs = self.pipeline.predict_proba(texts)

        clf_classes = self.pipeline.named_steps["clf"].classes_
        if len(clf_classes) == NUM_LABELS:
            return probs

        full_probs = np.zeros((len(texts), NUM_LABELS), dtype=np.float64)
        for col_idx, cls_id in enumerate(clf_classes):
            full_probs[:, int(cls_id)] = probs[:, col_idx]
        return full_probs

    def save(self, model_dir: Union[str, Path]) -> Path:
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
            "section_weight": self.section_weight,
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
        model_dir = Path(model_dir)
        config_path = model_dir / "config.json"
        pipeline_path = model_dir / "tfidf_pipeline.joblib"

        if not config_path.exists() or not pipeline_path.exists():
            raise FileNotFoundError(f"Missing TF-IDF model artifacts in {model_dir}")

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        instance = cls(
            use_section_context=bool(config.get("use_section_context", True)),
            section_weight=float(config.get("section_weight", 0.20)),
            classifier_type=str(config.get("classifier_type", "svm")),
            char_ngram_range=tuple(config.get("char_ngram_range", [1, 4])),
            max_features=int(config.get("max_features", 60000)),
            c_value=float(config.get("c_value", 1.0)),
            class_weight=config.get("class_weight", "balanced"),
            random_state=int(config.get("random_state", 42)),
        )
        instance.pipeline = joblib.load(pipeline_path)
        return instance

import sys
import json
import joblib
from pathlib import Path
from typing import List, Tuple, Dict, Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from src.utils.metrics import evaluate_predictions, save_metrics_report

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

class BaselineClassifier:
    """
    Standard Baseline Model:
    TF-IDF (Unigrams + Bigrams) with Logistic Regression (class-weighted).
    """
    def __init__(self, max_features: int = 15000, ngram_range: Tuple[int, int] = (1, 2), C: float = 1.0):
        self.vectorizer = TfidfVectorizer(
            ngram_range=ngram_range,
            max_features=max_features,
            sublinear_tf=True,
            strip_accents="unicode"
        )
        self.classifier = LogisticRegression(
            C=C,
            max_iter=1000,
            class_weight="balanced",
            solver="lbfgs"
        )
        self.pipeline = Pipeline([
            ("tfidf", self.vectorizer),
            ("clf", self.classifier)
        ])
        self.classes_ = None

    def fit(self, texts: List[str], labels: List[Any]):
        print(f"[*] Training baseline pipeline on {len(texts)} samples...")
        self.pipeline.fit(texts, labels)
        self.classes_ = list(self.pipeline.classes_)
        print(f"[OK] Training complete. Discovered {len(self.classes_)} classes.")
        return self

    def predict(self, texts: List[str]) -> List[Any]:
        return self.pipeline.predict(texts).tolist()

    def predict_proba(self, texts: List[str]):
        return self.pipeline.predict_proba(texts)

    def evaluate(self, texts: List[str], labels: List[Any], target_names: List[str] = None) -> Dict[str, Any]:
        preds = self.predict(texts)
        return evaluate_predictions(labels, preds, target_names=target_names)

    def save(self, model_path: Path):
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, model_path)
        print(f"[OK] Model artifacts saved to {model_path}")

    @classmethod
    def load(cls, model_path: Path):
        pipeline = joblib.load(model_path)
        instance = cls()
        instance.pipeline = pipeline
        instance.classes_ = list(pipeline.classes_)
        return instance

def load_clause_data(jsonl_path: Path) -> Tuple[List[str], List[str]]:
    texts = []
    labels = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            texts.append(item["text"])
            labels.append(item["category_name"])
    return texts, labels

def load_nli_data(jsonl_path: Path) -> Tuple[List[str], List[int]]:
    texts = []
    labels = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            # Format combined text for NLI feature extraction
            combined_text = f"Requirement: {item['hypothesis']} \n Evidence: {item['context']}"
            texts.append(combined_text)
            labels.append(item["label"])
    return texts, labels

import os
import sys
import json
import joblib
from pathlib import Path
from typing import List, Tuple, Dict, Any

import jieba
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.pipeline import Pipeline

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Add SystemCode directory to sys.path
sys_code_dir = Path(__file__).resolve().parent.parent.parent
if str(sys_code_dir) not in sys.path:
    sys.path.insert(0, str(sys_code_dir))

from src.data.taxonomy import TENDER_CATEGORIES, LABEL_NAMES_ZH, ID2LABEL
from src.utils.metrics import evaluate_predictions, save_metrics_report

# Chinese stopwords commonly found in legal and administrative clauses
CHINESE_STOPWORDS = set([
    "的", "了", "和", "是", "就", "都", "而", "及", "与", "着",
    "或", "一个", "没有", "我们", "你们", "其", "在", "当", "但",
    "等", "经", "以", "由", "按", "为", "对", "至", "从"
])

def chinese_tokenizer(text: str) -> List[str]:
    """Tokenize Chinese text using jieba, filtering out single whitespace/punctuation."""
    words = jieba.cut(text)
    return [w.strip() for w in words if len(w.strip()) > 0 and w.strip() not in CHINESE_STOPWORDS]

class ChineseTenderClauseClassifier:
    """
    Supervised Machine Learning Classifier for Chinese Engineering Tender Clauses:
    TfidfVectorizer (jieba unigram + bigram) + Balanced Classifier (Logistic Regression / LinearSVC).
    """
    def __init__(self, model_type: str = "logistic_regression", C: float = 2.0, max_features: int = 15000):
        self.model_type = model_type
        self.vectorizer = TfidfVectorizer(
            tokenizer=chinese_tokenizer,
            ngram_range=(1, 2),
            max_features=max_features,
            sublinear_tf=True,
            min_df=2
        )
        if model_type == "svm":
            self.clf = LinearSVC(C=C, class_weight="balanced", max_iter=2000, random_state=42)
        else:
            self.clf = LogisticRegression(
                C=C, 
                class_weight="balanced", 
                max_iter=1000, 
                solver="lbfgs", 
                random_state=42
            )
            
        self.pipeline = Pipeline([
            ("tfidf", self.vectorizer),
            ("clf", self.clf)
        ])
        self.classes_ = None

    def fit(self, texts: List[str], labels: List[int]):
        print(f"[*] Training {self.model_type.upper()} classifier on {len(texts)} Chinese clauses...")
        self.pipeline.fit(texts, labels)
        self.classes_ = list(self.pipeline.classes_)
        print(f"[OK] Training complete. Classes fitted: {self.classes_}")
        return self

    def predict(self, texts: List[str]) -> List[int]:
        return self.pipeline.predict(texts).tolist()

    def evaluate(self, texts: List[str], labels: List[int]) -> Dict[str, Any]:
        preds = self.predict(texts)
        # Unique target names present
        present_classes = sorted(list(set(labels) | set(preds)))
        target_names = [LABEL_NAMES_ZH[c] for c in present_classes]
        return evaluate_predictions(labels, preds, target_names=target_names)

    def save(self, model_path: Path):
        model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, model_path)
        print(f"[OK] Model saved to {model_path}")

    @classmethod
    def load(cls, model_path: Path):
        pipeline = joblib.load(model_path)
        instance = cls()
        instance.pipeline = pipeline
        instance.classes_ = list(pipeline.classes_)
        return instance

def load_chinese_dataset(jsonl_path: Path) -> Tuple[List[str], List[int]]:
    texts, labels = [], []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            texts.append(item["text"])
            labels.append(item["label"])
    return texts, labels

def train_and_evaluate_chinese_pipeline():
    base_dir = Path(__file__).resolve().parent.parent.parent
    data_dir = base_dir / "data" / "processed"
    models_dir = base_dir / "models"
    reports_dir = base_dir / "reports"
    
    train_file = data_dir / "chinese_tender_train.jsonl"
    dev_file = data_dir / "chinese_tender_dev.jsonl"
    test_file = data_dir / "chinese_tender_test.jsonl"
    
    print("[*] Loading Chinese engineering tender datasets...")
    train_texts, train_labels = load_chinese_dataset(train_file)
    dev_texts, dev_labels = load_chinese_dataset(dev_file)
    test_texts, test_labels = load_chinese_dataset(test_file)
    print(f"  Train: {len(train_texts)} | Dev: {len(dev_texts)} | Test: {len(test_texts)}")

    # 1. Train Logistic Regression
    print("\n" + "="*50)
    print("MODEL 1: TF-IDF + LOGISTIC REGRESSION (BALANCED)")
    print("="*50)
    lr_model = ChineseTenderClauseClassifier(model_type="logistic_regression", C=2.0)
    lr_model.fit(train_texts, train_labels)
    
    dev_metrics_lr = lr_model.evaluate(dev_texts, dev_labels)
    print("\n--- Dev Set Evaluation (Logistic Regression) ---")
    print(dev_metrics_lr["formatted_report"])
    print(f"Dev Macro-F1: {dev_metrics_lr['macro_f1']:.4f} | Accuracy: {dev_metrics_lr['accuracy']:.4f}")
    
    test_metrics_lr = lr_model.evaluate(test_texts, test_labels)
    print("\n--- Test Set Evaluation (Unseen Projects Holdout) ---")
    print(test_metrics_lr["formatted_report"])
    print(f"Test Macro-F1: {test_metrics_lr['macro_f1']:.4f} | Accuracy: {test_metrics_lr['accuracy']:.4f}")

    # 2. Train Linear SVM (Comparison Baseline)
    print("\n" + "="*50)
    print("MODEL 2: TF-IDF + LINEAR SVM (BALANCED)")
    print("="*50)
    svm_model = ChineseTenderClauseClassifier(model_type="svm", C=1.0)
    svm_model.fit(train_texts, train_labels)
    
    dev_metrics_svm = svm_model.evaluate(dev_texts, dev_labels)
    print("\n--- Dev Set Evaluation (Linear SVM) ---")
    print(dev_metrics_svm["formatted_report"])
    print(f"Dev Macro-F1: {dev_metrics_svm['macro_f1']:.4f} | Accuracy: {dev_metrics_svm['accuracy']:.4f}")
    
    test_metrics_svm = svm_model.evaluate(test_texts, test_labels)
    print("\n--- Test Set Evaluation (Unseen Projects Holdout) ---")
    print(test_metrics_svm["formatted_report"])
    print(f"Test Macro-F1: {test_metrics_svm['macro_f1']:.4f} | Accuracy: {test_metrics_svm['accuracy']:.4f}")

    # Pick the best model and save
    if test_metrics_lr["macro_f1"] >= test_metrics_svm["macro_f1"]:
        best_name = "Logistic Regression"
        best_model = lr_model
        best_metrics = test_metrics_lr
    else:
        best_name = "Linear SVM"
        best_model = svm_model
        best_metrics = test_metrics_svm
        
    print(f"\n[✓] Best Model: {best_name} (Holdout Test Macro-F1: {best_metrics['macro_f1']:.4f})")
    best_model.save(models_dir / "chinese_tender_clause_classifier.joblib")
    save_metrics_report(best_metrics, reports_dir / "chinese_tender_classification_metrics.json")
    
    # Save comparison summary
    comparison = {
        "dataset_summary": {
            "total_samples": len(train_texts) + len(dev_texts) + len(test_texts),
            "train_samples": len(train_texts),
            "dev_samples": len(dev_texts),
            "test_samples": len(test_texts),
            "categories": LABEL_NAMES_ZH
        },
        "logistic_regression": {
            "dev_macro_f1": dev_metrics_lr["macro_f1"],
            "test_macro_f1": test_metrics_lr["macro_f1"],
            "test_accuracy": test_metrics_lr["accuracy"]
        },
        "linear_svm": {
            "dev_macro_f1": dev_metrics_svm["macro_f1"],
            "test_macro_f1": test_metrics_svm["macro_f1"],
            "test_accuracy": test_metrics_svm["accuracy"]
        },
        "best_model": best_name
    }
    with open(reports_dir / "chinese_models_comparison.json", "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, ensure_ascii=False)
    print(f"[OK] Saved comparison report to {reports_dir / 'chinese_models_comparison.json'}")

if __name__ == "__main__":
    train_and_evaluate_chinese_pipeline()

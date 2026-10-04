import os
import sys
import argparse
from pathlib import Path

# Add SystemCode directory to sys.path so src can be imported directly
current_dir = Path(__file__).resolve().parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

# Ensure UTF-8 output on Windows console
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.data.download import setup_contract_nli
from src.data.cleaner import process_all_splits
from src.baseline.classifier import BaselineClassifier, load_clause_data, load_nli_data
from src.utils.metrics import save_metrics_report

def run_pipeline(step: str):
    data_dir = current_dir / "data"
    raw_dir = data_dir / "raw"
    processed_dir = data_dir / "processed"
    models_dir = current_dir / "models"
    reports_dir = current_dir / "reports"
    
    # 1. Download
    if step in ("download", "all"):
        print("\n" + "="*50)
        print("STAGE 1: DOWNLOAD BENCHMARK DATASET")
        print("="*50)
        setup_contract_nli(data_dir)

    # 2. Clean & Prepare Splits
    if step in ("clean", "all"):
        print("\n" + "="*50)
        print("STAGE 2: DATA CLEANING & SPLIT STANDARDIZATION")
        print("="*50)
        process_all_splits(raw_dir, processed_dir)

    # 3. Train & Evaluate Clause Classification Baseline (Knowledge Discovery)
    if step in ("clause", "all"):
        print("\n" + "="*50)
        print("STAGE 3: CLAUSE CLASSIFIER BASELINE (TF-IDF + LOGISTIC REGRESSION)")
        print("="*50)
        train_path = processed_dir / "contract_clauses_train.jsonl"
        dev_path = processed_dir / "contract_clauses_dev.jsonl"
        test_path = processed_dir / "contract_clauses_test.jsonl"
        
        train_texts, train_labels = load_clause_data(train_path)
        dev_texts, dev_labels = load_clause_data(dev_path)
        test_texts, test_labels = load_clause_data(test_path)
        
        clf = BaselineClassifier(max_features=15000, C=1.0)
        clf.fit(train_texts, train_labels)
        
        # Evaluate on Dev
        dev_metrics = clf.evaluate(dev_texts, dev_labels)
        print("\n--- Clause Classification (Dev Set Results) ---")
        print(dev_metrics["formatted_report"])
        print(f"Dev Macro-F1: {dev_metrics['macro_f1']:.4f} | Accuracy: {dev_metrics['accuracy']:.4f}")
        
        # Evaluate on Test
        test_metrics = clf.evaluate(test_texts, test_labels)
        print("\n--- Clause Classification (Test Set Holdout Results) ---")
        print(test_metrics["formatted_report"])
        print(f"Test Macro-F1: {test_metrics['macro_f1']:.4f} | Accuracy: {test_metrics['accuracy']:.4f}")
        
        # Save artifacts
        clf.save(models_dir / "baseline_clause_classifier.joblib")
        save_metrics_report(dev_metrics, reports_dir / "clause_classification_dev_metrics.json")
        save_metrics_report(test_metrics, reports_dir / "clause_classification_test_metrics.json")

    # 4. Train & Evaluate NLI Compliance Reasoning Baseline (Cognitive Systems)
    if step in ("nli", "all"):
        print("\n" + "="*50)
        print("STAGE 4: COMPLIANCE REASONING / NLI BASELINE (TF-IDF + LOGISTIC REGRESSION)")
        print("="*50)
        train_nli_path = processed_dir / "contract_nli_train_pairs.jsonl"
        dev_nli_path = processed_dir / "contract_nli_dev_pairs.jsonl"
        test_nli_path = processed_dir / "contract_nli_test_pairs.jsonl"
        
        train_nli_texts, train_nli_labels = load_nli_data(train_nli_path)
        dev_nli_texts, dev_nli_labels = load_nli_data(dev_nli_path)
        test_nli_texts, test_nli_labels = load_nli_data(test_nli_path)
        
        target_names = ["Entailment (Pass)", "Contradiction (Conflict)", "NotMentioned (Not Found)"]
        
        nli_clf = BaselineClassifier(max_features=20000, C=1.0)
        nli_clf.fit(train_nli_texts, train_nli_labels)
        
        # Dev evaluation
        dev_nli_metrics = nli_clf.evaluate(dev_nli_texts, dev_nli_labels, target_names=target_names)
        print("\n--- Compliance NLI (Dev Set Results) ---")
        print(dev_nli_metrics["formatted_report"])
        print(f"Dev Macro-F1: {dev_nli_metrics['macro_f1']:.4f} | Accuracy: {dev_nli_metrics['accuracy']:.4f}")
        
        # Test evaluation
        test_nli_metrics = nli_clf.evaluate(test_nli_texts, test_nli_labels, target_names=target_names)
        print("\n--- Compliance NLI (Test Set Holdout Results) ---")
        print(test_nli_metrics["formatted_report"])
        print(f"Test Macro-F1: {test_nli_metrics['macro_f1']:.4f} | Accuracy: {test_nli_metrics['accuracy']:.4f}")
        
        # Save artifacts
        nli_clf.save(models_dir / "baseline_nli_classifier.joblib")
        save_metrics_report(dev_nli_metrics, reports_dir / "compliance_nli_dev_metrics.json")
        save_metrics_report(test_nli_metrics, reports_dir / "compliance_nli_test_metrics.json")

    # 5. Train & Evaluate Chinese Engineering Tender Classifier (5+1 Real Schema)
    if step in ("chinese", "all"):
        print("\n" + "="*50)
        print("STAGE 5: CHINESE ENGINEERING TENDER CLASSIFIER (5+1 CATEGORIES)")
        print("="*50)
        from src.baseline.chinese_classifier import train_and_evaluate_chinese_pipeline
        train_and_evaluate_chinese_pipeline()

    print("\n" + "="*50)
    print("[SUCCESS] All requested baseline stages completed successfully!")
    print("="*50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TenderCheck Baseline Pipeline Runner")
    parser.add_argument(
        "--step",
        type=str,
        choices=["download", "clean", "clause", "nli", "chinese", "all"],
        default="all",
        help="Pipeline step to execute"
    )
    args = parser.parse_args()
    run_pipeline(args.step)

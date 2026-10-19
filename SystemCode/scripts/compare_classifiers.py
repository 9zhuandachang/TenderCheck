"""Compare classifiers on fixed holdouts and optionally the same RAG index."""
import argparse
import csv
import gc
import importlib.metadata
import math
import re
import subprocess
import time
from pathlib import Path

from _rag_experiment_utils import (ROOT, directory_hashes, read_json, require_separate_directories,
                                   set_offline, sha256, write_json)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classifier", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--holdout_json", type=Path, action="append", required=True)
    parser.add_argument("--output_dir", type=Path, required=True)
    parser.add_argument("--index_dir", type=Path)
    parser.add_argument("--rag_holdout_json", type=Path)
    parser.add_argument("--benchmark_json", type=Path)
    parser.add_argument("--reranker_model", default="BAAI/bge-reranker-v2-m3")
    parser.add_argument("--top_k", type=int, default=5)
    parser.add_argument("--max_eval_clauses", type=int, default=0,
                        help="Limit clause retrieval for smoke checks; 0 evaluates all clauses.")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.index_dir) != bool(args.rag_holdout_json):
        parser.error("index_dir and rag_holdout_json must be supplied together")
    if args.benchmark_json and not args.index_dir:
        parser.error("benchmark_json requires index_dir")
    if args.top_k < 1 or args.max_eval_clauses < 0:
        parser.error("Invalid evaluation limit")
    models = {}
    for specification in args.classifier:
        name, separator, path = specification.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in models:
            parser.error("Each classifier must have a unique NAME=PATH")
        models[name] = Path(path)
    for source in [*models.values(), *args.holdout_json, args.index_dir, args.rag_holdout_json, args.benchmark_json]:
        if source is not None:
            require_separate_directories(source, args.output_dir)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output_dir must be new or empty")
    set_offline(args.offline)
    import numpy as np
    import torch
    from evaluate_ml_rag import evaluate_joint_pipeline, load_holdout_samples
    from src.classification import load_classifier
    from src.data.schema import LABEL2ID, REQUIREMENT_TYPES
    from src.evaluation import evaluate_predictions
    from src.rag import CategoryRouterRetriever, EntityLedgerBuilder, HybridBidIndex

    samples = [s for path in args.holdout_json for s in load_holdout_samples(path)]
    if not samples:
        raise ValueError("Empty classification holdout")
    subsets = {"all": list(range(len(samples)))}
    subsets.update({s.project_id: [i for i, row in enumerate(samples) if row.project_id == s.project_id]
                    for s in samples})
    rag_samples = load_holdout_samples(args.rag_holdout_json) if args.index_dir else None
    benchmark = read_json(args.benchmark_json) if args.benchmark_json else None
    index_hashes = directory_hashes(args.index_dir) if args.index_dir else None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(status="running", started_at=time.time(),
                    classifiers={name: str(path) for name, path in models.items()},
                    inputs_sha256={str(p): sha256(p) for p in args.holdout_json},
                    label_order=list(REQUIREMENT_TYPES), index_sha256=index_hashes,
                    top_k=args.top_k, max_eval_clauses=args.max_eval_clauses,
                    metric_scope="Keyword/category proxy; ledger keyword hits count as rank 1.")
    try:
        manifest["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        manifest["git_commit"] = None
    manifest["versions"] = {package: importlib.metadata.version(package)
                            for package in ("torch", "transformers", "scikit-learn", "numpy")}
    manifest["model_files_sha256"] = {}
    if args.benchmark_json:
        manifest["benchmark_sha256"] = sha256(args.benchmark_json)
    if args.rag_holdout_json:
        manifest["rag_holdout_sha256"] = sha256(args.rag_holdout_json)
    class_rows, rag_rows, baselines = [], [], None
    try:
        for name, model_dir in models.items():
            manifest.update(classifier=name, phase="classification")
            write_json(args.output_dir / "run_manifest.json", manifest)
            print(f"CLASSIFICATION_START {name} samples={len(samples)}", flush=True)
            config_path = model_dir / "config.json"
            if config_path.exists() and not (model_dir / "tfidf_pipeline.joblib").exists():
                config = read_json(config_path)
                if config.get("label2id") != LABEL2ID or [config.get("id2label", {}).get(str(i)) for i in range(len(REQUIREMENT_TYPES))] != list(REQUIREMENT_TYPES):
                    raise ValueError(f"Classifier label order mismatch: {name}")
            clf = load_classifier(model_dir)
            manifest["model_files_sha256"][name] = directory_hashes(model_dir)
            probabilities = clf.predict_proba(samples)
            if (probabilities.shape != (len(samples), len(REQUIREMENT_TYPES))
                    or not np.isfinite(probabilities).all()
                    or not np.allclose(probabilities.sum(axis=1), 1, atol=1e-5)):
                raise ValueError(f"Invalid classifier probabilities: {name}")
            predictions = probabilities.argmax(axis=1).tolist()
            metrics = {}
            for subset, ids in subsets.items():
                report = evaluate_predictions([samples[i].label_id for i in ids], [predictions[i] for i in ids],
                                              split_name=subset, model_name=name)
                metrics[subset] = report
                class_rows.append(dict(classifier=name, subset=subset,
                                       **{k: report[k] for k in ("num_samples", "accuracy", "macro_f1", "weighted_f1")}))
            write_json(args.output_dir / name / "classification.json", metrics)
            if args.index_dir:
                manifest.update(phase="rag")
                write_json(args.output_dir / "run_manifest.json", manifest)
                print(f"RAG_START {name}", flush=True)
                index = HybridBidIndex.load(args.index_dir, reranker_model_name=args.reranker_model)
                if (index.dense_embeddings is None or index.dense_embeddings.ndim != 2
                        or len(index.dense_embeddings) != len(index.child_chunks)
                        or not np.isfinite(index.dense_embeddings).all()):
                    raise ValueError("Dense embeddings missing or invalid; refusing incomplete hybrid run")
                original_rerank = index._rerank_candidates

                def strict_rerank(query, hits, top_k):
                    result = original_rerank(query, hits, top_k)
                    if result and any(h.rerank_score is None or not math.isfinite(h.rerank_score) for h in result):
                        raise RuntimeError("Reranker failed; refusing automatic RRF fallback scores")
                    return result

                index._rerank_candidates = strict_rerank
                ledger = EntityLedgerBuilder.load_ledger(args.index_dir / "ledger.json")
                report = evaluate_joint_pipeline(rag_samples, clf, CategoryRouterRetriever(index, ledger), ledger,
                                                 benchmark_data=benchmark, top_k=args.top_k,
                                                 max_eval_clauses=args.max_eval_clauses)
                write_json(args.output_dir / name / "joint_evaluation.json", report)
                table = report.get("gold_retrieval_benchmark", {}).get("ablation_table", {})
                current = {key: table[key] for key in ("bm25_unrouted", "hybrid_unrouted") if key in table}
                if baselines is not None and current != baselines:
                    raise RuntimeError("Unrouted baselines differ between classifiers")
                baselines = current
                for method, row in table.items():
                    rag_rows.append(dict(classifier=name, method=method,
                                         **{key: row[key] for key in ("num_queries", "hit_at_1", "hit_at_3", "hit_at_5", "mrr_at_5")}))
                del index, ledger, original_rerank, strict_rerank, report
            del clf
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"MODEL_COMPLETE {name}", flush=True)
        if args.index_dir and directory_hashes(args.index_dir) != index_hashes:
            raise RuntimeError("Evaluation changed source index")
        for filename, rows in (("classification.csv", class_rows), ("rag_metrics.csv", rag_rows)):
            if rows:
                with (args.output_dir / filename).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
        manifest.update(status="complete", index_unchanged=bool(args.index_dir))
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        manifest["updated_at"] = time.time()
        write_json(args.output_dir / "run_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

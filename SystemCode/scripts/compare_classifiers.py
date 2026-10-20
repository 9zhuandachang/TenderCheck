"""Compare classifiers on fixed holdouts without mixing in retrieval metrics."""
import argparse
import csv
import gc
import importlib.metadata
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
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    models = {}
    for specification in args.classifier:
        name, separator, path = specification.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in models:
            parser.error("Each classifier must have a unique NAME=PATH")
        models[name] = Path(path)
    for source in [*models.values(), *args.holdout_json]:
        if source is not None:
            require_separate_directories(source, args.output_dir)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output_dir must be new or empty")
    set_offline(args.offline)
    import numpy as np
    import torch
    from src.evaluation.holdout import load_holdout_samples
    from src.classification import load_classifier
    from src.data.schema import LABEL2ID, REQUIREMENT_TYPES
    from src.evaluation import evaluate_predictions

    samples = [s for path in args.holdout_json for s in load_holdout_samples(path)]
    if not samples:
        raise ValueError("Empty classification holdout")
    subsets = {"all": list(range(len(samples)))}
    subsets.update({s.project_id: [i for i, row in enumerate(samples) if row.project_id == s.project_id]
                    for s in samples})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(status="running", started_at=time.time(),
                    classifiers={name: str(path) for name, path in models.items()},
                    inputs_sha256={str(p): sha256(p) for p in args.holdout_json},
                    label_order=list(REQUIREMENT_TYPES),
                    metric_scope="Eight-category classification holdout")
    try:
        manifest["git_commit"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        manifest["git_commit"] = None
    manifest["versions"] = {package: importlib.metadata.version(package)
                            for package in ("torch", "transformers", "scikit-learn", "numpy")}
    manifest["model_files_sha256"] = {}
    class_rows = []
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
            del clf
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"MODEL_COMPLETE {name}", flush=True)
        for filename, rows in (("classification.csv", class_rows),):
            if rows:
                with (args.output_dir / filename).open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
        manifest.update(status="complete")
    except Exception as exc:
        manifest.update(status="failed", error=str(exc))
        raise
    finally:
        manifest["updated_at"] = time.time()
        write_json(args.output_dir / "run_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

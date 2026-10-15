"""
Dataset loading, validation, and group-based splitting for TenderCheck.

Ensures zero data leakage across tender documents by grouping all clauses
from the same source tender file (`project_id`) into the same split
(Train / Validation / Test) while preserving label distribution.
"""

import collections
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.model_selection import GroupShuffleSplit

from .schema import LABEL2ID, REQUIREMENT_TYPES, RequirementSample

logger = logging.getLogger(__name__)

# Default subfolders containing v1.3 standard JSON annotation files
DEFAULT_ANNOTATION_FOLDERS: Tuple[str, ...] = (
    "2022",
    "2023",
    "2024",
    "2025",
    "2026_01",
    "2026_02",
    "2026-03",
    "2026_04",
)


def load_annotations_from_dir(
    annotations_dir: Path,
    folders: Optional[Sequence[str]] = DEFAULT_ANNOTATION_FOLDERS,
    strict: bool = False,
) -> List[RequirementSample]:
    """
    Load all standard v1.3 JSON annotation files from `annotations_dir`.

    Args:
        annotations_dir: Root directory containing annotation folders
            (e.g., `SystemCode/data/annotations`).
        folders: Specific subfolder names to scan. If `None`, recursively scans
            all `.json` files under `annotations_dir`.
        strict: If `True`, raise ValueError on any malformed entry; otherwise
            log a warning and skip the invalid entry.

    Returns:
        List of validated `RequirementSample` instances.
    """
    annotations_dir = Path(annotations_dir)
    if not annotations_dir.exists():
        raise FileNotFoundError(f"Annotations directory not found: {annotations_dir}")

    json_paths: List[Tuple[str, Path]] = []
    if folders is not None:
        for folder in folders:
            folder_path = annotations_dir / folder
            if not folder_path.exists():
                logger.warning("Annotation subfolder '%s' does not exist, skipping.", folder_path)
                continue
            for p in sorted(folder_path.glob("*.json")):
                json_paths.append((folder, p))
    else:
        for p in sorted(annotations_dir.rglob("*.json")):
            rel_parent = str(p.parent.relative_to(annotations_dir))
            json_paths.append((rel_parent, p))

    samples: List[RequirementSample] = []
    skipped_count = 0

    for folder, fpath in json_paths:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception as exc:
            msg = f"Failed to parse JSON file {fpath}: {exc}"
            if strict:
                raise ValueError(msg) from exc
            logger.warning(msg)
            continue

        if not isinstance(raw_data, list):
            continue

        project_stem = fpath.stem.replace("_annotations", "").replace("annotations", "").strip("._-")
        project_id = f"{folder}/{project_stem or fpath.stem}"

        for idx, item in enumerate(raw_data):
            if not isinstance(item, dict):
                skipped_count += 1
                continue

            item_id = str(item.get("ID", f"T{idx + 1:04d}")).strip()
            section = str(item.get("Section", "")).strip()
            requirement = str(item.get("Requirement", "")).strip()
            label = str(item.get("Requirement Type", "")).strip()
            raw_page = item.get("Page", 1)

            if not requirement or label not in LABEL2ID:
                msg = f"Invalid item in {fpath.name} [{item_id}]: label='{label}', req_len={len(requirement)}"
                if strict:
                    raise ValueError(msg)
                skipped_count += 1
                continue

            try:
                page = int(str(raw_page).strip())
            except ValueError:
                page = 1

            samples.append(
                RequirementSample(
                    sample_id=f"{project_id}::{item_id}",
                    item_id=item_id,
                    project_id=project_id,
                    folder=folder,
                    page=page,
                    section=section,
                    requirement=requirement,
                    label=label,
                    label_id=LABEL2ID[label],
                )
            )

    logger.info(
        "Loaded %d valid RequirementSample items across %d files (skipped=%d).",
        len(samples),
        len(json_paths),
        skipped_count,
    )
    return samples


def group_split_dataset(
    samples: Sequence[RequirementSample],
    val_ratio: float = 0.10,
    test_ratio: float = 0.15,
    random_state: int = 42,
    num_trials: int = 50,
) -> Dict[str, List[RequirementSample]]:
    """
    Split dataset into `train`, `val`, and `test` grouped by `project_id`.

    All clauses from a single tender document (`project_id`) are placed into the
    exact same split to prevent document-level data leakage. To ensure the 8
    label distributions across splits closely match the global distribution,
    searches over `num_trials` candidate group splits and selects the one with
    minimal L1 label-distribution divergence.

    Args:
        samples: Full sequence of `RequirementSample` objects.
        val_ratio: Target proportion of projects/samples for validation set.
        test_ratio: Target proportion of projects/samples for test set.
        random_state: Base random seed for reproducibility.
        num_trials: Number of candidate group splits to evaluate for best stratification.

    Returns:
        Dictionary with keys `"train"`, `"val"`, `"test"` mapping to sample lists.
    """
    if not samples:
        raise ValueError("Cannot split an empty sample sequence.")

    groups = np.array([s.project_id for s in samples])
    labels = np.array([s.label_id for s in samples])
    unique_projects = np.unique(groups)

    if len(unique_projects) < 3:
        raise ValueError(
            f"Need at least 3 distinct project files for group split, got {len(unique_projects)}."
        )

    global_dist = np.bincount(labels, minlength=len(REQUIREMENT_TYPES)) / len(labels)

    best_score = float("inf")
    best_splits: Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]] = None

    for trial in range(num_trials):
        seed = random_state + trial
        gss_test = GroupShuffleSplit(n_splits=1, test_size=test_ratio, random_state=seed)
        train_val_idx, test_idx = next(gss_test.split(samples, labels, groups))

        rel_val_ratio = val_ratio / (1.0 - test_ratio)
        gss_val = GroupShuffleSplit(n_splits=1, test_size=rel_val_ratio, random_state=seed + 1000)
        sub_train_idx, sub_val_idx = next(
            gss_val.split(
                train_val_idx,
                labels[train_val_idx],
                groups[train_val_idx],
            )
        )
        train_idx = train_val_idx[sub_train_idx]
        val_idx = train_val_idx[sub_val_idx]

        # Compute label distribution divergence + missing-class penalty
        score = 0.0
        for split_idx in (train_idx, val_idx, test_idx):
            counts = np.bincount(labels[split_idx], minlength=len(REQUIREMENT_TYPES))
            dist = counts / max(len(split_idx), 1)
            score += float(np.abs(dist - global_dist).sum())
            missing_classes = int((counts == 0).sum())
            score += missing_classes * 10.0

        if score < best_score:
            best_score = score
            best_splits = (train_idx, val_idx, test_idx)

    assert best_splits is not None
    train_idx, val_idx, test_idx = best_splits

    return {
        "train": [samples[i] for i in train_idx],
        "val": [samples[i] for i in val_idx],
        "test": [samples[i] for i in test_idx],
    }


def summarize_splits(splits: Dict[str, List[RequirementSample]]) -> Dict[str, Dict[str, object]]:
    """
    Generate summary statistics (sample counts, project counts, label counts) per split.
    """
    summary: Dict[str, Dict[str, object]] = {}
    for split_name, split_samples in splits.items():
        projects = sorted({s.project_id for s in split_samples})
        label_counts = collections.Counter(s.label for s in split_samples)
        summary[split_name] = {
            "num_samples": len(split_samples),
            "num_projects": len(projects),
            "projects": projects,
            "label_distribution": {
                label: label_counts.get(label, 0) for label in REQUIREMENT_TYPES
            },
        }
    return summary


def export_splits_to_jsonl(
    splits: Dict[str, List[RequirementSample]],
    output_dir: Path,
) -> Dict[str, Path]:
    """
    Save `train`, `val`, `test` splits to JSONL files and write a `split_summary.json`.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: Dict[str, Path] = {}

    for split_name, split_samples in splits.items():
        out_path = output_dir / f"requirements_{split_name}.jsonl"
        with open(out_path, "w", encoding="utf-8") as f:
            for sample in split_samples:
                f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")
        saved_paths[split_name] = out_path

    summary_path = output_dir / "split_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summarize_splits(splits), f, ensure_ascii=False, indent=2)
    saved_paths["summary"] = summary_path

    return saved_paths


def load_splits_from_jsonl(processed_dir: Path) -> Dict[str, List[RequirementSample]]:
    """
    Reload previously exported `requirements_{train,val,test}.jsonl` splits.
    """
    processed_dir = Path(processed_dir)
    splits: Dict[str, List[RequirementSample]] = {}
    for split_name in ("train", "val", "test"):
        fpath = processed_dir / f"requirements_{split_name}.jsonl"
        if not fpath.exists():
            raise FileNotFoundError(f"Split file not found: {fpath}")
        items: List[RequirementSample] = []
        with open(fpath, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                items.append(
                    RequirementSample(
                        sample_id=str(obj["sample_id"]),
                        item_id=str(obj["ID"]),
                        project_id=str(obj["project_id"]),
                        folder=str(obj["folder"]),
                        page=int(obj["Page"]),
                        section=str(obj["Section"]),
                        requirement=str(obj["Requirement"]),
                        label=str(obj["Requirement Type"]),
                        label_id=int(obj["label_id"]),
                    )
                )
        splits[split_name] = items
    return splits

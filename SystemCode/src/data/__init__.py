"""Data schema and dataset loading utilities for TenderCheck."""

from .dataset_loader import (
    DEFAULT_ANNOTATION_FOLDERS,
    export_splits_to_jsonl,
    group_split_dataset,
    load_annotations_from_dir,
    load_splits_from_jsonl,
    summarize_splits,
)
from .schema import (
    ID2LABEL,
    LABEL2ID,
    LABEL_CN_NAMES,
    NUM_LABELS,
    REQUIREMENT_TYPES,
    RequirementSample,
    format_model_input,
)

__all__ = [
    "REQUIREMENT_TYPES",
    "LABEL_CN_NAMES",
    "LABEL2ID",
    "ID2LABEL",
    "NUM_LABELS",
    "RequirementSample",
    "format_model_input",
    "DEFAULT_ANNOTATION_FOLDERS",
    "load_annotations_from_dir",
    "group_split_dataset",
    "summarize_splits",
    "export_splits_to_jsonl",
    "load_splits_from_jsonl",
]

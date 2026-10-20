"""Load manually labelled classification holdouts."""
import json
from pathlib import Path
from typing import List
from src.data.schema import LABEL2ID, RequirementSample

def load_holdout_samples(json_path: Path) -> List[RequirementSample]:
    """Load a holdout tender annotation JSON file into RequirementSample objects."""
    raw = json.loads(json_path.read_text(encoding="utf-8"))
    samples: List[RequirementSample] = []
    project_id = json_path.stem
    for idx, item in enumerate(raw):
        label = str(item.get("Requirement Type") or item.get("type") or "").strip()
        if label not in LABEL2ID:
            continue
        item_id = str(item.get("ID") or item.get("id") or f"T{idx + 1:04d}").strip()
        section = str(item.get("Section") or item.get("section") or "").strip()
        requirement = str(item.get("Requirement") or item.get("text") or "").strip()
        page_raw = item.get("Page", 1)
        try:
            page = int(page_raw)
        except (ValueError, TypeError):
            page = 1
        samples.append(
            RequirementSample(
                sample_id=f"{project_id}::{item_id}",
                item_id=item_id,
                project_id=project_id,
                folder="holdout",
                page=page,
                section=section,
                requirement=requirement,
                label=label,
                label_id=LABEL2ID[label],
            )
        )
    return samples

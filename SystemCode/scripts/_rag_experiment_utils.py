"""Shared utilities for local RAG experiments; no model is loaded on import."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

SYSTEM_CODE = Path(__file__).resolve().parents[1]
ROOT = SYSTEM_CODE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SYSTEM_CODE))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    """Replace a complete JSON file, with bounded retries for Windows file locks."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    for attempt in range(5):
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.1)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_sha256(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def directory_hashes(path):
    return {p.name: sha256(p) for p in sorted(Path(path).iterdir()) if p.is_file()}


def require_separate_directories(first, second):
    first, second = Path(first).resolve(), Path(second).resolve()
    if first == second or first in second.parents or second in first.parents:
        raise ValueError(f"Input and output directories must not overlap: {first}, {second}")


def set_offline(enabled):
    if enabled:
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")


def recover_completed_attempt(audit, heading):
    """Reuse EOS-completed OCR plus valid JSON; never invent missing JSON syntax."""
    import re
    from src.rag.vision import DOC_CATEGORIES, PageVisionResult, QwenVLBackend

    def text_for(box):
        entry = next((a for a in audit.get("ocr_attempts", []) if a.get("box") == box), None)
        if entry is None:
            return None
        if entry.get("finish_reason") == "eos" and entry.get("text"):
            return entry["text"]
        x0, y0, x1, y1 = box
        middle = (y0 + y1) // 2
        if middle <= y0:
            return None
        top = text_for([x0, y0, x1, middle])
        bottom = text_for([x0, middle, x1, y1])
        return None if top is None or bottom is None else top + "\n" + bottom

    if not audit.get("ocr_attempts"):
        return None
    text = text_for(audit["ocr_attempts"][0]["box"])
    if text is None:
        return None
    if audit.get("native_text_added"):
        text = audit["native_text_added"] + "\n" + text
    for index in reversed(range(len(audit.get("metadata_attempts", [])))):
        attempt = audit["metadata_attempts"][index]
        if attempt.get("finish_reason") != "eos":
            continue
        try:
            obj = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", attempt["text"].strip()))
        except (json.JSONDecodeError, KeyError):
            continue
        obj, changes = QwenVLBackend.normalize_recovery_metadata(obj, heading)
        if (not isinstance(obj, dict) or obj.get("doc_category") not in DOC_CATEGORIES
                or not isinstance(obj.get("extracted_kv"), dict)
                or not isinstance(obj.get("has_official_seal"), bool)
                or not isinstance(obj.get("has_signature"), bool)
                or not (obj.get("seal_company_name") is None or isinstance(obj["seal_company_name"], str))):
            continue
        audit.update(status="recovered", ocr_characters=len(text), restored_from_completed_output=True,
                     accepted_metadata_attempt_index=index, schema_normalization=changes)
        audit.pop("reason", None)
        return PageVisionResult(pdf_page=int(audit["pdf_page"]), doc_category=obj["doc_category"],
                                ocr_text=text, has_official_seal=obj["has_official_seal"],
                                seal_company_name=obj.get("seal_company_name"), has_signature=obj["has_signature"],
                                extracted_kv=obj["extracted_kv"], backend_name="qwen2.5_vl_recovered")
    return None

"""Document parsing, constraint formatting, deterministic numeric comparator, and drawing register utilities for the review UI.

All real compliance extraction, retrieval, grounding, and R01-R09 evaluation execute exclusively
through the canonical production trunk (`src/extraction/`, `src/rag/`, `src/rules/`, and `tcui/real_backend.py`).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz

from . import config
from .config import COMPLIANT, INSUFFICIENT, NON_COMPLIANT


OP_SYMBOL = {"<=": "≤", ">=": "≥", "==": "="}

DRAWING_ROW_RE = re.compile(r"\b([A-Z]{1,3}-\d{2,4})\s+(.+?)\s+Rev\.?\s*([A-Z0-9]{1,2})\s+(\d{4}-\d{2}-\d{2})")
DRAWING_REF_RE = re.compile(r"Drawing\s+(?:No\.?\s*)?([A-Z]{1,3}-\d{2,4}),?\s+Rev(?:ision)?\.?\s*([A-Z0-9]{1,2})\b", re.I)


# ------------------------------------------------------------------ PDF parsing

def parse_document(path: str, progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Read per-page text from a PDF file and report pages lacking a searchable text layer."""
    doc = fitz.open(path)
    try:
        pages = len(doc)
        texts: List[str] = []
        failed: List[int] = []
        for idx, page in enumerate(doc):
            txt = (page.get_text("text") or "").strip()
            texts.append(txt)
            if len(txt) < 15:
                failed.append(idx + 1)
            if progress and (idx == 0 or (idx + 1) % 25 == 0 or idx + 1 == pages):
                progress(idx + 1, pages)
        return {"pages": pages, "texts": texts, "failed_pages": failed}
    finally:
        doc.close()


def load_page_texts(path: str) -> List[str]:
    """Load raw per-page text list from a PDF file."""
    with fitz.open(path) as doc:
        return [(page.get_text("text") or "").strip() for page in doc]


# ------------------------------------------------------------------ constraint formatting & numeric check

def _canon_unit(unit: str) -> str:
    u = (unit or "").strip().lower()
    if u in {"day", "days", "calendar days", "天", "日历天"}:
        return "days"
    if u in {"week", "weeks", "周"}:
        return "weeks"
    if u in {"month", "months", "月", "个月"}:
        return "months"
    if u in {"year", "years", "年"}:
        return "years"
    if u in {"元", "rmb", "cny"}:
        return "元"
    if u in {"万元"}:
        return "万元"
    if u in {"%", "％", "percent"}:
        return "%"
    return u


def _convertible(unit_a: str, unit_b: str) -> bool:
    a, b = _canon_unit(unit_a), _canon_unit(unit_b)
    if a == b:
        return True
    if {a, b} == {"days", "weeks"}:
        return True
    if {a, b} == {"元", "万元"}:
        return True
    return False


def _convert(value: float, unit: str, target: str) -> float:
    a, b = _canon_unit(unit), _canon_unit(target)
    if a == b:
        return float(value)
    if a == "weeks" and b == "days":
        return float(value) * 7.0
    if a == "days" and b == "weeks":
        return float(value) / 7.0
    if a == "万元" and b == "元":
        return float(value) * 10000.0
    if a == "元" and b == "万元":
        return float(value) / 10000.0
    raise ValueError(f"Unsupported unit conversion: {unit} -> {target}")


def format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    num = f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    return f"{num} {unit}".strip() if unit else num


def describe_constraint(c: Dict[str, Any]) -> str:
    if not c:
        return "—"
    ctype = c.get("type") or c.get("clause_type") or ""
    if ctype == "numeric" and c.get("value") is not None:
        op = OP_SYMBOL.get(c.get("operator", "=="), c.get("operator", "="))
        return f"{c.get('field') or 'Value'} {op} {format_value(c.get('value'), c.get('unit') or '')}"
    return str(c.get("field") or ctype or "—")


def evaluate_rule(constraint: Dict[str, Any], actual: Dict[str, Any], bid_fully_parsed: bool = True) -> Dict[str, Any]:
    """Deterministic numeric comparison helper used for explicit reviewer numeric re-checks."""
    if not constraint or constraint.get("type") != "numeric" or constraint.get("value") is None:
        return {
            "verdict": INSUFFICIENT,
            "rule_id": "R01_NUMERIC_BOUND",
            "explanation": "Non-numeric constraint requires canonical ComplianceRuleEngine evaluation.",
        }
    req_val = float(constraint["value"])
    req_unit = str(constraint.get("unit") or "")
    op = str(constraint.get("operator") or "==")
    act_val = actual.get("value")
    act_unit = str(actual.get("unit") or req_unit)

    if act_val is None:
        return {
            "verdict": INSUFFICIENT,
            "rule_id": "R01_NUMERIC_BOUND",
            "explanation": "Missing numeric value in bid evidence.",
        }
    if not _convertible(act_unit, req_unit):
        return {
            "verdict": INSUFFICIENT,
            "rule_id": "R01_NUMERIC_BOUND",
            "explanation": f"Cannot deterministically convert unit '{act_unit}' to '{req_unit}'.",
        }
    converted = _convert(float(act_val), act_unit, req_unit)
    if op == "<=":
        ok = converted <= req_val
    elif op == ">=":
        ok = converted >= req_val
    elif op == "==":
        ok = abs(converted - req_val) <= 1e-6
    else:
        return {
            "verdict": INSUFFICIENT,
            "rule_id": "R01_NUMERIC_BOUND",
            "explanation": f"Unsupported operator '{op}'.",
        }
    verdict = COMPLIANT if ok else NON_COMPLIANT
    sym = OP_SYMBOL.get(op, op)
    return {
        "verdict": verdict,
        "rule_id": "R01_NUMERIC_BOUND",
        "explanation": f"{format_value(converted, req_unit)} {sym} {format_value(req_val, req_unit)} -> {verdict}",
    }


# ------------------------------------------------------------------ drawing register checks

def extract_drawing_register(page_texts: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for pno, txt in enumerate(page_texts, start=1):
        for ln in (txt or "").splitlines():
            m = DRAWING_ROW_RE.search(ln)
            if m:
                rows.append(
                    {
                        "drawing_no": m.group(1),
                        "title": m.group(2).strip(),
                        "revision": m.group(3),
                        "date": m.group(4),
                        "page": pno,
                    }
                )
    return rows


def extract_drawing_refs(page_texts: List[str]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for pno, txt in enumerate(page_texts, start=1):
        for ln in (txt or "").splitlines():
            for m in DRAWING_REF_RE.finditer(ln):
                refs.append(
                    {
                        "drawing_no": m.group(1),
                        "revision": m.group(2),
                        "page": pno,
                        "snippet": ln.strip()[:220],
                    }
                )
    return refs


def current_register(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Latest entry per drawing number; rows must be ordered tender first, then addenda in issue order."""
    current: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        current[row["drawing_no"]] = row
    return current


def check_drawings(rows: List[Dict[str, Any]], refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Register and reference findings, keyed by drawing number."""
    findings = []
    by_doc: Dict[Tuple[Any, str], List[Dict[str, Any]]] = {}
    for row in rows:
        by_doc.setdefault((row.get("doc_id"), row["drawing_no"]), []).append(row)
    for (_, no), entries in by_doc.items():
        variants = {(e["title"], e["revision"]) for e in entries}
        if len(variants) > 1:
            findings.append({
                "key": f"DWG {no}",
                "kind": "duplicate",
                "verdict": config.INSUFFICIENT,
                "requirement": f"Drawing number {no} shall identify a single drawing in the register.",
                "explanation": "The register lists this number more than once: "
                               + "; ".join(f"{t} Rev {r}" for t, r in sorted(variants))
                               + ". Seek a tender clarification.",
                "tender_page": entries[0].get("page"),
                "tender_doc_id": entries[0].get("doc_id"),
                "bid_page": None,
            })
    current = current_register(rows)
    seen = set()
    for ref in refs:
        no = ref["drawing_no"]
        if (no, ref["revision"]) in seen:
            continue
        seen.add((no, ref["revision"]))
        cur = current.get(no)
        if cur is None:
            findings.append({
                "key": f"DWG {no}", "kind": "unknown", "verdict": config.NON_COMPLIANT,
                "requirement": f"Drawings referenced in the bid shall exist in the tender drawing register ({no}).",
                "explanation": f"The bid references {no} Rev {ref['revision']}, which is not in the register.",
                "tender_page": None, "tender_doc_id": None, "bid_page": ref["page"], "snippet": ref["snippet"],
            })
        elif ref["revision"] != cur["revision"]:
            findings.append({
                "key": f"DWG {no}", "kind": "superseded", "verdict": config.NON_COMPLIANT,
                "requirement": f"Drawing references in the bid shall use the current revision of {no}.",
                "explanation": f"The bid references {no} Rev {ref['revision']}; the current revision is "
                               f"Rev {cur['revision']} dated {cur.get('date') or 'n/a'}.",
                "tender_page": cur.get("page"), "tender_doc_id": cur.get("doc_id"),
                "bid_page": ref["page"], "snippet": ref["snippet"],
            })
        else:
            findings.append({
                "key": f"DWG {no}", "kind": "ok", "verdict": config.COMPLIANT,
                "requirement": f"Drawing references in the bid shall use the current revision of {no}.",
                "explanation": f"The bid references the current revision {no} Rev {cur['revision']}.",
                "tender_page": cur.get("page"), "tender_doc_id": cur.get("doc_id"),
                "bid_page": ref["page"], "snippet": ref["snippet"],
            })
    return findings

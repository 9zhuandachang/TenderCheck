"""Placeholder reasoning engine for the review workspace.

Each public function stands in for one stage of the Proposal pipeline and returns plain dicts, so a
stage can be replaced by the real model without touching the UI:

    parse_document        -> Document Parser
    extract_requirements  -> Requirement Extractor (LLM)
    classify              -> Clause Classifier (ML)
    BidIndex.retrieve     -> Hybrid Evidence Retriever
    extract_constraint /
    find_bid_value        -> LLM structured extraction
    evaluate_rule         -> Symbolic Rule Engine
    check_drawings        -> Version & drawing register check
    suggest_kb / draft    -> Remediation Recommender

The heuristics only handle well-formed English text; they exist so the end-to-end flow can be exercised.
"""

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz

from . import config

ENGINE_STAGES = {
    "classify": "Keyword classifier (placeholder for the trained clause classifier)",
    "retrieve": "Keyword retrieval with IDF weighting (placeholder for hybrid retrieval)",
    "extract": "Pattern-based slot extraction (placeholder for LLM structured extraction)",
}

# ------------------------------------------------------------------ text utilities

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = set(
    """a an the and or of to in on for with by from at as is are be been being this that these those it its
    any all each every such other than then shall must will would should may can could not no nor into upon
    within we our us you your their they which who whom whose where when what how also per via
    tender tenders tenderer tenderers bid bids bidder bidders contractor employer submit submitted submission
    provide provided include including included required requirement requirements clause section works work
    have has hold held less more least most exceed minimum maximum period""".split()
)
UNIT_WORDS = {"day", "days", "calendar", "week", "weeks", "month", "months", "year", "years",
              "percent", "mpa", "mm", "usd", "sgd", "million"}
SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ions", "ion", "ies", "ed", "es", "s", "e")


def stem(token: str) -> str:
    if token.isdigit():
        return token
    for _ in range(2):
        for suf in SUFFIXES:
            if token.endswith(suf) and len(token) - len(suf) >= 3:
                token = token[: -len(suf)]
                break
        else:
            break
    return token


def tokens(text: str, drop_units: bool = False) -> List[str]:
    out = []
    for tok in TOKEN_RE.findall(text.lower()):
        if tok in STOPWORDS or len(tok) < 2:
            continue
        if drop_units and (tok in UNIT_WORDS or tok.isdigit()):
            continue
        out.append(stem(tok))
    return out


def sentences(text: str) -> List[str]:
    flat = re.sub(r"\s+", " ", text).strip()
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])", flat)
    return [p.strip() for p in parts if len(p.strip()) > 3]


# ------------------------------------------------------------------ parsing

def parse_document(path: str, progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Any]:
    """Extract page texts and flag pages without a searchable text layer."""
    doc = fitz.open(path)
    if doc.is_encrypted:
        raise ValueError("PDF is encrypted")
    texts, failed = [], []
    total = len(doc)
    for i, page in enumerate(doc):
        text = page.get_text("text")
        texts.append(text)
        if len(text.strip()) < config.MIN_TEXT_CHARS:
            failed.append(i + 1)
        if progress:
            progress(i + 1, total)
    doc.close()
    Path(str(path) + ".pages.json").write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    return {"pages": total, "failed_pages": failed}


def load_page_texts(path: str) -> List[str]:
    cache = Path(str(path) + ".pages.json")
    if not cache.exists():
        parse_document(path)
    return json.loads(cache.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ requirement extraction

ITEM_RE = re.compile(r"^([A-Z]?\d+(?:\.\d+)+)\s+(.+)$")
NOISE_RE = re.compile(r"^page \d+( of \d+)?$", re.I)
MODAL_RE = re.compile(r"\b(shall|must|is required to|are required to)\b", re.I)
DRAWING_ROW_RE = re.compile(r"\b([A-Z]{1,3}-\d{2,4})\s+(.+?)\s+Rev\.?\s*([A-Z0-9]{1,2})\s+(\d{4}-\d{2}-\d{2})")
DRAWING_REF_RE = re.compile(r"Drawing\s+(?:No\.?\s*)?([A-Z]{1,3}-\d{2,4}),?\s+Rev(?:ision)?\.?\s*([A-Z0-9]{1,2})\b", re.I)
AMEND_RE = re.compile(r"^Clause\s+(\d+(?:\.\d+)+)\s+is\s+(?:amended|replaced|revised)\s+to\s+read:?\s*(.+)$", re.I)
NEW_RE = re.compile(r"^(?:New|Additional)\s+Clause\s+(\d+(?:\.\d+)+):?\s*(.+)$", re.I)


def _is_heading(line: str) -> bool:
    """All-caps title lines such as "4. COMMERCIAL"; wrapped fragments like "USD 2,000,000." are not headings."""
    return line.upper() == line and bool(re.search(r"[A-Z]{4,}", line)) and not line.endswith(".")


def split_items(page_texts: List[str]) -> List[Dict[str, Any]]:
    """Group numbered paragraphs ("2.1 ...") with their wrapped continuation lines."""
    items: List[Dict[str, Any]] = []
    current = None
    for page_no, text in enumerate(page_texts, start=1):
        for raw in text.splitlines():
            line = raw.strip()
            if not line or NOISE_RE.match(line):
                continue
            m = ITEM_RE.match(line)
            if m:
                current = {"clause_no": m.group(1), "text": m.group(2), "page": page_no}
                items.append(current)
            elif _is_heading(line) or DRAWING_ROW_RE.search(line):
                current = None
            elif current is not None:
                current["text"] += " " + line
    for item in items:
        item["text"] = re.sub(r"\s+", " ", item["text"]).strip()
    return items


def extract_requirements(page_texts: List[str]) -> List[Dict[str, Any]]:
    """Atomic requirements from a tender document: numbered clauses that carry an obligation."""
    return [i for i in split_items(page_texts) if MODAL_RE.search(i["text"])]


def extract_addendum_changes(page_texts: List[str]) -> List[Dict[str, Any]]:
    changes = []
    for item in split_items(page_texts):
        m = AMEND_RE.match(item["text"])
        if m:
            changes.append({"kind": "amend", "clause_no": m.group(1), "text": m.group(2), "page": item["page"]})
            continue
        m = NEW_RE.match(item["text"])
        if m:
            changes.append({"kind": "new", "clause_no": m.group(1), "text": m.group(2), "page": item["page"]})
    return changes


def extract_drawing_register(page_texts: List[str]) -> List[Dict[str, Any]]:
    rows = []
    for page_no, text in enumerate(page_texts, start=1):
        for line in text.splitlines():
            m = DRAWING_ROW_RE.search(line)
            if m:
                rows.append({
                    "drawing_no": m.group(1),
                    "title": m.group(2).strip(),
                    "revision": m.group(3).upper(),
                    "date": m.group(4),
                    "page": page_no,
                })
    return rows


def extract_drawing_refs(page_texts: List[str]) -> List[Dict[str, Any]]:
    refs = []
    for page_no, text in enumerate(page_texts, start=1):
        flat = re.sub(r"\s+", " ", text)
        for m in DRAWING_REF_RE.finditer(flat):
            refs.append({
                "drawing_no": m.group(1).upper(),
                "revision": m.group(2).upper(),
                "page": page_no,
                "snippet": m.group(0),
            })
    return refs


# ------------------------------------------------------------------ classification

CATEGORY_KEYWORDS = {
    "Qualification": ["licen", "certif", "iso ", "registered", "experience", "qualif", "accredit", "track record"],
    "Document": ["submit", "method statement", " plan", "attach", "form of", "copy of", "schedule of"],
    "Technical": ["concrete", "steel", "mpa", "specification", "material", "pipe", "strength", "design"],
    "Commercial": ["price", "bond", "insurance", "payment", "contract sum", "usd", "sgd", "retention",
                   "valid for", "validity", "liquidated"],
    "Schedule": ["complet", "calendar days", "programme", "milestone", "commencement", "defects liability"],
    "Consistency": ["drawing", "consistent", "revision"],
    "Integrity": ["conflict of interest", "collusi", "debar", "corrupt", "declare"],
    "Scoring": ["score", "points", "weighting", "evaluated"],
}


def classify(text: str) -> Dict[str, Any]:
    low = f" {text.lower()} "
    hits = {cat: sum(low.count(k) for k in kws) for cat, kws in CATEGORY_KEYWORDS.items()}
    total = sum(hits.values())
    if total == 0:
        return {"category": "Technical", "score": 0.0, "scores": {}}
    best = max(config.CATEGORIES, key=lambda c: (hits.get(c, 0), -config.CATEGORIES.index(c)))
    scores = {c: round(h / total, 2) for c, h in hits.items() if h}
    return {"category": best, "score": scores[best], "scores": scores}


# ------------------------------------------------------------------ constraint extraction

NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
CURRENCY_RE = re.compile(r"(USD|SGD|US\$|S\$|\$)\s?" + NUM + r"(\s*million\b)?", re.I)
UNIT_RE = re.compile(
    NUM + r"\s*(%|percent\b|calendar days?\b|working days?\b|days?\b|weeks?\b|months?\b|years?\b|MPa\b|mm\b)", re.I
)
LE_PHRASES = ["not exceed", "not more than", "no more than", "within", "maximum of", "at most", "up to",
              "no later than", "not later than"]
GE_PHRASES = ["at least", "not less than", "no less than", "minimum", "min."]
FIELD_HINTS = [
    ("defects liability", "Defects liability period"),
    ("complet", "Completion period"),
    ("bond", "Performance bond"),
    ("insurance", "Insurance cover"),
    ("experience", "Experience"),
    ("concrete", "Concrete strength"),
    ("strength", "Strength"),
    ("valid for", "Tender validity"),
    ("validity", "Tender validity"),
    ("retention", "Retention"),
    ("method statement", "Method statement"),
    ("plan", "Plan submission"),
    ("certif", "Certificate"),
    ("conflict of interest", "Conflict of interest declaration"),
]
OP_SYMBOL = {"<=": "≤", ">=": "≥", "==": "="}


def _canon_unit(unit: str) -> str:
    u = unit.lower().strip()
    if u in ("%", "percent"):
        return "%"
    if u.startswith("working day"):
        return "working days"
    if u.startswith("calendar day") or u.startswith("day"):
        return "days"
    for base in ("week", "month", "year"):
        if u.startswith(base):
            return base + "s"
    if u == "mpa":
        return "MPa"
    if u in ("usd", "us$", "$"):
        return "USD"
    if u in ("sgd", "s$"):
        return "SGD"
    return u


def find_quantities(text: str) -> List[Dict[str, Any]]:
    found = []
    for m in CURRENCY_RE.finditer(text):
        value = float(m.group(2).replace(",", ""))
        if m.group(3):
            value *= 1_000_000
        found.append({"value": value, "unit": _canon_unit(m.group(1)), "start": m.start(), "end": m.end()})
    for m in UNIT_RE.finditer(text):
        if any(f["start"] <= m.start() < f["end"] for f in found):
            continue
        found.append({"value": float(m.group(1).replace(",", "")), "unit": _canon_unit(m.group(2)),
                      "start": m.start(), "end": m.end()})
    return sorted(found, key=lambda f: f["start"])


def _operator(text: str, start: int, end: int) -> str:
    before = text[max(0, start - 45):start].lower()
    after = text[end:end + 15].lower()
    best, pos = "==", -1
    for phrase in LE_PHRASES:
        i = before.rfind(phrase)
        if i > pos:
            best, pos = "<=", i
    for phrase in GE_PHRASES:
        i = before.rfind(phrase)
        if i > pos:
            best, pos = ">=", i
    if re.match(r"\s*(or more|or above|or greater)", after):
        best = ">="
    return best


SUBMIT_RE = re.compile(r"\bsubmit\s+(?:a|an|the)?\s*((?:[A-Z][\w-]*\s?){1,5})")


def field_label(text: str, unit: str = "") -> str:
    m = SUBMIT_RE.search(text)
    if m:
        return m.group(1).strip()
    low = text.lower()
    for hint, label in FIELD_HINTS:
        if hint in low:
            return label
    return {"days": "Duration", "%": "Percentage", "USD": "Amount", "SGD": "Amount", "years": "Years"}.get(unit, "Requirement")


def extract_constraint(text: str, category: str) -> Dict[str, Any]:
    quantities = find_quantities(text)
    if quantities:
        q = quantities[0]
        return {
            "type": "numeric",
            "field": field_label(text, q["unit"]),
            "operator": _operator(text, q["start"], q["end"]),
            "value": q["value"],
            "unit": q["unit"],
        }
    return {"type": "document" if category == "Document" else "presence", "field": field_label(text)}


def format_value(value: Optional[float], unit: str) -> str:
    if value is None:
        return "Not found"
    if unit in ("USD", "SGD"):
        return f"{unit} {value:,.0f}"
    num = f"{value:g}"
    return f"{num}%" if unit == "%" else f"{num} {unit}"


def describe_constraint(c: Dict[str, Any]) -> str:
    if not c:
        return ""
    if c.get("type") == "numeric":
        return f"{c['field']} {OP_SYMBOL.get(c['operator'], c['operator'])} {format_value(c['value'], c['unit'])}"
    return f"{c.get('field', 'Requirement')}: present in bid"


# ------------------------------------------------------------------ retrieval over the bid

class BidIndex:
    """Sentence index over one bid document."""

    def __init__(self, page_texts: List[str], failed_pages: Optional[List[int]] = None):
        self.failed_pages = failed_pages or []
        self.pages = []
        df: Counter = Counter()
        for page_no, text in enumerate(page_texts, start=1):
            sents = [(s, set(tokens(s))) for s in sentences(text)]
            page_tokens = set().union(*[t for _, t in sents]) if sents else set()
            df.update(page_tokens)
            self.pages.append({"page": page_no, "sentences": sents, "tokens": page_tokens})
        n = max(len(self.pages), 1)
        self.idf = {tok: math.log(1 + n / cnt) for tok, cnt in df.items()}

    def retrieve(self, keywords: set, k: int = 3) -> List[Dict[str, Any]]:
        if not keywords:
            return []
        norm = sum(self.idf.get(t, math.log(1 + len(self.pages))) for t in keywords)
        hits = []
        for p in self.pages:
            score = sum(self.idf[t] for t in keywords if t in p["tokens"])
            if score <= 0:
                continue
            best = max(p["sentences"], key=lambda s: len(keywords & s[1]))
            hits.append({"page": p["page"], "score": round(score / norm, 3), "snippet": best[0]})
        return sorted(hits, key=lambda h: -h["score"])[:k]

    def best_sentence(self, keywords: set, unit: Optional[str] = None):
        """Sentence with the highest keyword coverage; when unit is given it must state a compatible quantity."""
        best = None
        for p in self.pages:
            for sent, toks in p["sentences"]:
                overlap = len(keywords & toks)
                if overlap == 0:
                    continue
                qty = None
                if unit is not None:
                    qty = next((q for q in find_quantities(sent) if _convertible(q["unit"], unit)), None)
                    if qty is None:
                        continue
                if best is None or overlap > best["overlap"]:
                    best = {"page": p["page"], "sentence": sent, "overlap": overlap, "quantity": qty}
        return best


def _convertible(unit_a: str, unit_b: str) -> bool:
    return unit_a == unit_b or {unit_a, unit_b} == {"days", "weeks"}


def _convert(value: float, unit: str, target: str) -> float:
    if unit == target:
        return value
    if unit == "weeks" and target == "days":
        return value * 7
    if unit == "days" and target == "weeks":
        return value / 7
    raise ValueError(f"Cannot convert {unit} to {target}")


# ------------------------------------------------------------------ rules

def evaluate_rule(constraint: Dict[str, Any], actual: Dict[str, Any], bid_fully_parsed: bool = True) -> Dict[str, Any]:
    """Deterministic verdict from the required constraint and the extracted bid evidence."""
    ctype = constraint.get("type")
    if ctype == "numeric":
        value, unit = actual.get("value"), actual.get("unit") or constraint["unit"]
        if value is None:
            return {"verdict": config.INSUFFICIENT, "rule_id": "R01_NUMERIC_BOUND",
                    "trace": f"actual=None vs {constraint['operator']} {constraint['value']:g} -> ESCALATE",
                    "explanation": f"No bid value was found for {constraint['field'].lower()}."}
        if not _convertible(unit, constraint["unit"]):
            return {"verdict": config.INSUFFICIENT, "rule_id": "R01_NUMERIC_BOUND",
                    "trace": f"unit '{unit}' incompatible with '{constraint['unit']}' -> ESCALATE",
                    "explanation": "The bid states the value in a unit that cannot be compared automatically."}
        actual_v = _convert(value, unit, constraint["unit"])
        op, req = constraint["operator"], constraint["value"]
        ok = {"<=": actual_v <= req, ">=": actual_v >= req, "==": abs(actual_v - req) < 1e-9}[op]
        conv = f" (= {actual_v:g} {constraint['unit']})" if unit != constraint["unit"] else ""
        return {
            "verdict": config.COMPLIANT if ok else config.NON_COMPLIANT,
            "rule_id": "R01_NUMERIC_BOUND",
            "trace": f"{value:g} {unit}{conv} {OP_SYMBOL[op]} {req:g} {constraint['unit']} -> {'PASS' if ok else 'FAIL'}",
            "explanation": (
                f"Bid {constraint['field'].lower()} is {format_value(value, unit)}; the requirement is "
                f"{OP_SYMBOL[op]} {format_value(req, constraint['unit'])}."
            ),
        }

    coverage = actual.get("coverage", 0.0)
    matched, total = actual.get("matched", 0), actual.get("terms", 0)
    trace_cov = f"term coverage {matched}/{total} = {coverage:.2f}"
    if coverage >= 0.75:
        return {"verdict": config.COMPLIANT, "rule_id": "R02_PRESENCE",
                "trace": f"{trace_cov} >= 0.75 -> PASS",
                "explanation": "The bid contains a statement that covers this requirement."}
    if ctype == "document" and coverage < 0.5:
        if not bid_fully_parsed:
            return {"verdict": config.INSUFFICIENT, "rule_id": "R03_DOCUMENT_REQUIRED",
                    "trace": f"{trace_cov} < 0.5 but bid has unparsed pages -> ESCALATE",
                    "explanation": "The required document was not found, but some bid pages could not be read."}
        return {"verdict": config.NON_COMPLIANT, "rule_id": "R03_DOCUMENT_REQUIRED",
                "trace": f"{trace_cov} < 0.5 in fully parsed bid -> FAIL",
                "explanation": "The required submission was not found anywhere in the fully parsed bid."}
    return {"verdict": config.INSUFFICIENT, "rule_id": "R02_PRESENCE",
            "trace": f"{trace_cov} below 0.75 -> ESCALATE",
            "explanation": "Only partial evidence was found; a reviewer needs to decide."}


# ------------------------------------------------------------------ requirement review

def review_requirement(req: Dict[str, Any], index: BidIndex) -> Dict[str, Any]:
    constraint = req.get("constraint") or {}
    keywords = set(tokens(req["text"], drop_units=constraint.get("type") == "numeric"))
    hits = index.retrieve(keywords)
    bid_fully_parsed = not index.failed_pages

    if constraint.get("type") == "numeric":
        best = index.best_sentence(keywords, unit=constraint["unit"])
        needed = min(len(keywords), max(2, math.ceil(0.5 * len(keywords))))
        if best and best["overlap"] >= needed:
            qty = best["quantity"]
            actual = {"value": qty["value"], "unit": qty["unit"], "page": best["page"], "sentence": best["sentence"]}
        else:
            actual = {"value": None, "unit": None}
        rule = evaluate_rule(constraint, actual)
        fields = [{
            "field": constraint["field"],
            "required": f"{OP_SYMBOL[constraint['operator']]} {format_value(constraint['value'], constraint['unit'])}",
            "actual": format_value(actual["value"], actual["unit"] or constraint["unit"]),
            "result": rule["verdict"],
        }]
    else:
        best = index.best_sentence(keywords)
        matched = best["overlap"] if best else 0
        coverage = matched / len(keywords) if keywords else 0.0
        actual = {"coverage": coverage, "matched": matched, "terms": len(keywords)}
        if best and coverage >= 0.5:  # weaker matches are not cited as evidence
            actual.update({"page": best["page"], "sentence": best["sentence"]})
        rule = evaluate_rule(constraint, actual, bid_fully_parsed)
        if rule["verdict"] == config.COMPLIANT:
            found = f"Found (p.{actual['page']})"
        elif best and coverage >= 0.5:
            found = f"Partial match ({matched}/{len(keywords)} terms, p.{actual['page']})"
        else:
            found = "Not found"
        fields = [{"field": constraint.get("field", "Requirement"), "required": "Present in bid",
                   "actual": found, "result": rule["verdict"]}]

    evidence = []
    if actual.get("page"):
        evidence.append({"page": actual["page"], "snippet": actual["sentence"], "role": "Extracted value"})
    for h in hits:
        if rule["verdict"] == config.NON_COMPLIANT and constraint.get("type") != "numeric":
            break  # a missing document has no supporting evidence pages
        if all(h["page"] != e["page"] for e in evidence):
            evidence.append({"page": h["page"], "snippet": h["snippet"], "role": f"Retrieved (score {h['score']:.2f})"})

    chain = {
        "classify": {"category": req["category"], "score": req.get("category_score"),
                     "method": ENGINE_STAGES["classify"]},
        "retrieve": {"method": ENGINE_STAGES["retrieve"], "keywords": sorted(keywords), "hits": hits},
        "extract": {"method": ENGINE_STAGES["extract"], "required": constraint,
                    "actual": {k: v for k, v in actual.items() if k != "sentence"}},
        "rule": {"rule_id": rule["rule_id"], "rule_version": config.RULE_VERSION, "trace": rule["trace"]},
    }
    return {
        "verdict": rule["verdict"],
        "rule_id": rule["rule_id"],
        "explanation": rule["explanation"],
        "fields": fields,
        "evidence": evidence,
        "chain": chain,
    }


# ------------------------------------------------------------------ drawings

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


# ------------------------------------------------------------------ remediation

def load_kb() -> List[Dict[str, Any]]:
    if not config.KB_PATH.exists():
        return []
    return json.loads(config.KB_PATH.read_text(encoding="utf-8"))


def suggest_kb(req_text: str, category: str, kb: List[Dict[str, Any]], k: int = 3) -> List[Dict[str, Any]]:
    req_tokens = set(tokens(req_text, drop_units=True))
    scored = []
    for item in kb:
        words = re.findall(r"[A-Za-z0-9]+", " ".join(item.get("keywords", [])) + " " + item["title"])
        readable = {}
        for w in words:
            readable.setdefault(stem(w.lower()), w.lower())
        item_tokens = set(tokens(" ".join(words)))
        shared = {readable.get(t, t) for t in req_tokens & item_tokens}
        cat_match = category in item.get("categories", [])
        score = 2 * cat_match + len(shared)
        if score <= 0 or not shared:
            continue
        reasons = []
        if cat_match:
            reasons.append(f"category {category}")
        reasons.append("shared terms: " + ", ".join(sorted(shared)))
        scored.append({**item, "score": score, "reason": "; ".join(reasons)})
    return sorted(scored, key=lambda x: -x["score"])[:k]


def draft_revision(clause_no: str, req_text: str, constraint: Dict[str, Any], kb_items: List[Dict[str, Any]]) -> str:
    if constraint.get("type") == "numeric":
        target = f"{OP_SYMBOL[constraint['operator']]} {format_value(constraint['value'], constraint['unit'])}"
        lead = (f"In response to Clause {clause_no}, the Tenderer confirms that the "
                f"{constraint['field'].lower()} will be {target}, as required.")
    else:
        lead = f"In response to Clause {clause_no}, the Tenderer provides the following: {req_text}"
    body = "\n\n".join(item["content"] for item in kb_items)
    basis = ", ".join(f"{item['id']} {item['title']}" for item in kb_items) or "no knowledge base reference"
    return f"{lead}\n\n{body}\n\n[Basis: {basis}. Template draft - review and edit before inserting into the bid.]".strip()

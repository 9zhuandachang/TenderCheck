"""Service layer between the Streamlit pages and the engine/store.

Pages call these functions to submit jobs, read results and record reviewer actions; nothing here
imports Streamlit.
"""

from collections import Counter
import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from . import config, engine, jobs, real_backend, store
from .config import CLOSED, COMPLIANT, CONFIRMED, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE, UNREVIEWED


class ServiceError(Exception):
    """A user-facing error explaining why an action cannot be performed."""


# ================================================================= documents

def add_document(project_id: int, category: str, filename: str, data: bytes,
                 background: bool = True) -> Tuple[int, bool]:
    """Store an uploaded PDF as a new document version and parse it (in a background job by default).

    Returns (document id, created); an identical file already in the project is not added again.
    """
    if category not in config.DOC_CATEGORIES:
        raise ServiceError(f"Unknown document category: {category}")
    if not data.startswith(b"%PDF"):
        raise ServiceError(f"{filename} is not a PDF file.")
    sha = hashlib.sha256(data).hexdigest()
    existing = store.find_document_by_hash(project_id, sha)
    if existing:
        return existing["id"], False

    same = store.list_documents(project_id, category)
    version = len(same) + 1
    label = {"tender": f"Tender v{version}", "addendum": f"Addendum {version}", "bid": f"Bid v{version}"}[category]
    folder = store.project_dir(project_id) / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    path = folder / f"{category}_v{version}_{safe}"
    path.write_bytes(data)

    supersedes = None
    if category in ("bid", "tender") and same:
        supersedes = same[-1]["id"]
        # Only one version of the bid (and of the tender) takes part in a review round.
        store.execute("UPDATE documents SET selected=0 WHERE project_id=? AND category=?", (project_id, category))
    doc_id = store.insert("documents", {
        "project_id": project_id,
        "category": category,
        "label": label,
        "version": version,
        "filename": filename,
        "path": str(path),
        "sha256": sha,
        "selected": 1,
        "supersedes_id": supersedes,
        "parse_status": "Pending",
        "uploaded_at": store.now(),
    })
    store.log_action(project_id, "upload_document", payload={"doc_id": doc_id, "label": label, "filename": filename})
    if background:
        parse_document(doc_id)
    else:
        _parse_job(_no_progress, doc_id)
    return doc_id, True


def _no_progress(*_args: Any, **_kwargs: Any) -> None:
    pass


def parse_document(doc_id: int) -> int:
    doc = store.get_document(doc_id)
    return jobs.submit(doc["project_id"], f"parse:{doc_id}", _parse_job, doc_id)


def _parse_job(progress, doc_id: int) -> Dict[str, Any]:
    doc = store.get_document(doc_id)
    store.update("documents", doc_id, {"parse_status": "Parsing", "parse_error": None})
    try:
        out = engine.parse_document(doc["path"], lambda d, t: progress(f"Parsed {d}/{t} pages", d, t))
    except Exception as exc:
        store.update("documents", doc_id, {"parse_status": "Failed", "parse_error": str(exc)})
        raise
    status = "Parsed" if not out["failed_pages"] else "Parsed with warnings"
    store.update("documents", doc_id, {"pages": out["pages"], "failed_pages": out["failed_pages"], "parse_status": status})
    return out


def set_document_selected(doc_id: int, selected: bool) -> None:
    doc = store.get_document(doc_id)
    if selected and doc["category"] in ("bid", "tender"):
        store.execute("UPDATE documents SET selected=0 WHERE project_id=? AND category=?",
                      (doc["project_id"], doc["category"]))
    store.update("documents", doc_id, {"selected": int(selected)})


def package_pages(project_id: int) -> int:
    return sum(d["pages"] or 0 for d in store.list_documents(project_id, selected_only=True))


def documents_by_id(project_id: int) -> Dict[int, Dict[str, Any]]:
    return {d["id"]: d for d in store.list_documents(project_id)}


# ================================================================= requirements

def extract_requirements(project_id: int) -> int:
    if store.list_rounds(project_id):
        raise ServiceError("Requirements are already used by a review round. Edit or add requirements instead.")
    return jobs.submit(project_id, "extract", _extract_job, project_id)


def _new_requirement(project_id: int, clause_no: str, text: str, origin: str, doc_id: Optional[int],
                     page: Optional[int], category: Optional[str] = None, **extra: Any) -> int:
    cls = engine.classify(text)
    category = category if category in config.CATEGORIES else cls["category"]
    constraint = engine.extract_constraint(text, category)
    return store.insert("requirements", {
        "project_id": project_id,
        "clause_no": clause_no,
        "text": text,
        "category": category,
        "category_score": cls["score"],
        "constraint": constraint,
        "source_doc_id": doc_id,
        "page": page,
        "origin": origin,
        "original": {"text": text, "category": category, "constraint": constraint, "page": page},
        "created_at": store.now(),
        **extra,
    })


def _extract_job(progress, project_id: int) -> Dict[str, Any]:
    tenders = store.list_documents(project_id, "tender", selected_only=True)
    addenda = store.list_documents(project_id, "addendum", selected_only=True)
    if not tenders:
        raise ServiceError("Upload a tender document first.")

    cn_counts = real_backend.extract_chinese_project_requirements(project_id, progress=progress)
    if cn_counts is not None:
        return cn_counts

    store.execute("DELETE FROM requirements WHERE project_id=? AND origin IN ('extracted','addendum')", (project_id,))
    store.execute("DELETE FROM drawings WHERE project_id=? AND source='extracted'", (project_id,))

    docs = tenders + addenda
    seq = 0
    counts = {"requirements": 0, "amendments": 0, "drawings": 0}
    for i, doc in enumerate(docs, start=1):
        progress(f"Reading {doc['label']} ({i}/{len(docs)})", i - 1, len(docs))
        texts = engine.load_page_texts(doc["path"])
        for row in engine.extract_drawing_register(texts):
            seq += 1
            store.insert("drawings", {"project_id": project_id, "doc_id": doc["id"], "source": "extracted",
                                      "seq": seq, **row})
            counts["drawings"] += 1
        if doc["category"] == "tender":
            for item in engine.extract_requirements(texts):
                _new_requirement(project_id, item["clause_no"], item["text"], "extracted", doc["id"], item["page"])
                counts["requirements"] += 1
            continue
        for change in engine.extract_addendum_changes(texts):
            target = store.query_one(
                "SELECT * FROM requirements WHERE project_id=? AND clause_no=? AND origin='extracted'",
                (project_id, change["clause_no"]),
            )
            if change["kind"] == "amend" and target:
                store.update("requirements", target["id"], {
                    "amended_by_doc_id": doc["id"],
                    "amendment_page": change["page"],
                    "amendment_text": change["text"],
                    "amendment_status": "pending",
                })
            else:
                _new_requirement(project_id, change["clause_no"], change["text"], "addendum", doc["id"],
                                 change["page"], amended_by_doc_id=doc["id"], amendment_page=change["page"],
                                 amendment_text=change["text"], amendment_status="pending")
            counts["amendments"] += 1
    store.update("projects", project_id, {"req_confirmed_at": None})
    progress("Extraction complete", len(docs), len(docs))
    store.log_action(project_id, "extract_requirements", payload=counts)
    return counts


def decide_amendment(req_id: int, accept: bool, actor: str, role: str, note: str = "") -> None:
    req = store.get_requirement(req_id)
    if req["amendment_status"] != "pending":
        return
    if accept and req["origin"] != "addendum":
        constraint = engine.extract_constraint(req["amendment_text"], req["category"])
        store.update("requirements", req_id, {
            "text": req["amendment_text"],
            "constraint": constraint,
            "amendment_status": "accepted",
        })
    elif accept:
        store.update("requirements", req_id, {"amendment_status": "accepted"})
    else:
        values: Dict[str, Any] = {"amendment_status": "rejected"}
        if req["origin"] == "addendum":
            values.update({"applicable": 0, "na_reason": note or "Addendum clause not applied"})
        store.update("requirements", req_id, values)
    store.log_action(req["project_id"], "accept_amendment" if accept else "reject_amendment", actor, role, note,
                     {"clause_no": req["clause_no"], "amendment_text": req["amendment_text"]},
                     requirement_id=req_id)


def confirm_requirements(project_id: int, actor: str, role: str) -> None:
    if store.pending_amendments(project_id):
        raise ServiceError("Accept or reject all addendum changes before confirming the list.")
    if not store.reviewable_requirements(project_id):
        raise ServiceError("There are no applicable requirements to confirm.")
    store.update("projects", project_id, {"req_confirmed_at": store.now()})
    store.log_action(project_id, "confirm_requirements", actor, role,
                     payload={"count": len(store.reviewable_requirements(project_id))})


def update_requirement(req_id: int, changes: Dict[str, Any], reason: str, actor: str, role: str) -> None:
    """Apply reviewer corrections; the original values stay in original_json and the audit log."""
    req = store.get_requirement(req_id)
    changes = {k: v for k, v in changes.items() if req.get(k) != v}
    if not changes:
        return
    if not reason.strip():
        raise ServiceError("Give a reason for the change.")
    if "applicable" in changes and not changes["applicable"] and not (changes.get("na_reason") or req.get("na_reason")):
        raise ServiceError(f"Clause {req['clause_no']}: give a reason for marking it not applicable.")
    if "text" in changes and "constraint" not in changes:
        changes["constraint"] = engine.extract_constraint(changes["text"], changes.get("category", req["category"]))
    store.update("requirements", req_id, changes)
    store.log_action(req["project_id"], "edit_requirement", actor, role, reason,
                     {"before": {k: req.get(k) for k in changes}, "after": changes}, requirement_id=req_id)
    rnd = store.latest_round(req["project_id"])
    if rnd and not rnd["signed_at"] and store.result_for(rnd["id"], req_id):
        rereview_requirement(rnd["id"], req_id)


def add_requirement(project_id: int, clause_no: str, text: str, category: Optional[str], doc_id: Optional[int],
                    page: Optional[int], reason: str, actor: str, role: str, origin: str = "manual") -> int:
    if not clause_no.strip() or not text.strip():
        raise ServiceError("Clause number and requirement text are required.")
    req_id = _new_requirement(project_id, clause_no.strip(), text.strip(), origin, doc_id, page, category)
    store.log_action(project_id, "add_requirement", actor, role, reason, {"clause_no": clause_no},
                     requirement_id=req_id)
    return req_id


def import_requirements(project_id: int, rows: List[Dict[str, Any]], actor: str, role: str) -> int:
    tender = next(iter(store.list_documents(project_id, "tender", selected_only=True)), None)
    count = 0
    for row in rows:
        text = str(row.get("text") or "").strip()
        clause = str(row.get("clause_no") or "").strip()
        if not text or not clause:
            continue
        page = row.get("page")
        page = int(page) if page not in (None, "") else None
        _new_requirement(project_id, clause, text, "imported", tender["id"] if tender else None, page,
                         row.get("category"))
        count += 1
    store.log_action(project_id, "import_requirements", actor, role, payload={"count": count})
    return count


def rereview_requirement(round_id: int, req_id: int) -> None:
    """Re-run the rule for one requirement after a correction, keeping the system's original verdict."""
    rnd = store.get_round(round_id)
    req = store.get_requirement(req_id)
    if req["origin"] == "drawing":
        return
    existing = store.result_for(round_id, req_id)
    if existing and (existing.get("chain") or {}).get("is_real_backend"):
        out = real_backend.rereview_real_backend_result(req, existing)
        if out:
            store.update("results", existing["id"], {
                "verdict": out["verdict"], "rule_id": out["rule_id"], "explanation": out["explanation"],
                "fields": out["fields"], "evidence": out["evidence"], "chain": out["chain"],
                "corrected": 1, "human_status": UNREVIEWED, "updated_at": store.now(),
            })
            return
    bid = store.get_document(rnd["bid_doc_id"])
    index = engine.BidIndex(engine.load_page_texts(bid["path"]), bid["failed_pages"])
    out = engine.review_requirement(req, index)
    store.update("results", existing["id"], {
        "verdict": out["verdict"], "rule_id": out["rule_id"], "explanation": out["explanation"],
        "fields": out["fields"], "evidence": out["evidence"], "chain": out["chain"],
        "corrected": 1, "human_status": UNREVIEWED, "updated_at": store.now(),
    })


# ================================================================= review rounds

def review_readiness(project_id: int) -> List[Dict[str, Any]]:
    docs = store.list_documents(project_id, selected_only=True)
    tender_ok = any(d["category"] == "tender" and d["pages"] for d in docs)
    bid_ok = any(d["category"] == "bid" and d["pages"] for d in docs)
    pages = package_pages(project_id)
    unparsed = [d["label"] for d in docs if d["parse_status"] in ("Pending", "Parsing", "Failed")]
    project = store.get_project(project_id)
    return [
        {"item": "Tender document uploaded and parsed", "ok": tender_ok},
        {"item": "Bid document uploaded and parsed", "ok": bid_ok},
        {"item": "All selected documents parsed", "ok": not unparsed,
         "detail": ", ".join(unparsed) if unparsed else ""},
        {"item": f"Package within {config.MAX_PACKAGE_PAGES} pages", "ok": 0 < pages <= config.MAX_PACKAGE_PAGES,
         "detail": f"{pages} pages selected"},
        {"item": "Addendum changes decided", "ok": not store.pending_amendments(project_id)},
        {"item": "Requirement list confirmed", "ok": bool(project["req_confirmed_at"])},
    ]


def start_review(project_id: int, actor: str, role: str) -> int:
    blockers = [r["item"] for r in review_readiness(project_id) if not r["ok"]]
    if blockers:
        raise ServiceError("Not ready to review: " + "; ".join(blockers))
    store.log_action(project_id, "start_review", actor, role)
    return jobs.submit(project_id, "review", _review_job, project_id)


def _review_job(progress, project_id: int) -> Dict[str, Any]:
    bid = store.selected_bid(project_id)
    docs = store.list_documents(project_id, selected_only=True)
    prev = store.latest_round(project_id)
    project = store.get_project(project_id)
    is_s604 = real_backend.is_s604_project(project)
    if is_s604 and prev and prev["bid_doc_id"] == bid["id"]:
        round_id = prev["id"]
        store.execute("DELETE FROM results WHERE round_id=?", (round_id,))
        store.update("rounds", round_id, {"status": "running", "created_at": store.now()})
    else:
        no = (store.query_one("SELECT MAX(no) AS n FROM rounds WHERE project_id=?", (project_id,))["n"] or 0) + 1
        round_id = store.insert("rounds", {
            "project_id": project_id, "no": no, "bid_doc_id": bid["id"], "doc_ids": [d["id"] for d in docs],
            "engine": config.ENGINE_NAME, "rule_version": config.RULE_VERSION, "status": "running",
            "created_at": store.now(),
        })
    try:
        reqs = store.reviewable_requirements(project_id)
        total = len(reqs) + 1
        if is_s604:
            evaluated = real_backend.evaluate_all_s604_clauses(progress=progress)
            for i, req in enumerate(reqs, start=1):
                cid = req["clause_no"]
                if cid in evaluated:
                    out = evaluated[cid]
                else:
                    out = {
                        "verdict": INSUFFICIENT,
                        "rule_id": "UNSUPPORTED",
                        "explanation": "未在预设索引中匹配到该新增条款。",
                        "fields": [{"field": "Requirement", "required": req["text"], "actual": "待复核", "result": INSUFFICIENT}],
                        "evidence": [],
                        "chain": {"is_real_backend": True, "sub_verdict": "ATTACHMENT_OR_FORMULA"},
                    }
                status = CONFIRMED if out["verdict"] == COMPLIANT else UNREVIEWED
                _insert_result(round_id, req["id"], out, human_status=status)
            store.update("rounds", round_id, {"status": "done"})
            progress("Review complete", total, total)
            return {"round_id": round_id, "previous_round_id": prev["id"] if prev and prev["id"] != round_id else None}

        cn_evaluated = real_backend.evaluate_chinese_project_clauses(project_id, reqs, progress=progress)
        if cn_evaluated is not None:
            for req in reqs:
                out = cn_evaluated.get(req["id"])
                if not out:
                    continue
                if out.get("constraint"):
                    store.update("requirements", req["id"], {"constraint": out["constraint"]})
                status = CONFIRMED if out["verdict"] == COMPLIANT else UNREVIEWED
                _insert_result(round_id, req["id"], out, human_status=status)
            _carry_remediation_state(project_id, round_id)
            store.update("rounds", round_id, {"status": "done"})
            progress("Review complete", total, total)
            return {"round_id": round_id, "previous_round_id": prev["id"] if prev and prev["id"] != round_id else None}

        texts = engine.load_page_texts(bid["path"])
        index = engine.BidIndex(texts, bid["failed_pages"])
        for i, req in enumerate(reqs, start=1):
            progress(f"Checked {i - 1}/{len(reqs)} clauses", i - 1, total)
            out = engine.review_requirement(req, index)
            _insert_result(round_id, req["id"], out)
        progress("Checking drawing references", len(reqs), total)
        _review_drawings(project_id, round_id, texts)
        _carry_remediation_state(project_id, round_id)
        store.update("rounds", round_id, {"status": "done"})
    except Exception:
        store.update("rounds", round_id, {"status": "failed"})
        raise
    progress("Review complete", total, total)
    return {"round_id": round_id, "previous_round_id": prev["id"] if prev else None}


def _insert_result(round_id: int, req_id: int, out: Dict[str, Any], human_status: str = UNREVIEWED) -> int:
    return store.insert("results", {
        "round_id": round_id, "requirement_id": req_id,
        "verdict": out["verdict"], "original_verdict": out["verdict"], "rule_id": out["rule_id"],
        "explanation": out["explanation"], "fields": out["fields"], "evidence": out["evidence"],
        "chain": out["chain"], "human_status": human_status, "updated_at": store.now(),
    })


def _review_drawings(project_id: int, round_id: int, bid_texts: List[str]) -> None:
    rows = store.query("SELECT * FROM drawings WHERE project_id=? ORDER BY seq, id", (project_id,))
    findings = engine.check_drawings(rows, engine.extract_drawing_refs(bid_texts))
    existing = {r["clause_no"]: r for r in store.query(
        "SELECT * FROM requirements WHERE project_id=? AND origin='drawing'", (project_id,))}
    severity = {COMPLIANT: 0, INSUFFICIENT: 1, NON_COMPLIANT: 2}
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for f in findings:
        by_key.setdefault(f["key"], []).append(f)
    for key, group in by_key.items():
        worst = max(group, key=lambda f: severity[f["verdict"]])
        if worst["verdict"] == COMPLIANT and key not in existing:
            continue  # only report drawings that had an issue at some point
        req = existing.get(key)
        if req is not None and not req["applicable"]:
            continue  # the reviewer marked this drawing item not applicable
        if req is None:
            req_id = store.insert("requirements", {
                "project_id": project_id, "clause_no": key, "text": worst["requirement"],
                "category": "Consistency", "constraint": {"type": "drawing", "field": "Drawing revision"},
                "source_doc_id": worst.get("tender_doc_id"), "page": worst.get("tender_page"),
                "origin": "drawing", "original": {"text": worst["requirement"]}, "created_at": store.now(),
            })
        else:
            req_id = req["id"]
        evidence = [{"page": f["bid_page"], "snippet": f.get("snippet", ""), "role": "Drawing reference"}
                    for f in group if f.get("bid_page")]
        explanation = " ".join(f["explanation"] for f in group)
        _insert_result(round_id, req_id, {
            "verdict": worst["verdict"], "rule_id": "R04_DRAWING_REVISION", "explanation": explanation,
            "fields": [{"field": "Drawing revision", "required": "Current revision in register",
                        "actual": "; ".join(f["kind"] for f in group), "result": worst["verdict"]}],
            "evidence": evidence,
            "chain": {"rule": {"rule_id": "R04_DRAWING_REVISION", "rule_version": config.RULE_VERSION,
                               "trace": " | ".join(f"{f['kind']}: {f['explanation']}" for f in group)}},
        })
    for key, req in existing.items():
        if key not in by_key and req["applicable"]:
            _insert_result(round_id, req["id"], {
                "verdict": COMPLIANT, "rule_id": "R04_DRAWING_REVISION",
                "explanation": "The issue is no longer detected in this bid version.",
                "fields": [{"field": "Drawing revision", "required": "Current revision in register",
                            "actual": "No conflicting reference", "result": COMPLIANT}],
                "evidence": [],
                "chain": {"rule": {"rule_id": "R04_DRAWING_REVISION", "rule_version": config.RULE_VERSION,
                                   "trace": "no conflicting reference -> PASS"}},
            })


def _carry_remediation_state(project_id: int, round_id: int) -> None:
    """Carry reviewer decisions into a new round where nothing changed, and update remediation items."""
    rnd = store.get_round(round_id)
    prev = store.previous_round(rnd)
    if prev:
        before = {r["requirement_id"]: r for r in store.list_results(prev["id"])}
        for res in store.list_results(round_id):
            old = before.get(res["requirement_id"])
            if old and old["human_status"] == CONFIRMED and old["verdict"] == res["verdict"]:
                store.update("results", res["id"], {
                    "human_status": CONFIRMED,
                    "note": f"Carried forward from Round {prev['no']} (verdict unchanged).",
                })
    for item in store.list_remediations(project_id, open_only=True):
        res = store.result_for(round_id, item["requirement_id"])
        if res is None:
            continue
        if item["status"] == TO_REINSPECT and res["verdict"] != COMPLIANT:
            store.update("remediations", item["id"], {"status": TO_REMEDIATE, "updated_at": store.now()})
            status = TO_REMEDIATE
        else:
            status = item["status"]
        store.update("results", res["id"], {"human_status": status})


COMPARISON_CLASSES = ["Resolved", "Persisting", "New", "To confirm", "Unchanged"]


def compare_rounds(round_id: int) -> List[Dict[str, Any]]:
    rnd = store.get_round(round_id)
    prev = store.previous_round(rnd)
    before = {r["requirement_id"]: r["verdict"] for r in store.list_results(prev["id"])} if prev else {}
    rows = []
    for r in store.list_results(round_id):
        old, new = before.get(r["requirement_id"]), r["verdict"]
        if new == INSUFFICIENT:
            cls = "To confirm"
        elif old is None:
            cls = "New" if new != COMPLIANT else "Unchanged"
        elif old != COMPLIANT and new == COMPLIANT:
            cls = "Resolved"
        elif old != COMPLIANT:
            cls = "Persisting"
        elif new != COMPLIANT:
            cls = "New"
        else:
            cls = "Unchanged"
        rows.append({**r, "previous_verdict": old, "change": cls})
    return rows


# ================================================================= reviewer actions on results

def _editable_result(result_id: int) -> Dict[str, Any]:
    res = store.get_result(result_id)
    rnd = store.get_round(res["round_id"])
    if rnd["signed_at"]:
        raise ServiceError(f"Round {rnd['no']} is signed off and read-only. Start a new round to make changes.")
    return res


def _touch(res: Dict[str, Any], values: Dict[str, Any], seconds: float) -> None:
    values["review_seconds"] = (res["review_seconds"] or 0) + max(0.0, min(seconds, 1800))
    values["updated_at"] = store.now()
    store.update("results", res["id"], values)


def _project_of(res: Dict[str, Any]) -> int:
    return store.get_round(res["round_id"])["project_id"]


def confirm_result(result_id: int, actor: str, role: str, note: str = "", seconds: float = 0) -> str:
    res = _editable_result(result_id)
    project_id = _project_of(res)
    status = CONFIRMED
    item = store.open_remediation_for(res["requirement_id"])
    if item and item["status"] == TO_REINSPECT and res["verdict"] == COMPLIANT:
        store.update("remediations", item["id"], {"status": CLOSED, "closed_round_id": res["round_id"],
                                                  "updated_at": store.now()})
        status = CLOSED
    elif item:
        raise ServiceError("This clause has an open remediation item. Re-inspect a revised bid first.")
    _touch(res, {"human_status": status, "note": note or res["note"]}, seconds)
    store.log_action(project_id, "confirm_result", actor, role, note, {"verdict": res["verdict"], "status": status},
                     res["round_id"], res["requirement_id"], result_id)
    return status


def correct_result(result_id: int, constraint: Dict[str, Any], actual_value: Optional[float],
                   actual_unit: Optional[str], reason: str, actor: str, role: str, seconds: float = 0) -> None:
    """Re-run the rule with corrected requirement and/or bid values."""
    if not reason.strip():
        raise ServiceError("Give a reason for the correction.")
    res = _editable_result(result_id)
    project_id = _project_of(res)
    req = store.get_requirement(res["requirement_id"])
    if constraint.get("type") != "numeric":
        raise ServiceError("Only numeric requirements can be corrected field by field; add evidence instead.")

    before = {"constraint": req["constraint"], "fields": res["fields"], "verdict": res["verdict"]}
    if (res.get("chain") or {}).get("is_real_backend"):
        out = real_backend.rereview_real_backend_result(
            req, res, corrected_constraint=constraint, actual_value=actual_value, actual_unit=actual_unit
        )
        if out:
            if constraint != req["constraint"]:
                store.update("requirements", req["id"], {"constraint": constraint})
            _touch(
                res,
                {
                    "verdict": out["verdict"],
                    "fields": out["fields"],
                    "chain": out["chain"],
                    "corrected": 1,
                    "explanation": out["explanation"],
                    "rule_id": out["rule_id"],
                },
                seconds,
            )
            store.log_action(
                project_id,
                "correct_result",
                actor,
                role,
                reason,
                {"before": before, "after": {"constraint": constraint, "fields": out["fields"], "verdict": out["verdict"]}},
                res["round_id"],
                res["requirement_id"],
                result_id,
            )
            return

    rule = engine.evaluate_rule(constraint, {"value": actual_value, "unit": actual_unit or constraint["unit"]})
    fields = [{
        "field": constraint["field"],
        "required": f"{engine.OP_SYMBOL[constraint['operator']]} "
                    f"{engine.format_value(constraint['value'], constraint['unit'])}",
        "actual": engine.format_value(actual_value, actual_unit or constraint["unit"]),
        "result": rule["verdict"],
    }]
    chain = dict(res["chain"] or {})
    chain["rule"] = {"rule_id": rule["rule_id"], "rule_version": config.RULE_VERSION,
                     "trace": rule["trace"] + "  [re-run after reviewer correction]"}
    if constraint != req["constraint"]:
        store.update("requirements", req["id"], {"constraint": constraint})
    _touch(res, {"verdict": rule["verdict"], "fields": fields, "chain": chain, "corrected": 1,
                 "explanation": rule["explanation"], "rule_id": rule["rule_id"]}, seconds)
    store.log_action(project_id, "correct_result", actor, role, reason,
                     {"before": before, "after": {"constraint": constraint, "fields": fields, "verdict": rule["verdict"]}},
                     res["round_id"], res["requirement_id"], result_id)


def add_evidence(result_id: int, page: int, note: str, actor: str, role: str, seconds: float = 0) -> None:
    res = _editable_result(result_id)
    evidence = list(res["evidence"] or [])
    evidence.insert(0, {"page": int(page), "snippet": note or "Added by reviewer", "role": f"Added by {actor or role}"})
    _touch(res, {"evidence": evidence}, seconds)
    store.log_action(_project_of(res), "add_evidence", actor, role, note, {"page": page},
                     res["round_id"], res["requirement_id"], result_id)


def mark_not_applicable(result_id: int, reason: str, actor: str, role: str, seconds: float = 0) -> None:
    if not reason.strip():
        raise ServiceError("Give a reason for marking the clause not applicable.")
    res = _editable_result(result_id)
    store.update("requirements", res["requirement_id"], {"applicable": 0, "na_reason": reason})
    _touch(res, {"human_status": CONFIRMED, "note": reason}, seconds)
    store.log_action(_project_of(res), "mark_not_applicable", actor, role, reason, {},
                     res["round_id"], res["requirement_id"], result_id)


def send_to_remediation(result_id: int, note: str, actor: str, role: str, seconds: float = 0) -> int:
    res = _editable_result(result_id)
    if res["verdict"] == COMPLIANT:
        raise ServiceError("The clause is compliant; nothing to remediate.")
    project_id = _project_of(res)
    item = store.open_remediation_for(res["requirement_id"])
    if item is None:
        rem_id = store.insert("remediations", {
            "project_id": project_id, "requirement_id": res["requirement_id"], "opened_round_id": res["round_id"],
            "issue": (note + " " if note else "") + (res["explanation"] or ""), "status": TO_REMEDIATE,
            "updated_at": store.now(),
        })
    else:
        rem_id = item["id"]
    _touch(res, {"human_status": TO_REMEDIATE, "note": note or res["note"]}, seconds)
    store.log_action(project_id, "send_to_remediation", actor, role, note, {"remediation_id": rem_id},
                     res["round_id"], res["requirement_id"], result_id)
    return rem_id


# ================================================================= remediation

def save_draft(rem_id: int, kb_refs: List[str], draft: str, target: str, actor: str, role: str) -> None:
    item = store.query_one("SELECT * FROM remediations WHERE id=?", (rem_id,))
    store.update("remediations", rem_id, {"kb_refs": kb_refs, "draft": draft, "target": target,
                                          "updated_at": store.now()})
    store.log_action(item["project_id"], "save_draft", actor, role, payload={"kb_refs": kb_refs, "target": target},
                     requirement_id=item["requirement_id"])


def link_revised_bid(project_id: int, doc_id: int, actor: str, role: str) -> int:
    """A new bid version moves every item awaiting remediation to re-inspection."""
    items = [i for i in store.list_remediations(project_id, open_only=True) if i["status"] == TO_REMEDIATE]
    rnd = store.latest_round(project_id)
    for item in items:
        store.update("remediations", item["id"], {"status": TO_REINSPECT, "target_doc_id": doc_id,
                                                  "updated_at": store.now()})
        if rnd and not rnd["signed_at"]:
            res = store.result_for(rnd["id"], item["requirement_id"])
            if res:
                store.update("results", res["id"], {"human_status": TO_REINSPECT})
    store.log_action(project_id, "link_revised_bid", actor, role, payload={"doc_id": doc_id, "items": len(items)})
    return len(items)


def start_reinspection(project_id: int, actor: str, role: str) -> int:
    rnd = store.latest_round(project_id)
    bid = store.selected_bid(project_id)
    if rnd and bid and rnd["bid_doc_id"] == bid["id"]:
        raise ServiceError("Upload a revised bid before re-inspecting.")
    return start_review(project_id, actor, role)


# ================================================================= report and sign-off

def round_summary(round_id: int) -> Dict[str, Any]:
    results = store.list_results(round_id)
    applicable = [r for r in results if r["applicable"]]
    non_passing_list = [r for r in applicable if r["verdict"] != COMPLIANT]
    sub_counts = Counter(
        (r.get("chain") or {}).get("sub_verdict")
        for r in applicable
        if (r.get("chain") or {}).get("sub_verdict")
    )
    return {
        "total": len(results),
        "applicable": len(applicable),
        "not_applicable": len(results) - len(applicable),
        "unreviewed": sum(r["human_status"] == UNREVIEWED for r in applicable),
        "reviewed": sum(r["human_status"] != UNREVIEWED for r in applicable),
        "compliant": sum(r["verdict"] == COMPLIANT for r in applicable),
        "non_compliant": sum(r["verdict"] == NON_COMPLIANT for r in applicable),
        "insufficient": sum(r["verdict"] == INSUFFICIENT for r in applicable),
        "non_passing": len(non_passing_list),
        "non_passing_reviewed": sum(r["human_status"] != UNREVIEWED for r in non_passing_list),
        "non_passing_unreviewed": sum(r["human_status"] == UNREVIEWED for r in non_passing_list),
        "to_remediate": sum(r["human_status"] in (TO_REMEDIATE, TO_REINSPECT) for r in applicable),
        "review_seconds": sum(r["review_seconds"] or 0 for r in results),
        "sub_counts": dict(sub_counts),
        "is_real_backend": any((r.get("chain") or {}).get("is_real_backend") for r in applicable),
    }


def signoff_blockers(round_id: int) -> List[str]:
    rnd = store.get_round(round_id)
    blockers = []
    if rnd["status"] != "done":
        blockers.append("The review round has not finished.")
    latest = store.latest_round(rnd["project_id"])
    if latest and latest["id"] != round_id:
        blockers.append(f"A newer round (Round {latest['no']}) exists; sign off the latest round.")
    summary = round_summary(round_id)
    if summary["unreviewed"]:
        blockers.append(f"{summary['unreviewed']} applicable clause(s) have no reviewer decision yet.")
    awaiting = [r["clause_no"] for r in store.list_results(round_id)
                if r["applicable"] and r["human_status"] == TO_REINSPECT]
    if awaiting:
        blockers.append(f"Re-inspected clause(s) awaiting confirmation: {', '.join(awaiting)}.")
    if store.pending_amendments(rnd["project_id"]):
        blockers.append("Addendum changes are still pending.")
    return blockers


def sign_round(round_id: int, signer: str, comment: str, role: str) -> None:
    if role != "Manager":
        raise ServiceError("Only the Manager role can sign off.")
    if not signer.strip():
        raise ServiceError("Enter the signer's name.")
    blockers = signoff_blockers(round_id)
    if blockers:
        raise ServiceError(" ".join(blockers))
    rnd = store.get_round(round_id)
    store.update("rounds", round_id, {"signed_at": store.now(), "signer": signer, "sign_comment": comment})
    store.log_action(rnd["project_id"], "sign_off", signer, role, comment, {"round": rnd["no"]}, round_id)


# ================================================================= evaluation view

def set_known_issues(project_id: int, rows: List[Dict[str, Any]]) -> int:
    store.execute("DELETE FROM known_issues WHERE project_id=?", (project_id,))
    n = 0
    for row in rows:
        clause = str(row.get("clause_no") or "").strip()
        if clause:
            store.insert("known_issues", {"project_id": project_id, "clause_no": clause,
                                          "expected": row.get("expected") or NON_COMPLIANT,
                                          "note": row.get("note") or ""})
            n += 1
    return n


def evaluate_round(round_id: int) -> Dict[str, Any]:
    rnd = store.get_round(round_id)
    known = store.query("SELECT * FROM known_issues WHERE project_id=?", (rnd["project_id"],))
    results = {r["clause_no"]: r for r in store.list_results(round_id)}
    flagged = {c for c, r in results.items() if r["verdict"] != COMPLIANT}
    expected = {k["clause_no"] for k in known}
    rows = []
    for k in known:
        r = results.get(k["clause_no"])
        rows.append({"Clause": k["clause_no"], "Known issue": k["note"], "Expected": k["expected"],
                     "System verdict": r["verdict"] if r else "Not reviewed",
                     "Outcome": "Hit" if k["clause_no"] in flagged else "Missed"})
    false_alarms = sorted(flagged - expected)
    amended = store.query(
        "SELECT clause_no, amendment_status FROM requirements WHERE project_id=? AND amended_by_doc_id IS NOT NULL",
        (rnd["project_id"],),
    )
    return {
        "rows": rows,
        "hits": sum(r["Outcome"] == "Hit" for r in rows),
        "known": len(rows),
        "false_alarms": false_alarms,
        "amendments": amended,
        "amendments_applied": sum(a["amendment_status"] == "accepted" for a in amended),
    }

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
from src.rules.impact_graph import ClauseImpactGraph


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
    safe = re.sub(r"[^A-Za-z0-9._\-一-鿿??()]+", "_", filename)
    path = folder / f"{category}_v{version}_{safe}"
    path.write_bytes(data)

    supersedes = None
    if category in ("bid", "tender") and same:
        same_name_docs = [d for d in same if d.get("filename") == filename]
        if same_name_docs:
            supersedes = same_name_docs[-1]["id"]
            store.execute(
                "UPDATE documents SET selected=0 WHERE project_id=? AND category=? AND filename=?",
                (project_id, category, filename),
            )
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


def _default_constraint(category: str) -> Dict[str, Any]:
    return {
        "type": "document" if category == "Document" else "presence",
        "clause_type": category,
        "field": category,
    }


def _new_requirement(project_id: int, clause_no: str, text: str, origin: str, doc_id: Optional[int],
                     page: Optional[int], category: Optional[str] = None, **extra: Any) -> int:
    category = category if category in config.CATEGORIES else "Document"
    constraint = extra.pop("constraint", None) or _default_constraint(category)
    return store.insert("requirements", {
        "project_id": project_id,
        "clause_no": clause_no,
        "text": text,
        "category": category,
        "category_score": 1.0,
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
    if not tenders:
        raise ServiceError("Upload a tender document first.")

    cn_counts = real_backend.extract_chinese_project_requirements(project_id, progress=progress)
    if cn_counts is None:
        raise ServiceError("未检测到可解析的中文招标文件正文，请上传包含招标条款的中文 PDF 文件。")
    return cn_counts


def decide_amendment(req_id: int, accept: bool, actor: str, role: str, note: str = "") -> None:
    req = store.get_requirement(req_id)
    if req["amendment_status"] != "pending":
        return
    if accept and req["origin"] != "addendum":
        store.update("requirements", req_id, {
            "text": req["amendment_text"],
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
    """Re-run the canonical rule engine for one requirement after a correction, keeping the system's original verdict."""
    rnd = store.get_round(round_id)
    req = store.get_requirement(req_id)
    if req["origin"] == "drawing":
        return
    existing = store.result_for(round_id, req_id)
    if not existing:
        return
    out = real_backend.rereview_real_backend_result(req, existing)
    if out is None:
        evaluated = real_backend.evaluate_chinese_project_clauses(rnd["project_id"], [req])
        out = (evaluated or {}).get(req["id"])
    if out is None:
        raise ServiceError("无法在当前真实后端主干上重审该条款，请检查投标文件与配置。")
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
        if cn_evaluated is None:
            raise ServiceError("未检测到可审查的中文招标条款或投标文件，已阻断回退至占位引擎。")
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
    except Exception:
        store.update("rounds", round_id, {"status": "failed"})
        raise


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
    """Confirm the existing machine verdict without silently converting Non-compliant/Insufficient to Compliant."""
    res = _editable_result(result_id)
    project_id = _project_of(res)
    status = CONFIRMED
    current_verdict = res["verdict"]

    if current_verdict == COMPLIANT:
        item = store.open_remediation_for(res["requirement_id"])
        if item:
            store.update(
                "remediations",
                item["id"],
                {"status": CLOSED, "closed_round_id": res["round_id"], "updated_at": store.now()},
            )
            status = CLOSED

    updates: Dict[str, Any] = {
        "human_status": status,
        "note": note.strip() or res["note"],
    }
    _touch(res, updates, seconds)
    store.log_action(
        project_id,
        "confirm_result",
        actor,
        role,
        note,
        {"verdict": current_verdict, "status": status},
        res["round_id"],
        res["requirement_id"],
        result_id,
    )
    return status


def approve_manual_pass(result_id: int, reason: str, actor: str, role: str, seconds: float = 0) -> str:
    """Explicitly pass an abstained (`Insufficient evidence`) or Manager-overridden clause with mandatory audit evidence."""
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 4:
        raise ServiceError(
            "人工核验放行必须填写具体核验依据（至少 4 个字符，如：已目视核验第12页公章与法人签字齐全），严禁无依据放行。"
        )
    res = _editable_result(result_id)
    if res["verdict"] == COMPLIANT:
        return confirm_result(result_id, actor, role, note=clean_reason, seconds=seconds)

    if res["verdict"] == NON_COMPLIANT and role != "Manager":
        raise ServiceError(
            "确定性违规或标书前后矛盾项（Non-compliant）严禁普通审标员直接放行；请前往 Remediation 修改投标书重审，或切换 Manager 角色填写特批依据。"
        )

    project_id = _project_of(res)
    before_verdict = res["verdict"]
    item = store.open_remediation_for(res["requirement_id"])
    if item:
        store.update(
            "remediations",
            item["id"],
            {"status": CLOSED, "closed_round_id": res["round_id"], "updated_at": store.now()},
        )

    updated_fields = [{**f, "result": COMPLIANT} for f in (res.get("fields") or [])]
    chain = dict(res.get("chain") or {})
    if chain.get("sub_verdict") and chain.get("sub_verdict") not in ("COMPLIANT", "SCORED"):
        chain.setdefault("original_sub_verdict", chain.get("sub_verdict"))
        chain.setdefault("original_sub_verdict_label", chain.get("sub_verdict_label"))
    chain["sub_verdict"] = "COMPLIANT"
    chain["sub_verdict_label"] = "✓ Manual Verified"
    chain["sub_verdict_color"] = "blue"
    chain["manual_pass_reason"] = clean_reason
    chain["manual_pass_actor"] = f"{actor} ({role})"

    updates: Dict[str, Any] = {
        "verdict": COMPLIANT,
        "human_status": CONFIRMED,
        "fields": updated_fields,
        "chain": chain,
        "corrected": 1,
        "note": clean_reason,
    }
    _touch(res, updates, seconds)
    store.log_action(
        project_id,
        "manual_verify_pass",
        actor,
        role,
        clean_reason,
        {"before_verdict": before_verdict, "after_verdict": COMPLIANT, "status": CONFIRMED},
        res["round_id"],
        res["requirement_id"],
        result_id,
    )
    return CONFIRMED


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


def _build_round_impact_graph(project_id: int, round_id: int) -> ClauseImpactGraph:
    """Build a `ClauseImpactGraph` for all results in `round_id`, resolving document path/ID aliases."""
    doc_sources = real_backend._load_doc_sources()
    for d in store.list_documents(project_id):
        entry = {
            "filename": d.get("filename") or "",
            "label": d.get("label") or d.get("filename") or "",
            "path": d.get("path") or "",
            "sha256": d.get("sha256") or "",
            "pages": int(d.get("pages") or 1),
        }
        doc_sources[f"D_BID_{d['id']}"] = entry
        doc_sources[f"bid_doc_{d['id']}"] = entry
        doc_sources[str(d["id"])] = entry
    return ClauseImpactGraph(store.list_results(round_id), doc_sources=doc_sources)


def preview_page_impact(
    round_id: int,
    doc_path: str,
    page_no: int,
    *,
    exclude_result_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Preview all other clauses in `round_id` that cite `(doc_path, page_no)`."""
    rnd = store.get_round(round_id)
    if not rnd or not doc_path:
        return {"count": 0, "clauses": []}
    graph = _build_round_impact_graph(int(rnd["project_id"]), int(round_id))
    return graph.impact_summary(doc_path, int(page_no), exclude_result_id=exclude_result_id)


def apply_remediation_edit(
    rem_id: int,
    result_id: int,
    ev_index: int,
    modified_text: str,
    note: str,
    actor: str,
    role: str,
    resolve: bool = True,
    doc_path: str = "",
    page_no: int = 1,
) -> Dict[str, Any]:
    """Save in-place modification of bid original text, persist to bid document, and cascade re-evaluation across impacted clauses."""
    if not modified_text.strip():
        raise ServiceError("Modified bid text cannot be empty.")
    item = store.query_one("SELECT * FROM remediations WHERE id=?", (rem_id,))
    if not item:
        raise ServiceError("Remediation item not found.")
    res = _editable_result(result_id)
    project_id = _project_of(res)
    req = store.get_requirement(res["requirement_id"])

    evidence = [dict(e) for e in (res.get("evidence") or [])]
    target_label = "Bid text"
    before_text = ""
    if 0 <= ev_index < len(evidence):
        ev = evidence[ev_index]
        doc_path = doc_path or ev.get("doc_path") or ""
        page_no = int(page_no or ev.get("page") or 1)
        before_text = ev.get("original_text") or ev.get("rag_text") or ev.get("snippet") or ""
        target_label = f"{ev.get('doc_label') or 'Bid'} p.{page_no}"

    if doc_path:
        _cur_txt, orig_pdf_txt = real_backend.read_bid_page_text_with_edits(
            doc_path, page_no, project_id=project_id
        )
        real_backend.save_bid_page_edit(
            doc_path,
            page_no,
            modified_text.strip(),
            orig_pdf_txt or before_text,
            project_id=project_id,
        )

    # Re-run real backend extraction & rule engine on the modified Bid text
    reeval = real_backend.reevaluate_clause_with_modified_bid(
        req=req,
        existing_result=res,
        pdf_path=doc_path,
        page_no=int(page_no),
        modified_text=modified_text.strip(),
    )

    passed_now = reeval["verdict"] == COMPLIANT
    rem_status = CLOSED if (resolve and passed_now) else TO_REMEDIATE
    store.update(
        "remediations",
        rem_id,
        {
            "draft": modified_text.strip(),
            "target": target_label,
            "status": rem_status,
            "closed_round_id": res["round_id"] if rem_status == CLOSED else None,
            "updated_at": store.now(),
        },
    )

    res_updates: Dict[str, Any] = {
        "verdict": reeval["verdict"],
        "rule_id": reeval["rule_id"],
        "explanation": reeval["explanation"],
        "fields": reeval["fields"],
        "evidence": reeval["evidence"],
        "chain": reeval["chain"],
        "corrected": 1,
        "human_status": CONFIRMED if passed_now else TO_REMEDIATE,
        "note": note.strip() or res.get("note") or f"已在 Remediation 修改 {target_label} 原文并重审",
    }
    _touch(res, res_updates, 0.0)

    # Impact-aware cascade re-evaluation for sibling clauses referencing the same (doc_path, page_no)
    impacted_outcomes: List[Dict[str, Any]] = []
    if doc_path:
        graph = _build_round_impact_graph(project_id, int(res["round_id"]))
        siblings = graph.impacted_results(doc_path, int(page_no), exclude_result_id=result_id)
        for sib in siblings:
            sib_req = store.get_requirement(sib["requirement_id"])
            if not sib_req:
                continue
            prev_verdict = str(sib.get("verdict") or "")
            sib_reeval = real_backend.reevaluate_clause_with_modified_bid(
                req=sib_req,
                existing_result=sib,
                pdf_path=doc_path,
                page_no=int(page_no),
                modified_text=modified_text.strip(),
            )
            new_verdict = str(sib_reeval["verdict"])
            sib_passed = new_verdict == COMPLIANT
            regressed = (prev_verdict == COMPLIANT) and (not sib_passed)

            sib_human_status = sib.get("human_status") or UNREVIEWED
            if regressed:
                sib_human_status = TO_REMEDIATE
            elif sib_passed and prev_verdict != COMPLIANT:
                sib_human_status = CONFIRMED
            elif not sib_passed:
                sib_human_status = TO_REMEDIATE

            _touch(
                sib,
                {
                    "verdict": new_verdict,
                    "rule_id": sib_reeval["rule_id"],
                    "explanation": sib_reeval["explanation"],
                    "fields": sib_reeval["fields"],
                    "evidence": sib_reeval["evidence"],
                    "chain": sib_reeval["chain"],
                    "corrected": 1,
                    "human_status": sib_human_status,
                    "note": f"由 {res['clause_no']} 修改 {target_label} 触发联动重审 ({prev_verdict} -> {new_verdict})",
                },
                0.0,
            )

            existing_sib_rem = store.query_one(
                "SELECT * FROM remediations WHERE project_id=? AND requirement_id=? ORDER BY id DESC LIMIT 1",
                (project_id, sib["requirement_id"]),
            )
            if regressed:
                issue_msg = (
                    f"联动重审发现回归：修改 {target_label} 后，本条款从 {prev_verdict} 变为 {new_verdict}。"
                    f"{sib_reeval.get('explanation') or ''}"
                ).strip()
                if existing_sib_rem:
                    store.update(
                        "remediations",
                        existing_sib_rem["id"],
                        {
                            "status": TO_REMEDIATE,
                            "issue": issue_msg,
                            "target": target_label,
                            "closed_round_id": None,
                            "updated_at": store.now(),
                        },
                    )
                else:
                    store.insert(
                        "remediations",
                        {
                            "project_id": project_id,
                            "requirement_id": sib["requirement_id"],
                            "issue": issue_msg,
                            "target": target_label,
                            "status": TO_REMEDIATE,
                            "draft": "",
                            "opened_round_id": res["round_id"],
                            "updated_at": store.now(),
                        },
                    )
            elif sib_passed and resolve and existing_sib_rem and existing_sib_rem.get("status") != CLOSED:
                store.update(
                    "remediations",
                    existing_sib_rem["id"],
                    {
                        "status": CLOSED,
                        "closed_round_id": res["round_id"],
                        "updated_at": store.now(),
                    },
                )

            store.log_action(
                project_id,
                "impact_reevaluate_clause",
                actor,
                role,
                f"Cascade re-evaluation triggered by {res['clause_no']} edit on {target_label}",
                {
                    "trigger_result_id": result_id,
                    "trigger_clause_no": res["clause_no"],
                    "target": target_label,
                    "prev_verdict": prev_verdict,
                    "new_verdict": new_verdict,
                    "regressed": regressed,
                },
                res["round_id"],
                sib["requirement_id"],
                sib["id"],
            )
            impacted_outcomes.append(
                {
                    "result_id": int(sib["id"]),
                    "requirement_id": int(sib["requirement_id"]),
                    "clause_no": str(sib["clause_no"]),
                    "prev_verdict": prev_verdict,
                    "new_verdict": new_verdict,
                    "regressed": regressed,
                }
            )

    regressed_clauses = [x for x in impacted_outcomes if x["regressed"]]

    store.log_action(
        project_id,
        "apply_remediation_edit",
        actor,
        role,
        note,
        {
            "remediation_id": rem_id,
            "target": target_label,
            "before_text": before_text[:300],
            "after_text": modified_text.strip()[:300],
            "resolved": passed_now,
            "verdict_after_reeval": reeval["verdict"],
            "impacted_count": len(impacted_outcomes),
            "regressed_clauses": [x["clause_no"] for x in regressed_clauses],
        },
        res["round_id"],
        res["requirement_id"],
        result_id,
    )
    return {
        "verdict": reeval["verdict"],
        "passed": passed_now,
        "explanation": reeval["explanation"],
        "impacted_count": len(impacted_outcomes),
        "impacted_clauses": impacted_outcomes,
        "regressed_clauses": regressed_clauses,
    }


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
    if summary["non_compliant"]:
        blockers.append(
            f"{summary['non_compliant']} applicable clause(s) are Non-compliant (disqualification risk) and must be remediated before sign-off."
        )
    if summary["insufficient"]:
        blockers.append(
            f"{summary['insufficient']} applicable clause(s) still have Insufficient evidence and require manual verification or remediation before sign-off."
        )
    if summary["unreviewed"]:
        blockers.append(f"{summary['unreviewed']} applicable clause(s) have no reviewer decision yet.")
    open_rems = store.list_remediations(rnd["project_id"], open_only=True)
    if open_rems:
        rem_clauses = ", ".join(str(r.get("clause_no") or r["requirement_id"]) for r in open_rems[:8])
        blockers.append(f"{len(open_rems)} remediation item(s) remain unresolved ({rem_clauses}).")
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

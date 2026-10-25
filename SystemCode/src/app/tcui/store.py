"""SQLite persistence for projects, documents, requirements, review rounds and the audit log.

Every function opens its own connection so it can be called from background job threads.
"""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    is_demo INTEGER DEFAULT 0,
    req_confirmed_at TEXT,
    last_requirement_id INTEGER,
    baseline_minutes REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    label TEXT NOT NULL,
    version INTEGER NOT NULL,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    pages INTEGER,
    failed_pages_json TEXT DEFAULT '[]',
    parse_status TEXT DEFAULT 'Pending',
    parse_error TEXT,
    selected INTEGER DEFAULT 1,
    supersedes_id INTEGER,
    uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clause_no TEXT NOT NULL,
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    category_score REAL,
    constraint_json TEXT DEFAULT '{}',
    source_doc_id INTEGER,
    page INTEGER,
    origin TEXT NOT NULL,
    original_json TEXT DEFAULT '{}',
    amended_by_doc_id INTEGER,
    amendment_page INTEGER,
    amendment_text TEXT,
    amendment_status TEXT,
    applicable INTEGER DEFAULT 1,
    na_reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS drawings (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    doc_id INTEGER,
    drawing_no TEXT NOT NULL,
    title TEXT,
    revision TEXT,
    date TEXT,
    page INTEGER,
    source TEXT,
    seq INTEGER
);
CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    no INTEGER NOT NULL,
    bid_doc_id INTEGER,
    doc_ids_json TEXT,
    engine TEXT,
    rule_version TEXT,
    status TEXT,
    created_at TEXT,
    signed_at TEXT,
    signer TEXT,
    sign_comment TEXT
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
    requirement_id INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
    verdict TEXT,
    original_verdict TEXT,
    rule_id TEXT,
    explanation TEXT,
    fields_json TEXT DEFAULT '[]',
    evidence_json TEXT DEFAULT '[]',
    chain_json TEXT DEFAULT '{}',
    corrected INTEGER DEFAULT 0,
    human_status TEXT DEFAULT 'Unreviewed',
    note TEXT,
    review_seconds REAL DEFAULT 0,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    round_id INTEGER,
    requirement_id INTEGER,
    result_id INTEGER,
    action TEXT NOT NULL,
    actor TEXT,
    role TEXT,
    note TEXT,
    payload_json TEXT DEFAULT '{}',
    at TEXT
);
CREATE TABLE IF NOT EXISTS remediations (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    requirement_id INTEGER NOT NULL REFERENCES requirements(id) ON DELETE CASCADE,
    opened_round_id INTEGER,
    issue TEXT,
    kb_refs_json TEXT DEFAULT '[]',
    draft TEXT,
    target TEXT,
    target_doc_id INTEGER,
    status TEXT,
    closed_round_id INTEGER,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT,
    done INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    error TEXT,
    result_json TEXT,
    created_at TEXT,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS known_issues (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    clause_no TEXT NOT NULL,
    expected TEXT,
    note TEXT
);
"""

JSON_COLUMNS = {
    "failed_pages_json",
    "constraint_json",
    "original_json",
    "doc_ids_json",
    "fields_json",
    "evidence_json",
    "chain_json",
    "payload_json",
    "kb_refs_json",
    "result_json",
}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _decode(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS:
            out[key[: -len("_json")]] = json.loads(value) if value else None
        else:
            out[key] = value
    return out


def _encode(values: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for key, value in values.items():
        if f"{key}_json" in JSON_COLUMNS:
            out[f"{key}_json"] = json.dumps(value, ensure_ascii=False)
        else:
            out[key] = value
    return out


def query(sql: str, args: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with connect() as con:
        return [_decode(r) for r in con.execute(sql, tuple(args)).fetchall()]


def query_one(sql: str, args: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    with connect() as con:
        return _decode(con.execute(sql, tuple(args)).fetchone())


def insert(table: str, values: Dict[str, Any]) -> int:
    values = _encode(values)
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    with connect() as con:
        cur = con.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
        return cur.lastrowid


def update(table: str, row_id: int, values: Dict[str, Any]) -> None:
    values = _encode(values)
    sets = ", ".join(f"{k}=?" for k in values)
    with connect() as con:
        con.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*values.values(), row_id))


def execute(sql: str, args: Iterable[Any] = ()) -> None:
    with connect() as con:
        con.execute(sql, tuple(args))


def init_db() -> None:
    with connect() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        # Jobs cannot survive a process restart; surface them as failed so the user can retry.
        con.execute(
            "UPDATE jobs SET status='failed', error='Interrupted by application restart', finished_at=? "
            "WHERE status IN ('queued','running')",
            (now(),),
        )


# ---------------------------------------------------------------- projects

def create_project(name: str, is_demo: bool = False) -> int:
    pid = insert("projects", {"name": name, "is_demo": int(is_demo), "created_at": now()})
    project_dir(pid).mkdir(parents=True, exist_ok=True)
    return pid


def project_dir(project_id: int):
    return config.PROJECTS_DIR / str(project_id)


def list_projects() -> List[Dict[str, Any]]:
    return query("SELECT * FROM projects ORDER BY id DESC")


def get_project(project_id: int) -> Optional[Dict[str, Any]]:
    return query_one("SELECT * FROM projects WHERE id=?", (project_id,))


def delete_project(project_id: int) -> None:
    execute("DELETE FROM projects WHERE id=?", (project_id,))
    shutil.rmtree(project_dir(project_id), ignore_errors=True)


# ---------------------------------------------------------------- documents

def list_documents(project_id: int, category: Optional[str] = None, selected_only: bool = False):
    sql = "SELECT * FROM documents WHERE project_id=?"
    args: List[Any] = [project_id]
    if category:
        sql += " AND category=?"
        args.append(category)
    if selected_only:
        sql += " AND selected=1"
    return query(sql + " ORDER BY category, version", args)


def get_document(doc_id: int) -> Optional[Dict[str, Any]]:
    return query_one("SELECT * FROM documents WHERE id=?", (doc_id,))


def find_document_by_hash(project_id: int, sha256: str):
    return query_one("SELECT * FROM documents WHERE project_id=? AND sha256=?", (project_id, sha256))


def selected_bid(project_id: int) -> Optional[Dict[str, Any]]:
    return query_one(
        "SELECT * FROM documents WHERE project_id=? AND category='bid' AND selected=1 ORDER BY version DESC",
        (project_id,),
    )


# ---------------------------------------------------------------- requirements

def list_requirements(project_id: int, include_drawings: bool = True):
    sql = "SELECT * FROM requirements WHERE project_id=?"
    if not include_drawings:
        sql += " AND origin != 'drawing'"
    return query(sql + " ORDER BY origin='drawing', id", (project_id,))


def get_requirement(req_id: int):
    return query_one("SELECT * FROM requirements WHERE id=?", (req_id,))


def reviewable_requirements(project_id: int):
    return query(
        "SELECT * FROM requirements WHERE project_id=? AND applicable=1 AND origin != 'drawing' "
        "AND (amendment_status IS NULL OR amendment_status != 'pending' OR origin != 'addendum') ORDER BY id",
        (project_id,),
    )


def pending_amendments(project_id: int):
    return query(
        "SELECT * FROM requirements WHERE project_id=? AND amendment_status='pending' ORDER BY id",
        (project_id,),
    )


# ---------------------------------------------------------------- rounds and results

def list_rounds(project_id: int):
    return query("SELECT * FROM rounds WHERE project_id=? ORDER BY no DESC", (project_id,))


def latest_round(project_id: int, done_only: bool = True):
    sql = "SELECT * FROM rounds WHERE project_id=?"
    if done_only:
        sql += " AND status='done'"
    return query_one(sql + " ORDER BY no DESC LIMIT 1", (project_id,))


def get_round(round_id: int):
    return query_one("SELECT * FROM rounds WHERE id=?", (round_id,))


def previous_round(rnd: Dict[str, Any]):
    return query_one(
        "SELECT * FROM rounds WHERE project_id=? AND no<? AND status='done' ORDER BY no DESC LIMIT 1",
        (rnd["project_id"], rnd["no"]),
    )


def list_results(round_id: int):
    """Results joined with their requirement, in clause order."""
    rows = query(
        "SELECT r.*, q.clause_no, q.text AS req_text, q.category, q.applicable, q.na_reason, q.origin, "
        "q.source_doc_id, q.page AS req_page, q.amended_by_doc_id, q.amendment_page, q.amendment_status, "
        "q.constraint_json, q.original_json "
        "FROM results r JOIN requirements q ON q.id = r.requirement_id "
        "WHERE r.round_id=? ORDER BY q.origin='drawing', q.id",
        (round_id,),
    )
    return rows


def get_result(result_id: int):
    rows = query(
        "SELECT r.*, q.clause_no, q.text AS req_text, q.category, q.applicable, q.na_reason, q.origin, "
        "q.source_doc_id, q.page AS req_page, q.amended_by_doc_id, q.amendment_page, q.amendment_status, "
        "q.constraint_json, q.original_json "
        "FROM results r JOIN requirements q ON q.id = r.requirement_id WHERE r.id=?",
        (result_id,),
    )
    return rows[0] if rows else None


def result_for(round_id: int, requirement_id: int):
    return query_one(
        "SELECT * FROM results WHERE round_id=? AND requirement_id=?", (round_id, requirement_id)
    )


# ---------------------------------------------------------------- audit log

def log_action(project_id: int, action: str, actor: str = "", role: str = "", note: str = "",
               payload: Optional[Dict[str, Any]] = None, round_id: Optional[int] = None,
               requirement_id: Optional[int] = None, result_id: Optional[int] = None) -> None:
    insert("actions", {
        "project_id": project_id,
        "round_id": round_id,
        "requirement_id": requirement_id,
        "result_id": result_id,
        "action": action,
        "actor": actor,
        "role": role,
        "note": note,
        "payload": payload or {},
        "at": now(),
    })


def list_actions(project_id: int, requirement_id: Optional[int] = None):
    if requirement_id is None:
        return query("SELECT * FROM actions WHERE project_id=? ORDER BY id DESC", (project_id,))
    return query(
        "SELECT * FROM actions WHERE project_id=? AND requirement_id=? ORDER BY id DESC",
        (project_id, requirement_id),
    )


# ---------------------------------------------------------------- remediation

def list_remediations(project_id: int, open_only: bool = False):
    sql = (
        "SELECT m.*, q.clause_no, q.text AS req_text, q.category FROM remediations m "
        "JOIN requirements q ON q.id = m.requirement_id WHERE m.project_id=?"
    )
    if open_only:
        sql += " AND m.status != 'Closed'"
    return query(sql + " ORDER BY m.status='Closed', m.id", (project_id,))


def open_remediation_for(requirement_id: int):
    return query_one(
        "SELECT * FROM remediations WHERE requirement_id=? AND status != 'Closed' ORDER BY id DESC",
        (requirement_id,),
    )


# ---------------------------------------------------------------- jobs

def create_job(project_id: int, kind: str) -> int:
    return insert("jobs", {"project_id": project_id, "kind": kind, "status": "queued", "created_at": now()})


def get_job(job_id: int):
    return query_one("SELECT * FROM jobs WHERE id=?", (job_id,))


def active_job(project_id: int, kind: Optional[str] = None):
    sql = "SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running')"
    args: List[Any] = [project_id]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    return query_one(sql + " ORDER BY id DESC LIMIT 1", args)


def active_jobs(project_id: int, kind_prefix: str = ""):
    return query(
        "SELECT * FROM jobs WHERE project_id=? AND status IN ('queued','running') AND kind LIKE ? ORDER BY id",
        (project_id, kind_prefix + "%"),
    )


def failed_jobs_since(project_id: int, kind_prefix: str, since_id: int = 0):
    return query(
        "SELECT * FROM jobs WHERE project_id=? AND status='failed' AND kind LIKE ? AND id>? ORDER BY id",
        (project_id, kind_prefix + "%", since_id),
    )


def last_job(project_id: int, kind: str):
    row = query_one(
        "SELECT * FROM jobs WHERE project_id=? AND kind=? ORDER BY id DESC LIMIT 1", (project_id, kind)
    )
    if row is not None and "message" not in row:
        row["message"] = row.get("stage") or ""
    return row


def latest_job(project_id: int, kind: str):
    return last_job(project_id, kind)

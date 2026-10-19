"""Shared Streamlit components: session context, status badges, job progress and the PDF page viewer."""

import base64
import html
import time
from typing import Any, Dict, List, Optional

import pymupdf as fitz
import streamlit as st

from . import config, store
from .config import CLOSED, COMPLIANT, CONFIRMED, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE, UNREVIEWED

PAGES = {
    "overview": "views/overview.py",
    "documents": "views/documents.py",
    "requirements": "views/requirements.py",
    "workspace": "views/workspace.py",
    "remediation": "views/remediation.py",
    "report": "views/report.py",
}

VERDICT_STYLE = {
    COMPLIANT: ("green", ":material/check_circle:", "✓"),
    NON_COMPLIANT: ("red", ":material/cancel:", "✗"),
    INSUFFICIENT: ("orange", ":material/help:", "?"),
}
STATUS_COLOR = {
    UNREVIEWED: "gray",
    CONFIRMED: "blue",
    TO_REMEDIATE: "violet",
    TO_REINSPECT: "violet",
    CLOSED: "green",
}

CSS = """
<style>
.block-container {padding-top: 3rem; padding-bottom: 2rem;}
.st-key-ws_nav button {justify-content: flex-start; text-align: left;}
.st-key-ws_nav button p {text-align: left;}
table.tc-cmp {width: 100%; border-collapse: collapse; background: #ffffff; font-size: 0.95rem;}
table.tc-cmp th {text-align: left; color: #5b6472; font-weight: 600; border-bottom: 2px solid #d9dde3; padding: 6px 10px;}
table.tc-cmp td {border-bottom: 1px solid #eceff3; padding: 8px 10px; font-variant-numeric: tabular-nums;}
div[data-testid="stMetric"] {background: #ffffff; border: 1px solid #e3e6eb; border-radius: 8px; padding: 10px 14px;}
.tc-muted {color: #5b6472; font-size: 0.85rem;}
.tc-req {background: #ffffff; border-left: 4px solid #1f3a68; padding: 8px 12px; border-radius: 4px; margin: 4px 0 8px 0;}
.tc-pdf {height: 640px; overflow: auto; border: 1px solid #d9dde3; border-radius: 6px; background: #ffffff;}
.tc-pdf img {display: block; margin: 0 auto;}
</style>
"""


# ----------------------------------------------------------------- session context

def apply_style() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def project_id() -> Optional[int]:
    pid = st.session_state.get("project_id")
    if pid is not None and store.get_project(pid):
        return pid
    projects = store.list_projects()
    st.session_state["project_id"] = projects[0]["id"] if projects else None
    return st.session_state["project_id"]


def current_project() -> Optional[Dict[str, Any]]:
    pid = project_id()
    return store.get_project(pid) if pid else None


def require_project() -> Dict[str, Any]:
    project = current_project()
    if not project:
        st.info("No project yet. Create one or load the demo project on the Overview page.")
        st.page_link(PAGES["overview"], label="Go to Overview", icon=":material/dashboard:")
        st.stop()
    return project


def select_project(pid: Optional[int]) -> None:
    st.session_state["project_id"] = pid
    for key in list(st.session_state):
        if key.startswith(("ws_", "rem_", "pane_")):
            del st.session_state[key]


def role() -> str:
    return st.session_state.get("role") or "Reviewer"


def actor() -> str:
    return (st.session_state.get("user_name") or "").strip() or role()


def sidebar() -> None:
    with st.sidebar:
        projects = store.list_projects()
        if projects:
            ids = [p["id"] for p in projects]
            names = {p["id"]: p["name"] for p in projects}
            pid = project_id()
            chosen = st.selectbox("Project", ids, index=ids.index(pid) if pid in ids else 0,
                                  format_func=lambda i: names[i])
            if chosen != pid:
                select_project(chosen)
                st.rerun()
        if "role" not in st.session_state:
            st.session_state["role"] = "Reviewer"
        st.segmented_control("Role", config.ROLES, key="role", required=True)
        st.text_input("Your name", key="user_name", placeholder="Recorded in the audit log")
        st.divider()
        if config.LLM_PROVIDER:
            st.warning(f"Clause text and bid excerpts are sent to **{config.LLM_PROVIDER}** for extraction.",
                       icon=":material/cloud_upload:")
        else:
            st.caption(":material/lock: All processing runs on this machine. No document text is sent to an "
                       "external LLM service.")
        st.caption(f"Engine: {config.ENGINE_NAME} · Rules: {config.RULE_VERSION}")


def flash(message: str, kind: str = "success") -> None:
    st.session_state.setdefault("_flash", []).append((kind, message))


def show_flash() -> None:
    for kind, message in st.session_state.pop("_flash", []):
        getattr(st, kind)(message)


def go(page: str, **state: Any) -> None:
    for key, value in state.items():
        st.session_state[key] = value
    st.switch_page(PAGES[page])


# ----------------------------------------------------------------- badges

def verdict_md(verdict: Optional[str]) -> str:
    if not verdict:
        return ""
    color, icon, _ = VERDICT_STYLE.get(verdict, ("gray", ":material/remove:", "-"))
    return f":{color}-badge[{icon} {verdict}]"


def verdict_icon(verdict: Optional[str]) -> str:
    return VERDICT_STYLE.get(verdict, ("gray", "", "·"))[2]


def status_md(status: Optional[str]) -> str:
    if not status:
        return ""
    return f":{STATUS_COLOR.get(status, 'gray')}-badge[{status}]"


# ----------------------------------------------------------------- background jobs

@st.fragment(run_every=1.0)
def _poll_jobs(pid: int, kind_prefix: str) -> None:
    active = store.active_jobs(pid, kind_prefix)
    if not active:
        st.rerun(scope="app")
    for job in active:
        total = job["total"] or 0
        frac = min(1.0, (job["done"] or 0) / total) if total else 0.0
        st.progress(frac, text=f"{job['stage'] or 'Queued'}")


def job_progress(pid: int, kind_prefix: str) -> bool:
    """Show live progress for active jobs of this kind; returns True while any is running."""
    if store.active_jobs(pid, kind_prefix):
        _poll_jobs(pid, kind_prefix)
        return True
    return False


def job_error(pid: int, kind: str) -> None:
    job = store.last_job(pid, kind)
    if job and job["status"] == "failed":
        st.error(f"The last {kind} task failed: {job['error']}")


# ----------------------------------------------------------------- PDF page viewer

@st.cache_data(max_entries=48, show_spinner=False)
def render_page(path: str, page_no: int, scale: float, highlight: str = "") -> bytes:
    doc = fitz.open(path)
    try:
        page = doc[page_no - 1]
        if highlight:
            words = highlight.split()
            for i in range(0, len(words), 5):
                chunk = " ".join(words[i:i + 5])
                for rect in page.search_for(chunk):
                    page.add_highlight_annot(rect)
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), annots=True).tobytes("png")
    finally:
        doc.close()


ZOOMS = {"Fit": 100, "125%": 125, "150%": 150, "200%": 200}


def _step_page(key: str, delta: int, pages: int) -> None:
    st.session_state[key] = min(max(1, st.session_state[key] + delta), pages)


def pdf_pane(key: str, doc: Optional[Dict[str, Any]], page: Optional[int], highlight: str = "") -> None:
    """Independent page viewer: jumps to `page` when the target changes, then lets the user browse freely."""
    if not doc:
        st.markdown('<div class="tc-pdf" style="height:200px;padding:16px">No source page for this item.</div>',
                    unsafe_allow_html=True)
        return
    pages = doc["pages"] or 1
    target = (doc["id"], page or 1)
    page_key = f"pane_{key}_page"
    if st.session_state.get(f"pane_{key}_target") != target or page_key not in st.session_state:
        st.session_state[f"pane_{key}_target"] = target
        st.session_state[page_key] = min(max(1, page or 1), pages)

    c1, c2, c3, c4 = st.columns([1, 2.2, 1, 2], vertical_alignment="center")
    c1.button("", icon=":material/chevron_left:", key=f"pane_{key}_prev", on_click=_step_page,
              args=(page_key, -1, pages), disabled=st.session_state[page_key] <= 1)
    c2.number_input("Page", min_value=1, max_value=pages, key=page_key, label_visibility="collapsed")
    c3.button("", icon=":material/chevron_right:", key=f"pane_{key}_next", on_click=_step_page,
              args=(page_key, 1, pages), disabled=st.session_state[page_key] >= pages)
    zoom = c4.selectbox("Zoom", list(ZOOMS), key=f"pane_{key}_zoom", label_visibility="collapsed")
    current = st.session_state[page_key]
    st.caption(f"{doc['label']} · page {current} of {pages}"
               + (" · evidence highlighted" if highlight and current == (page or 1) else ""))
    pct = ZOOMS[zoom]
    png = render_page(doc["path"], current, 1.6 * pct / 100, highlight if current == (page or 1) else "")
    b64 = base64.b64encode(png).decode()
    st.markdown(f'<div class="tc-pdf"><img src="data:image/png;base64,{b64}" style="width:{pct}%"/></div>',
                unsafe_allow_html=True)


# ----------------------------------------------------------------- review timer

def start_timer(result_id: int) -> None:
    if st.session_state.get("ws_timer_result") != result_id:
        st.session_state["ws_timer_result"] = result_id
        st.session_state["ws_timer_start"] = time.time()


def take_elapsed() -> float:
    start = st.session_state.get("ws_timer_start") or time.time()
    st.session_state["ws_timer_start"] = time.time()
    return time.time() - start


def doc_map(pid: int) -> Dict[int, Dict[str, Any]]:
    return {d["id"]: d for d in store.list_documents(pid)}


def fmt_minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f} min"


def evidence_label(e: Dict[str, Any], i: int) -> str:
    return f"{i + 1}. p.{e['page']} · {e.get('role', '')}"


RESULT_COLOR = {COMPLIANT: "#1e7d3c", NON_COMPLIANT: "#b42318", INSUFFICIENT: "#b25e09"}


def comparison_table(fields: List[Dict[str, Any]]) -> None:
    """Requirement vs bid response, the primary explanation of a verdict."""
    cells = "".join(
        f"<tr><td>{html.escape(str(f['field']))}</td><td>{html.escape(str(f['required']))}</td>"
        f"<td>{html.escape(str(f['actual']))}</td>"
        f"<td style='color:{RESULT_COLOR.get(f['result'], '#333')};font-weight:600'>"
        f"{verdict_icon(f['result'])} {html.escape(str(f['result']))}</td></tr>"
        for f in fields
    )
    st.markdown(
        "<table class='tc-cmp'><thead><tr><th>Field</th><th>Tender requirement</th><th>Bid response</th>"
        f"<th>Result</th></tr></thead><tbody>{cells}</tbody></table>",
        unsafe_allow_html=True,
    )


def table(rows: List[Dict[str, Any]], **kwargs: Any) -> None:
    st.dataframe(rows, hide_index=True, width="stretch", **kwargs)

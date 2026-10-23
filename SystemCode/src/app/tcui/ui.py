"""Shared Streamlit components: session context, status badges, job progress and the PDF page viewer."""

import base64
import html
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz
import streamlit as st

from . import config, service, store
from .config import CLOSED, COMPLIANT, CONFIRMED, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE, UNREVIEWED

PAGES = {
    "overview": "views/overview.py",
    "requirements": "views/requirements.py",
    "workspace": "views/workspace.py",
    "remediation": "views/remediation.py",
    "report": "views/report.py",
}

NAVIGATION = {
    "Workspace": [("overview", "Overview", ":material/dashboard:")],
    "Review": [
        ("requirements", "Requirements", ":material/checklist:"),
        ("workspace", "Review Workspace", ":material/compare:"),
        ("remediation", "Remediation", ":material/build:"),
    ],
    "Delivery": [("report", "Report & Export", ":material/verified:")],
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
.stAppDeployButton, [data-testid="stAppDeployButton"] {display: none !important;}
.block-container {padding-top: 3rem; padding-bottom: 2rem;}
.st-key-ws_nav button {justify-content: flex-start; text-align: left;}
.st-key-ws_nav button p {text-align: left;}
table.tc-cmp {width: 100%; border-collapse: collapse; background: #ffffff; font-size: 0.95rem;}
table.tc-cmp th {text-align: left; color: #5b6472; font-weight: 600; border-bottom: 2px solid #d9dde3; padding: 6px 10px;}
table.tc-cmp td {border-bottom: 1px solid #eceff3; padding: 8px 10px; font-variant-numeric: tabular-nums;}
div[data-testid="stMetric"] {background: #ffffff; border: 1px solid #e3e6eb; border-radius: 8px; padding: 10px 14px;}
.tc-muted {color: #5b6472; font-size: 0.85rem;}
.tc-req {background: #fff8db; border: 1px solid #f5d061; border-left: 5px solid #d97706; padding: 10px 14px; border-radius: 6px; margin: 6px 0 10px 0; font-weight: 500; color: #1f2937;}
.tc-req mark {background: #fde047; padding: 1px 4px; border-radius: 3px; font-weight: 600;}
.tc-bid {background: #f0f7ff; border: 1px solid #bfdbfe; border-left: 5px solid #2563eb; padding: 12px 16px; border-radius: 6px; margin: 4px 0 12px 0; color: #1e293b; line-height: 1.6; min-height: 140px; max-height: 420px; overflow-y: auto; white-space: pre-wrap;}
.tc-bid mark.tc-tag {background: #dbeafe; color: #1d4ed8; padding: 1px 6px; border-radius: 3px; font-weight: 600;}
.tc-bid mark.tc-quote {background: #fef08a; color: #1f2937; padding: 1px 3px; border-radius: 3px; font-weight: 600;}
.tc-pdf {height: 640px; overflow: auto; border: 1px solid #d9dde3; border-radius: 6px; background: #ffffff;}
.tc-pdf img {display: block; margin: 0 auto;}
.tc-pane-title {white-space: nowrap; overflow-x: auto; overflow-y: hidden; scrollbar-width: thin; line-height: 1.5; padding-bottom: 2px;}
.st-key-ws_tender_head, .st-key-ws_bid_head {min-height: 42px; display: flex; align-items: center;}
.st-key-ws_bid_head [data-testid="stButtonGroup"] > div:last-child {flex-wrap: nowrap !important; overflow-x: auto !important; overflow-y: hidden !important; scrollbar-width: thin;}
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
    st.session_state["_show_project_start"] = False
    for key in list(st.session_state):
        if key.startswith(("ws_", "rem_", "pane_")):
            del st.session_state[key]


def role() -> str:
    return st.session_state.get("role") or "Reviewer"


def actor() -> str:
    return (st.session_state.get("user_name") or "").strip() or role()


def sidebar(current_page: str = "") -> None:
    with st.sidebar:
        st.caption("Projects")
        if st.button("New project", icon=":material/add:", key="nav_new_project", type="secondary",
                     width="stretch", wrap=True):
            st.session_state["_show_project_start"] = True
            go("overview")
        if current_page:
            st.session_state["_show_project_start"] = False
        projects = store.list_projects()
        if projects:
            ids = [p["id"] for p in projects]
            names = {p["id"]: p["name"] for p in projects}
            pid = project_id()
            starting = st.session_state.get("_show_project_start", False)
            chosen = st.selectbox("Project", ids, index=None if starting else (ids.index(pid) if pid in ids else 0),
                                  format_func=lambda i: names[i], placeholder="Open an existing project")
            if chosen is not None and (chosen != pid or starting):
                select_project(chosen)
                st.rerun()
        for section, pages in NAVIGATION.items():
            st.caption(section)
            for page, label, icon in pages:
                st.page_link(PAGES[page], label=label, icon=icon, width="stretch")
        with st.expander("Engine details"):
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


# ----------------------------------------------------------------- overview guidance

def workflow_steps(active: Optional[str] = None, completed: Optional[List[str]] = None) -> None:
    steps = [
        ("Documents", "Upload tender and bid"),
        ("Requirements", "Confirm what to check"),
        ("Review", "Trace and verify evidence"),
        ("Remediation", "Resolve and re-inspect if needed"),
        ("Sign-off", "Approve and export"),
    ]
    st.subheader("Review workflow")
    with st.container(horizontal=True, wrap=True, gap="small"):
        for number, (label, detail) in enumerate(steps, start=1):
            with st.container(width=160, gap="xsmall"):
                st.markdown(f"**{number} · {label}**")
                if label in (completed or []):
                    st.badge("Complete", icon=":material/check:", color="green")
                elif label == active:
                    st.badge("Next", icon=":material/arrow_forward:", color="blue")
                elif label == "Remediation" and active:
                    st.badge("If needed", color="gray")
                elif active:
                    st.badge("Upcoming", color="gray")
                st.caption(detail)


def workflow_completed_steps(project: Dict[str, Any], checks: List[Dict[str, Any]],
                             rnd: Optional[Dict[str, Any]]) -> List[str]:
    """Read completion from project data; re-inspection can return to an earlier stage."""
    completed = []
    requirement_checks = {"Addendum changes decided", "Requirement list confirmed"}
    if all(c["ok"] for c in checks if c["item"] not in requirement_checks):
        completed.append("Documents")
    if all(c["ok"] for c in checks):
        completed.append("Requirements")
    bid = store.selected_bid(project["id"])
    if (not all(c["ok"] for c in checks) or not rnd or not bid or bid["id"] != rnd["bid_doc_id"]
            or store.active_jobs(project["id"], "review")):
        return completed
    items = store.list_remediations(project["id"])
    if not service.signoff_blockers(rnd["id"]):
        completed.append("Review")
        if items and all(item["status"] == CLOSED for item in items):
            completed.append("Remediation")
    if rnd["signed_at"]:
        completed.append("Sign-off")
    return completed


def project_next_step(project: Dict[str, Any], checks: List[Dict[str, Any]],
                      rnd: Optional[Dict[str, Any]]) -> Tuple[str, str, str]:
    """Choose an Overview entry point using existing readiness and review state."""
    pid = project["id"]
    if store.active_jobs(pid, "review"):
        return "Review", "The automated review is running. Follow its progress below.", "overview"

    requirement_checks = {"Addendum changes decided", "Requirement list confirmed"}
    missing_documents = [c["item"] for c in checks if not c["ok"] and c["item"] not in requirement_checks]
    if missing_documents:
        return "Documents", "Prepare the selected documents: " + "; ".join(missing_documents) + ".", "documents"
    if store.active_jobs(pid, "extract"):
        return "Requirements", "Requirements are being extracted. Open Requirements to follow progress.", "requirements"
    if any(not c["ok"] for c in checks):
        return ("Requirements", "Extract or import requirements, decide addendum changes, and confirm the list.",
                "requirements")
    if not rnd:
        return "Review", "Your documents and requirements are ready. Start the automated review.", "overview"

    bid = store.selected_bid(pid)
    open_rem = store.list_remediations(pid, open_only=True)
    if bid and bid["id"] != rnd["bid_doc_id"]:
        if open_rem:
            return "Remediation", "A new bid version is ready. Re-inspect it and compare the results.", "remediation"
        return "Review", "A new bid version is ready. Start a review of this version.", "overview"
    if rnd["signed_at"]:
        return "Sign-off", "This round is signed off. Open the report to export the matrix and review report.", "report"

    summary = service.round_summary(rnd["id"])
    if summary["unreviewed"]:
        return ("Review", f"Verify the source evidence and record decisions for {summary['unreviewed']} clause(s).",
                "workspace")
    if any(item["status"] == TO_REINSPECT for item in open_rem):
        return "Review", "Check the re-inspection evidence and confirm the remaining remediation items.", "workspace"
    if open_rem:
        return ("Remediation", "Resolve open items, upload a revised bid, and re-inspect. Open risks can also be "
                "disclosed at sign-off.", "remediation")
    if service.signoff_blockers(rnd["id"]):
        return "Review", "Complete the remaining reviewer decisions before sign-off.", "workspace"
    return "Sign-off", "Reviewer decisions are complete. A Manager can sign off and export.", "report"


# ----------------------------------------------------------------- badges

def verdict_md(verdict: Optional[str]) -> str:
    if not verdict:
        return ""
    color, icon, _ = VERDICT_STYLE.get(verdict, ("gray", ":material/remove:", "-"))
    return f":{color}-badge[{icon} {verdict}]"


def sub_verdict_md(chain: Optional[Dict[str, Any]]) -> str:
    if not chain or not chain.get("sub_verdict_label"):
        return ""
    color = chain.get("sub_verdict_color") or "gray"
    label = chain["sub_verdict_label"]
    return f":{color}-badge[{label}]"


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

def _extract_highlight_chunks(highlight: str) -> List[str]:
    """Split English or Chinese highlight excerpts into non-overlapping sub-spans for PyMuPDF `search_for`."""
    if not highlight:
        return []
    if re.search(r"[\u4e00-\u9fff]", highlight):
        parts = [
            seg.strip()
            for seg in re.split(r"[，。；：、！？\n\r\t|（）()【】《》“”\"']+", highlight)
            if len(seg.strip()) >= 3
        ]
        chunks: List[str] = []
        for p in parts:
            if len(p) <= 10:
                chunks.append(p)
            else:
                for i in range(0, len(p), 9):
                    sub = p[i : i + 9]
                    if len(sub) >= 3:
                        chunks.append(sub)
        return list(dict.fromkeys(chunks))[:18]
    words = highlight.split()
    return [" ".join(words[i : i + 5]) for i in range(0, len(words), 5)]


def _extract_fallback_keywords(highlight: str) -> List[str]:
    """Extract non-overlapping 3-5 char Chinese key terms and numeric-unit spans for condensed tender clauses."""
    if not highlight:
        return []
    kws: List[str] = []
    for num_span in re.findall(r"\d+(?:\.\d+)?[\u4e00-\u9fffA-Za-z%]+", highlight):
        if len(num_span) >= 2:
            kws.append(num_span)
    cleaned = re.sub(r"[，。；：、！？\n\r\t|（）()【】《》“”\"'的了在及与或为须应需]+", " ", highlight)
    for token in cleaned.split():
        token = token.strip()
        if len(token) < 3:
            continue
        if len(token) <= 6:
            kws.append(token)
        else:
            for i in range(0, len(token), 4):
                sub = token[i : i + 4]
                if len(sub) >= 3:
                    kws.append(sub)
    return list(dict.fromkeys(kws))[:20]


def _merge_overlapping_rects(rects: List[fitz.Rect]) -> List[fitz.Rect]:
    """Merge overlapping rectangles on the same line so `add_highlight_annot` never stacks multiple yellow layers."""
    if not rects:
        return []
    merged: List[fitz.Rect] = []
    for r in sorted(rects, key=lambda box: (round(box.y0, 1), box.x0)):
        r_copy = fitz.Rect(r)
        combined = False
        for idx, existing in enumerate(merged):
            same_line = abs(existing.y0 - r_copy.y0) <= 4.0 and abs(existing.y1 - r_copy.y1) <= 4.0
            horiz_overlap = not (r_copy.x1 < existing.x0 - 1.0 or r_copy.x0 > existing.x1 + 1.0)
            if (same_line and horiz_overlap) or existing.intersects(r_copy):
                merged[idx] = existing | r_copy
                combined = True
                break
        if not combined:
            merged.append(r_copy)
    return merged


def _group_page_paragraphs(page: fitz.Page) -> List[List[Any]]:
    """Group line-level PDF blocks into complete logical clause paragraphs."""
    blocks = [
        b for b in (page.get_text("blocks") or [])
        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()
    ]
    paras: List[List[Any]] = []
    cur: List[Any] = []
    new_para_pat = re.compile(
        r"^\s*(\d+\.\d+|[（(][一二三四五六七八九十\d]+[)）]|[一二三四五六七八九十]+、|[①②③④⑤⑥⑦⑧⑨⑩]|第[一二三四五六七八九十\d]+[条章节])"
    )
    for b in blocks:
        txt = b[4].strip()
        if cur:
            prev = cur[-1]
            prev_txt = prev[4].strip()
            starts_new_item = bool(new_para_pat.match(txt))
            col_jump = abs(b[0] - prev[0]) > 45
            vert_gap = (b[1] - prev[3]) > 18 or b[1] < prev[1] - 5
            prev_ended = bool(re.search(r"[。；]\s*$", prev_txt))
            if starts_new_item or col_jump or vert_gap or prev_ended:
                paras.append(cur)
                cur = [b]
            else:
                cur.append(b)
        else:
            cur.append(b)
    if cur:
        paras.append(cur)
    return paras


def _find_tender_paragraph_rects(page: fitz.Page, req_text: str) -> List[fitz.Rect]:
    """Find and return all line rects of the full original tender paragraph(s) corresponding to `req_text`."""
    if not req_text:
        return []
    paras = _group_page_paragraphs(page)
    if not paras:
        return []
    clean_req = re.sub(r"[，。；：、！？\s（）()【】《》“”\"'的了在及与或为须应需]+", "", req_text)
    bigrams = {
        clean_req[i : i + 2]
        for i in range(max(0, len(clean_req) - 1))
        if len(clean_req[i : i + 2]) == 2
    }
    num_tokens = set(re.findall(r"\d+(?:\.\d+)?[\u4e00-\u9fffA-Za-z%]*", req_text))

    scored: List[Tuple[float, List[Any]]] = []
    for p in paras:
        p_text = re.sub(r"\s+", "", "".join(b[4] for b in p))
        if len(p_text) < 2:
            continue
        bg_hits = sum(1.0 for bg in bigrams if bg in p_text)
        num_hits = sum(3.0 for nt in num_tokens if len(nt) >= 2 and nt in p_text)
        kw_hits = sum(2.0 for kw in _extract_fallback_keywords(req_text) if kw in p_text)
        score = bg_hits + num_hits + kw_hits
        if score > 0:
            scored.append((score, p))

    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score = scored[0][0]
    selected_rects: List[fitz.Rect] = []
    for s, p in scored:
        if s >= max(3.0, best_score * 0.72):
            for b in p:
                selected_rects.append(fitz.Rect(b[:4]))
        if len(selected_rects) >= 10:
            break
    return _merge_overlapping_rects(selected_rects)


def _find_bid_rag_rects(page: fitz.Page, rag_text: str, snippet: str = "") -> Tuple[List[fitz.Rect], List[fitz.Rect]]:
    """Find all rects for the RAG-retrieved passage on the Bid PDF page and return (base_rects, emphasis_rects)."""
    target_text = (rag_text or snippet or "").strip()
    if not target_text:
        return [], []

    # 1. Match full blocks/lines on the page that belong to the retrieved RAG passage
    clean_rag = re.sub(r"\s+", "", target_text)
    rag_grams = {
        clean_rag[i : i + 4]
        for i in range(0, max(0, len(clean_rag) - 3), 2)
        if len(clean_rag[i : i + 4]) == 4
    }
    rag_rects: List[fitz.Rect] = []
    for b in page.get_text("blocks") or []:
        if len(b) < 5 or not isinstance(b[4], str) or not b[4].strip():
            continue
        b_clean = re.sub(r"\s+", "", b[4])
        if len(b_clean) < 3:
            continue
        if b_clean in clean_rag or (len(b_clean) >= 6 and b_clean[:6] in clean_rag):
            rag_rects.append(fitz.Rect(b[:4]))
            continue
        if rag_grams:
            overlap = sum(1 for g in rag_grams if g in b_clean)
            if overlap >= 2 or (len(b_clean) <= 12 and overlap >= 1):
                rag_rects.append(fitz.Rect(b[:4]))

    # 2. Also collect phrase search rects from `target_text` and `snippet`
    phrase_rects: List[fitz.Rect] = []
    for chunk in _extract_highlight_chunks(target_text):
        phrase_rects.extend(page.search_for(chunk))
    if snippet and snippet != target_text:
        for chunk in _extract_highlight_chunks(snippet):
            phrase_rects.extend(page.search_for(chunk))

    base = _merge_overlapping_rects(rag_rects + phrase_rects)
    # Emphasize ("加重表示") the RAG retrieved region & specific quote rects
    emphasis = _merge_overlapping_rects(phrase_rects if phrase_rects else rag_rects)
    return base, emphasis


@st.cache_data(max_entries=64, show_spinner=False)
def render_page(
    path: str,
    page_no: int,
    scale: float,
    highlight: str = "",
    mode: str = "tender",
    secondary_highlight: str = "",
) -> bytes:
    doc = fitz.open(path)
    try:
        idx = min(max(0, page_no - 1), max(0, len(doc) - 1))
        page = doc[idx]
        if highlight or secondary_highlight:
            if mode == "tender":
                # Highlight the entire involved original paragraph(s) in the Tender PDF
                para_rects = _find_tender_paragraph_rects(page, highlight)
                if not para_rects:
                    for chunk in _extract_highlight_chunks(highlight):
                        para_rects.extend(page.search_for(chunk))
                    para_rects = _merge_overlapping_rects(para_rects)
                for rect in para_rects:
                    page.add_highlight_annot(rect)
            else:
                # BID / RAG mode: highlight all RAG-retrieved passage blocks and emphasize ("加重") them
                base_rects, emphasis_rects = _find_bid_rag_rects(
                    page, secondary_highlight or highlight, highlight
                )
                for rect in base_rects:
                    annot = page.add_highlight_annot(rect)
                    annot.set_colors(stroke=(1.0, 0.88, 0.2))
                    annot.update()
                for rect in emphasis_rects:
                    annot2 = page.add_highlight_annot(rect)
                    annot2.set_colors(stroke=(1.0, 0.72, 0.0))
                    annot2.update()
        return page.get_pixmap(matrix=fitz.Matrix(scale, scale), annots=True).tobytes("png")
    finally:
        doc.close()


def _step_page(key: str, delta: int, pages: int) -> None:
    st.session_state[key] = min(max(1, st.session_state[key] + delta), pages)


@st.dialog("Document Viewer", width="large")
def _open_document_modal(
    doc: Dict[str, Any],
    initial_page: int,
    highlight: str = "",
    mode: str = "tender",
    secondary_highlight: str = "",
) -> None:
    pages = doc.get("pages") or 1
    modal_page_key = f"modal_page_{doc.get('id')}_{initial_page}"
    if modal_page_key not in st.session_state:
        st.session_state[modal_page_key] = min(max(1, initial_page), pages)

    mc1, mc2, mc3, mc4 = st.columns([1, 1.6, 1, 3.4], vertical_alignment="center")
    mc1.button(
        "Prev",
        icon=":material/chevron_left:",
        key=f"{modal_page_key}_prev",
        on_click=_step_page,
        args=(modal_page_key, -1, pages),
        disabled=st.session_state[modal_page_key] <= 1,
        width="stretch",
    )
    mc2.number_input("Page", min_value=1, max_value=pages, key=modal_page_key, label_visibility="collapsed")
    mc3.button(
        "Next",
        icon=":material/chevron_right:",
        key=f"{modal_page_key}_next",
        on_click=_step_page,
        args=(modal_page_key, 1, pages),
        disabled=st.session_state[modal_page_key] >= pages,
        width="stretch",
    )
    cur = min(max(1, st.session_state[modal_page_key]), pages)
    mc4.caption(f"**{doc.get('label', 'Document')}** · Page {cur} of {pages}")

    hl = highlight if cur == initial_page else ""
    sec_hl = secondary_highlight if cur == initial_page else ""
    png = render_page(doc["path"], cur, 2.2, hl, mode=mode, secondary_highlight=sec_hl)
    b64 = base64.b64encode(png).decode()
    st.markdown(
        f'<div style="max-height:78vh;overflow:auto;border:1px solid #d9dde3;border-radius:6px;background:#fff;">'
        f'<img src="data:image/png;base64,{b64}" style="width:100%;display:block;margin:0 auto;"/></div>',
        unsafe_allow_html=True,
    )


def pdf_pane(
    key: str,
    doc: Optional[Dict[str, Any]],
    page: Optional[int],
    highlight: str = "",
    allow_page_nav: bool = False,
    secondary_highlight: str = "",
) -> None:
    """Page viewer: displays the target page and opens a large viewer modal when the file button is clicked."""
    if not doc:
        st.markdown('<div class="tc-pdf" style="height:200px;padding:16px">No source page for this item.</div>',
                    unsafe_allow_html=True)
        return
    pages = doc["pages"] or 1
    current = min(max(1, page or 1), pages)
    page_key = f"pane_{key}_page"
    st.session_state[page_key] = current
    mode = "tender" if key == "tender" else "bid"

    if st.button(
        f"{doc['label']} · page {current} of {pages}"
        + (" · highlighted" if (highlight or secondary_highlight) else ""),
        key=f"pane_{key}_open_modal",
        icon=":material/open_in_new:",
        type="tertiary",
        width="stretch",
        help="Click to open document in a large window",
    ):
        _open_document_modal(doc, current, highlight, mode=mode, secondary_highlight=secondary_highlight)

    png = render_page(doc["path"], current, 1.6, highlight, mode=mode, secondary_highlight=secondary_highlight)
    b64 = base64.b64encode(png).decode()
    st.markdown(f'<div class="tc-pdf"><img src="data:image/png;base64,{b64}" style="width:100%"/></div>',
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
    """Structured field & bid response summary table."""
    cells = "".join(
        f"<tr><td>{html.escape(str(f['field']))}</td>"
        f"<td>{html.escape(str(f['actual']))}</td>"
        f"<td style='color:{RESULT_COLOR.get(f['result'], '#333')};font-weight:600'>"
        f"{verdict_icon(f['result'])} {html.escape(str(f['result']))}</td></tr>"
        for f in fields
    )
    st.markdown(
        "<table class='tc-cmp'><thead><tr><th>Field</th><th>Bid response</th>"
        f"<th>Result</th></tr></thead><tbody>{cells}</tbody></table>",
        unsafe_allow_html=True,
    )


def table(rows: List[Dict[str, Any]], **kwargs: Any) -> None:
    st.dataframe(rows, hide_index=True, width="stretch", **kwargs)

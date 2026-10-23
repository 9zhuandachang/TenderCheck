import html
from pathlib import Path
import pymupdf as fitz
import streamlit as st

from tcui import real_backend, service, store, ui
from tcui.config import CLOSED
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()

rounds = [r for r in store.list_rounds(pid) if r["status"] == "done"]
if not rounds:
    st.title("Remediation")
    st.info("No review results yet. Start a review from the Overview page first.")
    st.page_link(ui.PAGES["overview"], label="Go to Overview", icon=":material/dashboard:")
    st.stop()

docs = ui.doc_map(pid)
rnd = rounds[0]
round_id = rnd["id"]
signed = bool(rnd["signed_at"])

results = store.list_results(round_id)
result_by_req_id = {r["requirement_id"]: r for r in results}
items = store.list_remediations(pid)

# ----------------------------------------------------------------- top bar
top_l, top_r = st.columns([4, 1.2], vertical_alignment="center")
top_l.markdown(f"### Remediation  \n<span class='tc-muted'>{html.escape(project['name'])}</span>", unsafe_allow_html=True)
if top_r.button("Back to Workspace", icon=":material/arrow_back:", width="stretch"):
    ui.go("workspace")

if not items:
    with st.container(border=True):
        st.info(
            "当前还没有待修改的条款。请在 **Review Workspace** 中点击 **To remediation** 进入本页面。",
            icon=":material/info:",
        )
        if st.button("前往 Review Workspace", type="primary", icon=":material/compare:"):
            ui.go("workspace")
    st.stop()

items_sorted = sorted(items, key=lambda x: (x["status"] == CLOSED, x["clause_no"]))
item_by_id = {i["id"]: i for i in items_sorted}

focus_rem_id = st.session_state.pop("rem_focus_id", None)
focus_req_id = st.session_state.pop("rem_focus_req", None)
if focus_rem_id and focus_rem_id in item_by_id:
    st.session_state["rem_selected_id"] = focus_rem_id
elif focus_req_id:
    hit = next((i["id"] for i in items_sorted if i["requirement_id"] == focus_req_id), None)
    if hit:
        st.session_state["rem_selected_id"] = hit

if st.session_state.get("rem_selected_id") not in item_by_id:
    open_first = next((i["id"] for i in items_sorted if i["status"] != CLOSED), items_sorted[0]["id"])
    st.session_state["rem_selected_id"] = open_first

cur_rem_id = st.session_state["rem_selected_id"]
cur_item = item_by_id[cur_rem_id]
cur_res = result_by_req_id.get(cur_item["requirement_id"])

if not cur_res:
    st.warning("该整改项对应的审查结果不存在。")
    st.stop()


def _select_rem(rem_id: int) -> None:
    st.session_state["rem_selected_id"] = rem_id


@st.cache_data(show_spinner=False)
def _read_bid_page_text(pdf_path: str, page_no: int) -> str:
    """Read raw text directly from the Bid PDF page (1-indexed)."""
    p = Path(pdf_path)
    if not p.exists():
        return ""
    try:
        with fitz.open(str(p)) as doc:
            idx = max(0, min(len(doc) - 1, int(page_no) - 1))
            return doc[idx].get_text("text").strip()
    except Exception:
        return ""


# Collect available Bid documents (primary Bid + supporting volumes if any)
bid_doc = docs.get(rnd["bid_doc_id"])
bid_volumes = []
if bid_doc and bid_doc.get("path") and Path(bid_doc["path"]).exists():
    bid_volumes.append(
        {
            "label": bid_doc.get("filename") or bid_doc.get("label") or "Bid Document",
            "path": str(bid_doc["path"]),
            "pages": int(bid_doc.get("pages") or 1),
        }
    )
if real_backend.is_s604_project(project):
    seen_paths = {v["path"] for v in bid_volumes}
    for doc_id, src in sorted(real_backend._load_doc_sources().items()):
        if src.get("exists") and src["path"] not in seen_paths:
            seen_paths.add(src["path"])
            bid_volumes.append(
                {
                    "label": src["label"],
                    "path": src["path"],
                    "pages": int(src.get("pages") or 1),
                }
            )

# ----------------------------------------------------------------- 2-column clean layout
cols = st.columns([1.1, 3.2], gap="medium")

# ================================================================= Left: Problem clauses list
with cols[0]:
    open_cnt = sum(1 for i in items_sorted if i["status"] != CLOSED)
    closed_cnt = sum(1 for i in items_sorted if i["status"] == CLOSED)
    st.markdown(
        f"**问题条款** <span class='tc-muted'>({open_cnt} 待改 · {closed_cnt} 已改)</span>",
        unsafe_allow_html=True,
    )
    with st.container(height=680, border=False, key="rem_nav"):
        for it in items_sorted:
            is_sel = it["id"] == cur_rem_id
            icon = "✓" if it["status"] == CLOSED else "✎"
            status_txt = "已修改" if it["status"] == CLOSED else "待修改"
            meta = f"{it['category']} · {status_txt}"
            st.button(
                f"{icon} **{it['clause_no']}** {it['req_text']}  \n{meta if is_sel else f':gray[{meta}]'}",
                key=f"rem_item_{it['id']}",
                width="stretch",
                type="primary" if is_sel else "secondary",
                on_click=_select_rem,
                args=(it["id"],),
            )

# ================================================================= Right: Requirement + AI Suggestion + Direct Bid Editor
with cols[1]:
    with st.container(border=True):
        # 1. Tender Requirement
        st.markdown(f"#### {cur_res['clause_no']} · {cur_res['category']}")
        st.markdown(
            f"<div class='tc-req'><mark>Tender Requirement</mark> &nbsp;{html.escape(str(cur_res['req_text']))}</div>",
            unsafe_allow_html=True,
        )

        # Determine initial Bid volume & page to open (prefer the conflicting page B if present)
        evidence = cur_res.get("evidence") or []
        default_vol_idx = 0
        default_page = 1
        default_ev_idx = 0
        if evidence and bid_volumes:
            for e_i, ev_cand in enumerate(evidence):
                if "冲突证据 B" in str(ev_cand.get("role") or "") or "无在建工程" in str(ev_cand.get("snippet") or ""):
                    default_ev_idx = e_i
                    break
            ev0 = evidence[default_ev_idx]
            ev0_path = ev0.get("doc_path")
            ev0_page = int(ev0.get("page") or 1)
            for v_i, v in enumerate(bid_volumes):
                if ev0_path and Path(v["path"]) == Path(ev0_path):
                    default_vol_idx = v_i
                    default_page = max(1, min(v["pages"], ev0_page))
                    break
            else:
                default_page = max(1, min(bid_volumes[0]["pages"], ev0_page))

        vol_key = f"rem_bid_vol_{cur_rem_id}"
        page_key = f"rem_bid_page_{cur_rem_id}"
        if vol_key not in st.session_state:
            st.session_state[vol_key] = default_vol_idx
        cur_vol_idx = int(st.session_state[vol_key])
        if cur_vol_idx >= len(bid_volumes):
            cur_vol_idx = 0

        cur_vol = bid_volumes[cur_vol_idx] if bid_volumes else {"label": "Bid", "path": "", "pages": 1}
        max_pages = max(1, int(cur_vol["pages"]))

        if page_key not in st.session_state:
            st.session_state[page_key] = default_page
        cur_page = max(1, min(max_pages, int(st.session_state[page_key])))

        raw_bid_page_text, _orig_pdf_text = (
            real_backend.read_bid_page_text_with_edits(cur_vol["path"], cur_page)
            if cur_vol["path"]
            else ("", "")
        )
        editor_key = f"rem_direct_bid_{cur_rem_id}_{cur_vol_idx}_{cur_page}"
        if editor_key not in st.session_state:
            st.session_state[editor_key] = raw_bid_page_text or (cur_item.get("draft") or "")

        # 2. AI Modification Suggestion (DeepSeek API)
        ai_cache_key = f"rem_ai_sug_{cur_rem_id}_{cur_vol_idx}_{cur_page}"
        if st.button(
            "AI 修改建议",
            icon=":material/auto_awesome:",
            key=f"rem_ai_btn_{cur_rem_id}",
            disabled=signed,
        ):
            with st.spinner("正在调用 DeepSeek 生成修改建议..."):
                st.session_state[ai_cache_key] = real_backend.generate_remediation_suggestion(
                    clause_no=cur_res["clause_no"],
                    req_text=cur_res["req_text"],
                    explanation=cur_res.get("explanation") or cur_item.get("issue") or "",
                    orig_text=st.session_state.get(editor_key) or raw_bid_page_text,
                    all_evidence=evidence,
                )

        ai_data = st.session_state.get(ai_cache_key)
        if ai_data:
            with st.container(border=True):
                st.markdown(f"**✨ AI 修改建议：** {ai_data.get('diagnosis', '')}")
                st.markdown(
                    f"<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:10px 12px;"
                    f"font-size:0.9rem;color:#166534;white-space:pre-wrap;max-height:180px;overflow-y:auto;'>"
                    f"{html.escape(ai_data.get('replacement_span') or ai_data.get('revised_text') or '')}</div>",
                    unsafe_allow_html=True,
                )
                if st.button(
                    "应用建议到下方投标书",
                    key=f"rem_apply_ai_{cur_rem_id}",
                    type="primary",
                    icon=":material/done:",
                ):
                    st.session_state[editor_key] = ai_data.get("revised_text") or st.session_state[editor_key]
                    st.rerun()

        st.divider()

        # 3. Open Bid Document directly for editing
        nav1, nav2 = st.columns([2.8, 1.2], vertical_alignment="center")
        if len(bid_volumes) > 1:
            sel_vol = nav1.selectbox(
                "投标书分册 (Bid Volume)",
                list(range(len(bid_volumes))),
                index=cur_vol_idx,
                format_func=lambda i: f"{bid_volumes[i]['label']} ({bid_volumes[i]['pages']}p)",
                key=vol_key,
            )
            if sel_vol != cur_vol_idx:
                st.session_state[page_key] = 1
                st.rerun()
        else:
            nav1.markdown(f"**投标书原件 (Bid Document)：** `{cur_vol['label']}`")

        nav2.number_input(
            f"页码 (1 - {max_pages})",
            min_value=1,
            max_value=max_pages,
            key=page_key,
        )

        modified_text = st.text_area(
            f"Bid 原文内容（第 {cur_page} / {max_pages} 页 · 可直接在此修改）",
            height=340,
            key=editor_key,
            disabled=signed,
        )

        if st.button(
            "Save & Return to Workspace",
            type="primary",
            icon=":material/check_circle:",
            width="stretch",
            disabled=signed,
        ):
            try:
                outcome = service.apply_remediation_edit(
                    cur_rem_id,
                    cur_res["id"],
                    default_ev_idx,
                    modified_text,
                    f"已在投标书 {cur_vol['label']} 第 {cur_page} 页完成修改",
                    ui.actor(),
                    ui.role(),
                    resolve=True,
                    doc_path=cur_vol["path"],
                    page_no=cur_page,
                )
                ui.render_page.clear()
                if outcome.get("passed"):
                    ui.flash(f"已保存至投标文件并重新核验：条款 {cur_res['clause_no']} 重审通过 (✓ Compliant)！")
                else:
                    ui.flash(
                        f"已保存修改并重新核验：条款 {cur_res['clause_no']} 仍存在未消除的问题（{outcome.get('verdict')}）。",
                        "warning",
                    )
                ui.go("workspace", ws_focus_requirement=cur_res["requirement_id"])
            except ServiceError as e:
                st.error(str(e))

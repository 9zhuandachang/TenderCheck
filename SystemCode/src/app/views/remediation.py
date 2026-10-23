import html
from pathlib import Path
import re
import streamlit as st

from tcui import service, store, ui
from tcui.config import CLOSED, CONFIRMED, TO_REINSPECT, TO_REMEDIATE
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
top_l, top_r = st.columns([4, 1.3], vertical_alignment="center")
top_l.markdown(
    f"### Remediation · 原文修改工作台  \n<span class='tc-muted'>{html.escape(project['name'])}</span>",
    unsafe_allow_html=True,
)
if top_r.button("Back to Workspace", icon=":material/arrow_back:", width="stretch"):
    ui.go("workspace")

if not items:
    with st.container(border=True):
        st.info(
            "当前还没有进入整改池的条款。请在 **Review Workspace** 审标时，对需要修改投标书原文的条款点击 **To remediation**，系统会自动跳转到本页面进行原文修改。",
            icon=":material/info:",
        )
        if st.button("前往 Review Workspace", type="primary", icon=":material/compare:"):
            ui.go("workspace")
    st.stop()

# Order: open items (To remediate / To reinspect) first, then Closed
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


def _build_ai_suggestion_preview(req_text: str, orig_text: str, clause_no: str) -> str:
    """Frontend placeholder generator for AI revision preview (backend LLM not connected yet)."""
    clean_req = req_text.strip()
    if not orig_text.strip():
        return f"【响应承诺（针对 {clause_no}）】我方完全响应并满足招标文件要求：{clean_req}"
    return (
        f"{orig_text.strip()}\n\n"
        f"【AI 建议修订补充（针对 {clause_no} 招标要求）】经核实，我方郑重确认并修正如下："
        f"完全满足“{clean_req}”之规定，相关人员、证书及承诺事项均以前述核定内容为准，不存在任何前后不一致情形。"
    )


# ----------------------------------------------------------------- 3-column layout
cols = st.columns([1.05, 2.15, 1.8], gap="small")

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
                f"{icon} **{it['clause_no']}** {it['req_text'][:38]}…  \n{meta if is_sel else f':gray[{meta}]'}",
                key=f"rem_item_{it['id']}",
                width="stretch",
                type="primary" if is_sel else "secondary",
                on_click=_select_rem,
                args=(it["id"],),
            )

# ================================================================= Middle: Issue & Original Text Editor
with cols[1]:
    with st.container(border=True):
        h1, h2 = st.columns([2.5, 2.5], vertical_alignment="center")
        h1.markdown(f"#### {cur_res['clause_no']} · {cur_res['category']}")
        rem_badge = (
            ":green-badge[:material/check_circle: 已修改原文]"
            if cur_item["status"] == CLOSED
            else ":violet-badge[:material/build: 待修改原文]"
        )
        h2.markdown(f"{ui.verdict_md(cur_res['verdict'])} {rem_badge}")

        st.markdown(
            f"<div class='tc-req'><mark>招标要求 (Tender Requirement)</mark> &nbsp;{html.escape(str(cur_res['req_text']))}</div>",
            unsafe_allow_html=True,
        )
        if cur_res.get("fields"):
            ui.comparison_table(cur_res["fields"])
        st.caption(f"**问题说明：** {cur_res.get('explanation') or cur_item.get('issue') or ''}")

    # Evidence page selection & direct original text editing
    evidence = cur_res.get("evidence") or []
    ev_idx = 0
    with st.container(border=True):
        st.markdown("##### 直接修改投标文件原文 (Modify Bid Original Text)")

        if evidence:
            page_freq = {}
            for item in evidence:
                page_freq[item["page"]] = page_freq.get(item["page"], 0) + 1

            def _fmt_ev_tab(i: int) -> str:
                ev_item = evidence[i]
                pg = ev_item["page"]
                if page_freq.get(pg, 1) <= 1:
                    return f"p.{pg}"
                raw_lbl = str(ev_item.get("doc_label") or "").replace(".pdf", "").replace(".PDF", "")
                short_lbl = re.sub(r"^\d+\.?|[（）()]", "", raw_lbl).strip()
                tag = short_lbl[-4:] if len(short_lbl) > 4 else (short_lbl or f"#{i + 1}")
                return f"p.{pg}·{tag}"

            ev_idx = st.segmented_control(
                "选择要修改的投标书证据页",
                list(range(len(evidence))),
                default=0,
                key=f"rem_ev_sel_{cur_rem_id}",
                required=True,
                format_func=_fmt_ev_tab,
                help="切换该条款关联的不同投标书页面以定位并修改原文",
                width="stretch",
                wrap=False,
            ) or 0
            cur_ev = evidence[ev_idx]
            target_desc = f"{cur_ev.get('doc_label') or '投标文件'} · 第 {cur_ev['page']} 页 ({cur_ev.get('role') or ''})"
            st.caption(f"当前定位位置：**{target_desc}**")
            orig_text = (
                cur_ev.get("original_text")
                or cur_ev.get("rag_text")
                or cur_ev.get("snippet")
                or ""
            )
            current_edit_default = (
                cur_ev.get("rag_text")
                or cur_item.get("draft")
                or cur_ev.get("snippet")
                or ""
            )
        else:
            cur_ev = None
            st.caption("当前条款未检索到现成段落，可直接下方编写补充响应原文。")
            orig_text = "（原投标书中未检出对应段落）"
            current_edit_default = cur_item.get("draft") or ""

        with st.expander("查看修改前投标书原文 (Original Bid Text)", expanded=True):
            st.markdown(
                f"<div style='background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;"
                f"font-size:0.9rem;color:#334155;max-height:150px;overflow-y:auto;white-space:pre-wrap;'>"
                f"{html.escape(orig_text)}</div>",
                unsafe_allow_html=True,
            )

        # AI Suggestion Toolbar (Frontend UI preview only, backend not wired yet)
        ai_col1, ai_col2 = st.columns([1.4, 2.6], vertical_alignment="center")
        ai_state_key = f"rem_ai_show_{cur_rem_id}_{ev_idx}"
        editor_key = f"rem_editor_{cur_rem_id}_{ev_idx}"

        if ai_col1.button(
            "AI 修改建议",
            icon=":material/auto_awesome:",
            key=f"rem_ai_btn_{cur_rem_id}_{ev_idx}",
            width="stretch",
            disabled=signed,
        ):
            st.session_state[ai_state_key] = True

        ai_col2.caption("生成针对该条款的合规原文改写方案（前端演示，暂未接入后端模型）")

        if st.session_state.get(ai_state_key):
            preview_suggestion = _build_ai_suggestion_preview(
                cur_res["req_text"], orig_text, cur_res["clause_no"]
            )
            with st.container(border=True):
                st.markdown("**✨ AI 建议修改稿（预览演示）**")
                st.markdown(
                    f"<div style='background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:8px 10px;"
                    f"font-size:0.9rem;color:#166534;white-space:pre-wrap;'>{html.escape(preview_suggestion)}</div>",
                    unsafe_allow_html=True,
                )
                sc1, sc2 = st.columns([1.5, 1.5])
                if sc1.button("填入下方编辑框", key=f"rem_ai_apply_{cur_rem_id}_{ev_idx}", type="primary", width="stretch"):
                    st.session_state[editor_key] = preview_suggestion
                    st.session_state[ai_state_key] = False
                    st.rerun()
                if sc2.button("收起建议", key=f"rem_ai_hide_{cur_rem_id}_{ev_idx}", width="stretch"):
                    st.session_state[ai_state_key] = False
                    st.rerun()

        if editor_key not in st.session_state:
            st.session_state[editor_key] = current_edit_default

        modified_text = st.text_area(
            "修改后投标书原文 (可直接在此编辑原文)",
            height=200,
            key=editor_key,
            disabled=signed,
        )
        rem_note = st.text_input(
            "修改说明 / 备注 (可选)",
            value=cur_res.get("note") or "",
            key=f"rem_note_{cur_rem_id}",
            placeholder="例如：已将投标函项目经理在建表述与承诺书统一修改",
            disabled=signed,
        )

        b1, b2, b3 = st.columns([2.1, 1.2, 1.3])
        if b1.button(
            "Save & Return to Workspace",
            type="primary",
            icon=":material/check_circle:",
            width="stretch",
            disabled=signed,
        ):
            try:
                service.apply_remediation_edit(
                    cur_rem_id,
                    cur_res["id"],
                    ev_idx,
                    modified_text,
                    rem_note,
                    ui.actor(),
                    ui.role(),
                    resolve=True,
                )
                ui.flash(f"已完成条款 {cur_res['clause_no']} 原文修改并确认，正在返回 Review Workspace。")
                ui.go("workspace", ws_focus_requirement=cur_res["requirement_id"])
            except ServiceError as e:
                st.error(str(e))

        if b2.button(
            "暂存草稿",
            icon=":material/save:",
            width="stretch",
            disabled=signed,
        ):
            try:
                service.apply_remediation_edit(
                    cur_rem_id,
                    cur_res["id"],
                    ev_idx,
                    modified_text,
                    rem_note,
                    ui.actor(),
                    ui.role(),
                    resolve=False,
                )
                ui.flash(f"条款 {cur_res['clause_no']} 原文修改草稿已暂存。")
                st.rerun()
            except ServiceError as e:
                st.error(str(e))

        if b3.button(
            "返回审标",
            icon=":material/arrow_back:",
            width="stretch",
        ):
            ui.go("workspace", ws_focus_requirement=cur_res["requirement_id"])

# ================================================================= Right: PDF Document Reference
with cols[2]:
    pdf_mode = st.segmented_control(
        "PDF 预览源",
        ["投标书原件 (BID)", "招标书原件 (TENDER)"],
        default="投标书原件 (BID)",
        key=f"rem_pdf_mode_{cur_rem_id}",
        required=True,
        label_visibility="collapsed",
        width="stretch",
    )
    if pdf_mode == "招标书原件 (TENDER)":
        t_doc = docs.get(cur_res["source_doc_id"])
        t_page = cur_res["req_page"]
        st.markdown(
            f"<div class='tc-pane-title'><b>TENDER</b> <span class='tc-muted'>{html.escape(t_doc['label'] if t_doc else '')}</span></div>",
            unsafe_allow_html=True,
        )
        ui.pdf_pane("rem_tender", t_doc, t_page, cur_res["req_text"])
    else:
        bid_doc = docs.get(rnd["bid_doc_id"])
        if evidence and cur_ev:
            ev_doc = bid_doc
            if cur_ev.get("doc_path") and Path(cur_ev["doc_path"]).exists():
                ev_doc = {
                    "id": cur_ev.get("document_id") or (bid_doc["id"] if bid_doc else 0),
                    "path": cur_ev["doc_path"],
                    "label": cur_ev.get("doc_label") or (bid_doc["label"] if bid_doc else "Bid"),
                    "pages": cur_ev.get("doc_pages") or (bid_doc["pages"] if bid_doc else 1),
                }
            st.markdown(
                f"<div class='tc-pane-title'><b>BID</b> <span class='tc-muted'>{html.escape(ev_doc['label'] if ev_doc else '')}</span></div>",
                unsafe_allow_html=True,
            )
            ui.pdf_pane(
                "rem_bid",
                ev_doc,
                cur_ev["page"],
                cur_ev.get("snippet", ""),
                secondary_highlight=cur_ev.get("original_text") or cur_ev.get("rag_text") or cur_ev.get("snippet", ""),
            )
        else:
            st.markdown(
                f"<div class='tc-pane-title'><b>BID</b> <span class='tc-muted'>{html.escape(bid_doc['label'] if bid_doc else '')}</span></div>",
                unsafe_allow_html=True,
            )
            ui.pdf_pane("rem_bid", bid_doc, 1)

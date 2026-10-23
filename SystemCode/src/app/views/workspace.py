import html
from pathlib import Path
import re
import streamlit as st

from tcui import config, engine, real_backend, service, store, ui
from tcui.config import COMPLIANT, CONFIRMED, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE, UNREVIEWED
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()

rounds = [r for r in store.list_rounds(pid) if r["status"] == "done"]
if not rounds:
    st.title("Review Workspace")
    ui.job_progress(pid, "review")
    st.info("No review results yet. Confirm the requirement list and start the review from the Overview page.")
    st.page_link(ui.PAGES["overview"], label="Go to Overview", icon=":material/dashboard:")
    st.stop()

docs = ui.doc_map(pid)
rnd = rounds[0]
round_id = rnd["id"]
st.session_state["ws_round"] = round_id

# ----------------------------------------------------------------- top bar
top = st.columns([3.5, 2.5], vertical_alignment="center")
used = [docs[d]["label"] for d in (rnd["doc_ids"] or []) if d in docs]
top[0].markdown(f"**{project['name']}**  \n<span class='tc-muted'>{' · '.join(used)}</span>", unsafe_allow_html=True)
summary = service.round_summary(round_id)
np_total = summary.get("non_passing", summary["non_compliant"] + summary["insufficient"])
all_total = summary.get("applicable") or summary.get("total") or 1
top[1].progress(
    np_total / all_total if all_total else 0.0,
    text=f"Non-passing / All: {np_total}/{all_total}",
)
signed = bool(rnd["signed_at"])
if signed:
    st.info(f"Signed off by {rnd['signer']}; it is read-only.", icon=":material/lock:")

# ----------------------------------------------------------------- clause filters
results = store.list_results(round_id)
result_by_id = {r["id"]: r for r in results}

REASON_TAG_MAP = {
    label: code for code, (label, _color) in real_backend.SUB_VERDICT_LABELS.items()
}
REASON_TAG_OPTIONS = list(REASON_TAG_MAP.keys())

FILTERS = [
    "All",
    NON_COMPLIANT,
    INSUFFICIENT,
    COMPLIANT,
    "Unreviewed",
    "In remediation",
]
if st.session_state.get("ws_filter") not in FILTERS:
    st.session_state["ws_filter"] = "All"

f1, f2, f3, f4 = st.columns([1.8, 2.0, 1.8, 1.6], vertical_alignment="bottom")
flt = f1.selectbox("Verdict / Status", FILTERS, key="ws_filter")
reason_tags = f2.multiselect(
    "Result (Reason Tag)",
    REASON_TAG_OPTIONS,
    key="ws_reason_tags",
    placeholder="All results",
)
cats = f3.multiselect("Category", config.CATEGORIES, key="ws_cats", placeholder="All categories")
search = f4.text_input("Search", key="ws_search", placeholder="Clause No. (e.g. T0056)")


def _match(r):
    if not r["applicable"] and flt != "All":
        return False
    r_sub = (r.get("chain") or {}).get("sub_verdict")
    if flt in (NON_COMPLIANT, INSUFFICIENT, COMPLIANT) and r["verdict"] != flt:
        return False
    if flt == "Unreviewed" and r["human_status"] != UNREVIEWED:
        return False
    if flt == "In remediation" and r["human_status"] not in (TO_REMEDIATE, TO_REINSPECT):
        return False
    if reason_tags:
        allowed_codes = {REASON_TAG_MAP[t] for t in reason_tags if t in REASON_TAG_MAP}
        if r_sub not in allowed_codes:
            return False
    if cats and r["category"] not in cats:
        return False
    q = search.strip().lower()
    return not q or q in r["clause_no"].lower()


shown = [r for r in results if _match(r)]
ids = [r["id"] for r in shown]


def _pick_default_non_passing(candidates):
    for item in candidates:
        sub_v = (item.get("chain") or {}).get("sub_verdict")
        if item["applicable"] and item["human_status"] == UNREVIEWED and (
            item["verdict"] == NON_COMPLIANT or sub_v == "CONFLICTING_EVIDENCE"
        ):
            return item["id"]
    for item in candidates:
        if item["applicable"] and item["verdict"] != COMPLIANT and item["human_status"] == UNREVIEWED:
            return item["id"]
    for item in candidates:
        if item["applicable"] and item["verdict"] != COMPLIANT:
            return item["id"]
    return candidates[0]["id"]


focus_req = st.session_state.pop("ws_focus_requirement", None)
if focus_req:
    hit = next((r["id"] for r in results if r["requirement_id"] == focus_req), None)
    if hit:
        st.session_state["ws_result"] = hit
if st.session_state.get("ws_result") not in result_by_id:
    st.session_state["ws_result"] = _pick_default_non_passing(results)
current_id = st.session_state["ws_result"]
res = result_by_id[current_id]
ui.start_timer(current_id)
if project["last_requirement_id"] != res["requirement_id"]:
    store.update("projects", pid, {"last_requirement_id": res["requirement_id"]})


def _select(result_id):
    st.session_state["ws_result"] = result_id


# ----------------------------------------------------------------- navigator + documents
cols = st.columns([1.15, 2, 2], gap="small")
with cols[0]:
    st.markdown(f"**Clauses** <span class='tc-muted'>({len(shown)} of {len(results)})</span>",
                unsafe_allow_html=True)
    with st.container(height=640, border=False, key="ws_nav"):
        if not shown:
            st.info("No clauses match the filters.")
        for r in shown:
            mark = ui.verdict_icon(r["verdict"]) if r["applicable"] else "–"
            status = r["human_status"] if r["applicable"] else "N/A"
            selected = r["id"] == current_id
            meta = f"{r['category']} · {status}"
            st.button(
                f"{mark} **{r['clause_no']}** {r['req_text']}  \n{meta if selected else f':gray[{meta}]'}",
                key=f"nav_{r['id']}", width="stretch", on_click=_select, args=(r["id"],),
                type="primary" if selected else "secondary",
            )
    pos = ids.index(current_id) if current_id in ids else -1
    prev_target = next(
        (r["id"] for r in reversed(shown[:pos]) if r["applicable"] and r["human_status"] != CONFIRMED),
        None,
    ) if pos > 0 else None
    next_target = next(
        (r["id"] for r in shown[pos + 1:] if r["applicable"] and r["human_status"] != CONFIRMED),
        None,
    ) if pos >= 0 else next(
        (r["id"] for r in shown if r["applicable"] and r["human_status"] != CONFIRMED),
        None,
    )
    n1, n2 = st.columns(2)
    n1.button("Previous", icon=":material/arrow_upward:", width="stretch",
              disabled=prev_target is None,
              on_click=_select, args=(prev_target or current_id,))
    n2.button("Next", icon=":material/arrow_downward:", width="stretch",
              disabled=next_target is None,
              on_click=_select, args=(next_target or current_id,))
    st.components.v1.html(
        f"""<script>
        setTimeout(() => {{
            const doc = window.parent.document;
            const nav = doc.querySelector('.st-key-ws_nav');
            if (nav) {{
                const activeBtn = nav.querySelector('button[kind="primary"]');
                if (activeBtn) {{
                    activeBtn.scrollIntoView({{block: 'center', behavior: 'instant'}});
                }}
            }}
        }}, 80);
        </script><!-- {current_id} -->""",
        height=0,
    )

with cols[1]:
    amended = res["amendment_status"] == "accepted" and res["amended_by_doc_id"]
    view = "Addendum"
    head, ctrl = st.container(border=False, key="ws_tender_head").columns([1, 1.3], vertical_alignment="center")
    if amended and res["origin"] != "addendum":
        view = ctrl.segmented_control("Tender source", ["Addendum", "Original"], default="Addendum",
                                      key=f"ws_src_{res['requirement_id']}", required=True,
                                      label_visibility="collapsed", wrap=False)
    if amended and view == "Addendum":
        t_doc, t_page = docs.get(res["amended_by_doc_id"]), res["amendment_page"]
    else:
        t_doc, t_page = docs.get(res["source_doc_id"]), res["req_page"]
    head.markdown(
        f"<div class='tc-pane-title'><b>TENDER</b> <span class='tc-muted'>{html.escape(t_doc['label'] if t_doc else '')}</span></div>",
        unsafe_allow_html=True,
    )
    tender_text = res["req_text"] if not (amended and view == "Original") else ""
    tender_raw = (res.get("original") or {}).get("raw_text", "") if isinstance(res.get("original"), dict) else ""
    ui.pdf_pane(
        "tender",
        t_doc,
        t_page,
        tender_text if res["origin"] != "drawing" else "",
        allow_page_nav=False,
        secondary_highlight=tender_raw if res["origin"] != "drawing" else "",
    )

with cols[2]:
    bid_doc = docs.get(rnd["bid_doc_id"])
    evidence = res["evidence"] or []
    ev_key = f"ws_ev_{current_id}"
    head, ctrl = st.container(border=False, key="ws_bid_head").columns([1, 1.3], vertical_alignment="center")
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

        idx = ctrl.segmented_control(
            "Evidence", list(range(len(evidence))), default=0, key=ev_key, required=True,
            format_func=_fmt_ev_tab, label_visibility="collapsed",
            help="Evidence pages: verified quotes first, then retrieved candidate pages",
            width="stretch", wrap=False,
        )
        ev = evidence[idx or 0]
        # Support multi-document bid evidence when `ev` specifies `doc_path`
        ev_doc = bid_doc
        if ev.get("doc_path") and Path(ev["doc_path"]).exists():
            ev_doc = {
                "id": ev.get("document_id") or (bid_doc["id"] if bid_doc else 0),
                "path": ev["doc_path"],
                "label": ev.get("doc_label") or (bid_doc["label"] if bid_doc else "Bid"),
                "pages": ev.get("doc_pages") or (bid_doc["pages"] if bid_doc else 1),
            }
        head.markdown(
            f"<div class='tc-pane-title' title='{html.escape(ev.get('role', ''))}'>"
            f"<b>BID</b> <span class='tc-muted'>{html.escape(ev_doc['label'] if ev_doc else '')}</span></div>",
            unsafe_allow_html=True,
        )
        ui.pdf_pane(
            "bid",
            ev_doc,
            ev["page"],
            ev.get("snippet", ""),
            secondary_highlight=ev.get("rag_text") or ev.get("snippet", ""),
        )
    else:
        head.markdown(
            f"<div class='tc-pane-title'><b>BID</b> <span class='tc-muted'>{html.escape(bid_doc['label'] if bid_doc else '')} · no evidence found</span></div>",
            unsafe_allow_html=True,
        )
        ui.pdf_pane("bid", bid_doc, 1)

# ----------------------------------------------------------------- verification panel
with st.container(border=True):
    h1, h2 = st.columns([2.6, 2.4], vertical_alignment="center")
    tags = [ui.verdict_md(res["verdict"])]
    tags.append(ui.status_md(res["human_status"] if res["applicable"] else "Not applicable"))
    if res["corrected"]:
        tags.append(f":gray-badge[Corrected · system said {res['original_verdict']}]")
    h1.markdown(f"#### {res['clause_no']} · {res['category']}")
    h2.markdown(" ".join(tags))

    # 1. Yellow Tender Requirement box
    st.markdown(
        f"<div class='tc-req'><mark>Tender Requirement</mark> &nbsp;{html.escape(str(res['req_text']))}</div>",
        unsafe_allow_html=True,
    )

    # 2. Selectable Bid Response RAG passages (synced with the BID PDF viewer above)
    if evidence:
        def _sync_ev_from_rag() -> None:
            st.session_state[ev_key] = st.session_state[f"ws_rag_sel_{current_id}"]

        def _fmt_rag_option(i: int) -> str:
            ev_item = evidence[i]
            eid = ev_item.get("evidence_id") or f"#{i + 1}"
            pg = ev_item["page"]
            role_txt = str(ev_item.get("role") or "")
            if "冲突" in role_txt:
                prefix = "⚡ "
            elif "★" in role_txt or "核验" in role_txt or "锚点" in role_txt:
                prefix = "★ "
            elif "✎" in role_txt:
                prefix = "✎ "
            else:
                prefix = ""
            return f"{prefix}{eid} · p.{pg}"

        active_ev_idx = int(st.session_state.get(ev_key) or 0)
        if active_ev_idx >= len(evidence):
            active_ev_idx = 0
        st.session_state[f"ws_rag_sel_{current_id}"] = active_ev_idx

        rag_sel_idx = st.segmented_control(
            "Bid content (RAG 候选段落)",
            list(range(len(evidence))),
            key=f"ws_rag_sel_{current_id}",
            required=True,
            format_func=_fmt_rag_option,
            on_change=_sync_ev_from_rag,
            label_visibility="collapsed",
            width="stretch",
            wrap=False,
        )
        sel_ev = evidence[rag_sel_idx if rag_sel_idx is not None else active_ev_idx]
        saved_page_edit = real_backend.get_bid_page_edit(
            sel_ev.get("doc_path") or (bid_doc["path"] if bid_doc else ""),
            int(sel_ev.get("page") or 1),
        )
        rag_body = (
            (saved_page_edit.get("modified_text") if saved_page_edit else None)
            or sel_ev.get("rag_text")
            or sel_ev.get("snippet")
            or ""
        ).strip()
        quote_span = (sel_ev.get("snippet") or "").strip()
        escaped_body = html.escape(rag_body)
        if quote_span and len(quote_span) < len(rag_body) and html.escape(quote_span) in escaped_body:
            escaped_quote = html.escape(quote_span)
            escaped_body = escaped_body.replace(
                escaped_quote, f"<mark class='tc-quote'>{escaped_quote}</mark>", 1
            )
        escaped_body = escaped_body.replace("\r\n", "\n").replace("\n", "<br/>")
        meta_line = (
            f"{html.escape(str(sel_ev.get('doc_label') or 'Bid'))} · "
            f"p.{sel_ev['page']} · {html.escape(str(sel_ev.get('role') or ''))}"
        )
        st.markdown(
            f"<div class='tc-bid' style='min-height: 160px; max-height: 360px; overflow-y: auto;'>"
            f"<mark class='tc-tag'>Bid content</mark> "
            f"<span class='tc-muted'>{meta_line}</span><br/>{escaped_body}</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div class='tc-bid' style='min-height: 160px; max-height: 360px; overflow-y: auto;'>"
            "<mark class='tc-tag'>Bid content</mark> "
            "<span class='tc-muted'>未在投标书中检索到对应候选段落 (No RAG evidence retrieved)</span></div>",
            unsafe_allow_html=True,
        )

    # 3. Field structured comparison table
    ui.comparison_table(res["fields"] or [])

    # 4. Verdict detail (e.g., 【⚠ Conflicting Evidence】 / 【🌐 External Web Check】 + explanation)
    if res.get("explanation"):
        st.markdown(f"{res['explanation']}")
    if not res["applicable"]:
        st.caption(f"Not applicable: {res['na_reason']}")

    # 5. Reasoning chain
    chain = res["chain"] or {}
    with st.expander("Reasoning chain", icon=":material/account_tree:"):
        if "classify" in chain:
            c = chain["classify"]
            ctype_info = f" · `{c['clause_type']}`" if c.get("clause_type") else ""
            score = f" · keyword share {c['score']:.2f}" if c.get("score") is not None else ""
            st.markdown(f"**1 · Classify** → {c['category']}{ctype_info}{score}  \n:gray[{c['method']}]")
        if "retrieve" in chain:
            c = chain["retrieve"]
            hits_list = c.get("hits") or []
            hits = (
                ", ".join(
                    f"{h.get('evidence_id', '') + ' ' if h.get('evidence_id') else ''}p.{h['page']} ({h['score']:.2f})"
                    for h in hits_list
                )
                or "no pages"
            )
            kw = ", ".join(c.get("keywords") or [])
            kw_str = f"; slots: {kw}" if kw else ""
            st.markdown(f"**2 · Retrieve** → {hits}  \n:gray[{c['method']}{kw_str}]")
        if "extract" in chain:
            c = chain["extract"]
            act = c.get("actual") or {}
            if act.get("value") is not None:
                bid_val = engine.format_value(act["value"], act.get("unit") or "")
            elif act.get("text"):
                bid_val = str(act["text"])[:60]
            elif "matched" in act or "terms" in act:
                bid_val = f"{act.get('matched', 0)}/{act.get('terms', 0)} terms matched"
            else:
                bid_val = "structured slots extracted"
            if act.get("page"):
                bid_val += f" (p.{act['page']})"
            st.markdown(f"**3 · Extract** → required `{engine.describe_constraint(c.get('required') or {})}`; "
                        f"bid `{bid_val}`  \n:gray[{c['method']}]")
        if "rule" in chain:
            c = chain["rule"]
            st.markdown(f"**4 · Rule** `{c['rule_id']}` ({c['rule_version']})  \n`{c['trace']}`")
        st.caption("Retrieval scores describe search relevance only; they are not a compliance confidence.")

    # ------------------------------------------------------------- actions
    note = ""
    a1, a2 = st.columns(2)

    def _run(fn, *args, success=""):
        try:
            fn(*args)
            if success:
                ui.flash(success)
        except ServiceError as e:
            ui.flash(str(e), "error")

    def _confirm():
        _run(lambda: service.confirm_result(current_id, ui.actor(), ui.role(), note, ui.take_elapsed()),
             success=f"Clause {res['clause_no']} confirmed.")
        if current_id in ids:
            curr_idx = ids.index(current_id)
            ordered = shown[curr_idx + 1:] + shown[:curr_idx]
            next_non_passing = next(
                (r["id"] for r in ordered if r["applicable"] and r["verdict"] != COMPLIANT and r["human_status"] == UNREVIEWED),
                None,
            )
            if next_non_passing is not None:
                st.session_state["ws_result"] = next_non_passing
            elif curr_idx < len(ids) - 1:
                st.session_state["ws_result"] = ids[curr_idx + 1]

    a1.button("Confirm", type="primary", icon=":material/check:", width="stretch", on_click=_confirm,
              disabled=signed or not res["applicable"])

    if a2.button("To remediation", icon=":material/build:", width="stretch",
                 disabled=signed or res["verdict"] == COMPLIANT or not res["applicable"]):
        try:
            rem_id = service.send_to_remediation(current_id, note, ui.actor(), ui.role(), ui.take_elapsed())
            ui.go("remediation", rem_focus_id=rem_id, rem_focus_req=res["requirement_id"])
        except ServiceError as e:
            st.error(str(e))

    history = store.list_actions(pid, res["requirement_id"])
    if history:
        with st.expander(f"History ({len(history)})"):
            ui.table([{"Time": h["at"].replace("T", " "), "Who": f"{h['actor']} ({h['role']})", "Action": h["action"],
                       "Note": h["note"] or ""} for h in history])

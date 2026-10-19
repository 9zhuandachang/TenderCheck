import streamlit as st

from tcui import config, engine, service, store, ui
from tcui.config import COMPLIANT, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE, UNREVIEWED
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
round_ids = [r["id"] for r in rounds]
if st.session_state.get("ws_round") not in round_ids:
    st.session_state["ws_round"] = round_ids[0]
by_round = {r["id"]: r for r in rounds}

# ----------------------------------------------------------------- top bar
top = st.columns([2.2, 3, 1.6, 1.2], vertical_alignment="center")
round_id = top[0].selectbox(
    "Round", round_ids, key="ws_round", label_visibility="collapsed",
    format_func=lambda i: f"Round {by_round[i]['no']} · {docs.get(by_round[i]['bid_doc_id'], {}).get('label', '')}"
                          + (" · signed" if by_round[i]["signed_at"] else ""),
)
rnd = by_round[round_id]
used = [docs[d]["label"] for d in (rnd["doc_ids"] or []) if d in docs]
top[1].markdown(f"<span class='tc-muted'>{' · '.join(used)}</span>", unsafe_allow_html=True)
summary = service.round_summary(round_id)
top[2].progress(summary["reviewed"] / summary["applicable"] if summary["applicable"] else 0.0,
                text=f"Reviewed {summary['reviewed']}/{summary['applicable']}")
top[3].page_link(ui.PAGES["report"], label="Report", icon=":material/description:")
signed = bool(rnd["signed_at"])
if signed:
    st.info(f"Round {rnd['no']} was signed off by {rnd['signer']}; it is read-only.", icon=":material/lock:")

# ----------------------------------------------------------------- filters
results = store.list_results(round_id)
FILTERS = ["All", NON_COMPLIANT, INSUFFICIENT, COMPLIANT, "Unreviewed", "In remediation", "Not applicable"]
if st.session_state.get("ws_filter") not in FILTERS:
    st.session_state["ws_filter"] = "All"
SHORT = {INSUFFICIENT: "Insufficient", "In remediation": "Remediation", "Not applicable": "N/A"}
f1, f2, f3, f4 = st.columns([6.6, 1.4, 1.3, 0.7], vertical_alignment="bottom")
flt = f1.segmented_control("Show", FILTERS, key="ws_filter", required=True, format_func=lambda f: SHORT.get(f, f))
cats = f2.multiselect("Category", config.CATEGORIES, key="ws_cats", placeholder="All categories")
search = f3.text_input("Search", key="ws_search", placeholder="Clause or text")
show_nav = f4.toggle("List", value=True, key="ws_show_nav")


def _match(r):
    if flt == "Not applicable":
        if r["applicable"]:
            return False
    elif not r["applicable"] and flt != "All":
        return False
    if flt in (NON_COMPLIANT, INSUFFICIENT, COMPLIANT) and r["verdict"] != flt:
        return False
    if flt == "Unreviewed" and r["human_status"] != UNREVIEWED:
        return False
    if flt == "In remediation" and r["human_status"] not in (TO_REMEDIATE, TO_REINSPECT):
        return False
    if cats and r["category"] not in cats:
        return False
    q = search.strip().lower()
    return not q or q in r["clause_no"].lower() or q in r["req_text"].lower()


shown = [r for r in results if _match(r)]
if not shown:
    st.info("No clauses match the filters.")
    st.stop()

ids = [r["id"] for r in shown]
focus_req = st.session_state.pop("ws_focus_requirement", None)
if focus_req:
    hit = next((r["id"] for r in shown if r["requirement_id"] == focus_req), None)
    if hit:
        st.session_state["ws_result"] = hit
if st.session_state.get("ws_result") not in ids:
    st.session_state["ws_result"] = ids[0]
current_id = st.session_state["ws_result"]
res = next(r for r in shown if r["id"] == current_id)
ui.start_timer(current_id)
if project["last_requirement_id"] != res["requirement_id"]:
    store.update("projects", pid, {"last_requirement_id": res["requirement_id"]})


def _select(result_id):
    st.session_state["ws_result"] = result_id


# ----------------------------------------------------------------- navigator + documents
cols = st.columns([1.15, 2, 2], gap="small") if show_nav else [None] + st.columns(2, gap="small")
if show_nav:
    with cols[0]:
        st.markdown(f"**Clauses** <span class='tc-muted'>({len(shown)} of {len(results)})</span>",
                    unsafe_allow_html=True)
        with st.container(height=640, border=False, key="ws_nav"):
            for r in shown:
                mark = ui.verdict_icon(r["verdict"]) if r["applicable"] else "–"
                status = r["human_status"] if r["applicable"] else "N/A"
                selected = r["id"] == current_id
                meta = f"{r['category']} · {status}"
                st.button(
                    f"{mark} **{r['clause_no']}** {r['req_text'][:44]}…  \n{meta if selected else f':gray[{meta}]'}",
                    key=f"nav_{r['id']}", width="stretch", on_click=_select, args=(r["id"],),
                    type="primary" if selected else "secondary",
                )
        pos = ids.index(current_id)
        n1, n2 = st.columns(2)
        n1.button("Previous", icon=":material/arrow_upward:", width="stretch", disabled=pos == 0,
                  on_click=_select, args=(ids[max(pos - 1, 0)],))
        n2.button("Next", icon=":material/arrow_downward:", width="stretch", disabled=pos == len(ids) - 1,
                  on_click=_select, args=(ids[min(pos + 1, len(ids) - 1)],))

with cols[1]:
    amended = res["amendment_status"] == "accepted" and res["amended_by_doc_id"]
    view = "Addendum"
    head, ctrl = st.container(height=44, border=False).columns([1, 1.3], vertical_alignment="center")
    if amended:
        view = ctrl.segmented_control("Tender source", ["Addendum", "Original"], default="Addendum",
                                      key=f"ws_src_{res['requirement_id']}", required=True,
                                      label_visibility="collapsed")
    if amended and view == "Addendum":
        t_doc, t_page = docs.get(res["amended_by_doc_id"]), res["amendment_page"]
    else:
        t_doc, t_page = docs.get(res["source_doc_id"]), res["req_page"]
    head.markdown(f"**TENDER** <span class='tc-muted'>{t_doc['label'] if t_doc else ''}</span>",
                  unsafe_allow_html=True)
    tender_text = res["req_text"] if not (amended and view == "Original") else ""
    ui.pdf_pane("tender", t_doc, t_page, tender_text if res["origin"] != "drawing" else "")

with cols[2]:
    bid_doc = docs.get(rnd["bid_doc_id"])
    evidence = res["evidence"] or []
    ev_key = f"ws_ev_{current_id}"
    head, ctrl = st.container(height=44, border=False).columns([1, 1.3], vertical_alignment="center")
    if evidence:
        idx = ctrl.segmented_control(
            "Evidence", list(range(len(evidence))), default=0, key=ev_key, required=True,
            format_func=lambda i: f"p.{evidence[i]['page']}", label_visibility="collapsed",
            help="Evidence pages: the extracted value first, then other retrieved pages",
        )
        ev = evidence[idx or 0]
        head.markdown(f"**BID** <span class='tc-muted'>{bid_doc['label']}</span>", unsafe_allow_html=True,
                      help=ev.get("role", ""))
        ui.pdf_pane("bid", bid_doc, ev["page"], ev.get("snippet", ""))
    else:
        head.markdown(f"**BID** <span class='tc-muted'>{bid_doc['label']} · no evidence found</span>",
                      unsafe_allow_html=True)
        ui.pdf_pane("bid", bid_doc, 1)

# ----------------------------------------------------------------- verification panel
with st.container(border=True):
    h1, h2 = st.columns([3, 2], vertical_alignment="center")
    tags = [ui.verdict_md(res["verdict"]), ui.status_md(res["human_status"] if res["applicable"] else "Not applicable")]
    if res["corrected"]:
        tags.append(f":gray-badge[Corrected · system said {res['original_verdict']}]")
    h1.markdown(f"#### {res['clause_no']} · {res['category']}")
    h2.markdown(" ".join(tags))
    st.markdown(f"<div class='tc-req'>{res['req_text']}</div>", unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="medium")
    with left:
        ui.comparison_table(res["fields"] or [])
        st.markdown(f"{res['explanation'] or ''}")
        if not res["applicable"]:
            st.caption(f"Not applicable: {res['na_reason']}")
        if res["note"]:
            st.caption(f"Reviewer note: {res['note']}")
    with right:
        with st.expander("Reasoning chain", icon=":material/account_tree:"):
            chain = res["chain"] or {}
            if "classify" in chain:
                c = chain["classify"]
                score = f" · keyword share {c['score']:.2f}" if c.get("score") is not None else ""
                st.markdown(f"**1 · Classify** → {c['category']}{score}  \n:gray[{c['method']}]")
            if "retrieve" in chain:
                c = chain["retrieve"]
                hits = ", ".join(f"p.{h['page']} ({h['score']:.2f})" for h in c["hits"]) or "no pages"
                st.markdown(f"**2 · Retrieve** → {hits}  \n:gray[{c['method']}; terms: {', '.join(c['keywords'])}]")
            if "extract" in chain:
                c = chain["extract"]
                act = c["actual"] or {}
                if "value" in act:
                    bid_val = engine.format_value(act["value"], act.get("unit") or "")
                else:
                    bid_val = f"{act.get('matched', 0)}/{act.get('terms', 0)} terms matched"
                if act.get("page"):
                    bid_val += f" (p.{act['page']})"
                st.markdown(f"**3 · Extract** → required `{engine.describe_constraint(c['required'])}`; "
                            f"bid `{bid_val}`  \n:gray[{c['method']}]")
            if "rule" in chain:
                c = chain["rule"]
                st.markdown(f"**4 · Rule** `{c['rule_id']}` ({c['rule_version']})  \n`{c['trace']}`")
            st.caption("Retrieval scores describe search relevance only; they are not a compliance confidence.")

    # ------------------------------------------------------------- actions
    note = st.text_input("Reviewer note", key=f"ws_note_{current_id}", placeholder="Optional note for the record",
                         disabled=signed)
    a1, a2, a3, a4, a5 = st.columns(5)

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
        if current_id in ids and ids.index(current_id) < len(ids) - 1:
            st.session_state["ws_result"] = ids[ids.index(current_id) + 1]

    a1.button("Confirm", type="primary", icon=":material/check:", width="stretch", on_click=_confirm,
              disabled=signed or not res["applicable"])

    @st.dialog("Correct extracted values")
    def correct_dialog():
        c = dict(res["constraint"] or {})
        if c.get("type") != "numeric":
            st.info("This clause is checked for presence, not a value. Use **Add evidence** to point at the "
                    "right bid page, then confirm with a note.")
            return
        st.caption("The rule is re-run with the corrected values. The original values stay in the audit log.")
        actual_txt = (res["fields"] or [{}])[0].get("actual", "")
        x1, x2, x3 = st.columns(3)
        ops = ["<=", ">=", "=="]
        op = x1.selectbox("Operator", ops, index=ops.index(c["operator"]), format_func=lambda o: engine.OP_SYMBOL[o])
        req_v = x2.number_input("Required value", value=float(c["value"]))
        unit = x3.text_input("Unit", c["unit"])
        actual_hint = (res["chain"] or {}).get("extract", {}).get("actual", {})
        y1, y2 = st.columns(2)
        act_v = y1.text_input("Bid value", "" if actual_hint.get("value") is None else f"{actual_hint['value']:g}",
                              help=f"Extracted: {actual_txt}")
        act_u = y2.text_input("Bid unit", actual_hint.get("unit") or c["unit"])
        reason = st.text_area("Reason for correction")
        if st.button("Save and re-run rule", type="primary"):
            try:
                value = float(act_v.replace(",", "")) if act_v.strip() else None
                service.correct_result(current_id, {**c, "operator": op, "value": req_v, "unit": unit}, value,
                                       act_u, reason, ui.actor(), ui.role(), ui.take_elapsed())
                ui.flash("Correction saved and rule re-run.")
                st.rerun()
            except ValueError:
                st.error("Bid value must be a number.")
            except ServiceError as e:
                st.error(str(e))

    if a2.button("Correct", icon=":material/edit:", width="stretch", disabled=signed):
        correct_dialog()

    with a3.popover("Add evidence", icon=":material/add_link:", width="stretch", disabled=signed):
        pg = st.number_input("Bid page", min_value=1, max_value=bid_doc["pages"] or 1, value=1,
                             key=f"ws_addev_page_{current_id}")
        ev_note = st.text_input("What does this page show?", key=f"ws_addev_note_{current_id}")
        if st.button("Add", key=f"ws_addev_btn_{current_id}"):
            _run(service.add_evidence, current_id, pg, ev_note, ui.actor(), ui.role(), ui.take_elapsed(),
                 success="Evidence added.")
            st.rerun()

    with a4.popover("Not applicable", icon=":material/block:", width="stretch",
                    disabled=signed or not res["applicable"]):
        na = st.text_input("Reason (required)", key=f"ws_na_{current_id}")
        if st.button("Mark not applicable", key=f"ws_na_btn_{current_id}"):
            _run(service.mark_not_applicable, current_id, na, ui.actor(), ui.role(), ui.take_elapsed(),
                 success="Marked not applicable.")
            st.rerun()

    a5.button("To remediation", icon=":material/build:", width="stretch",
              disabled=signed or res["verdict"] == COMPLIANT or not res["applicable"],
              on_click=lambda: _run(service.send_to_remediation, current_id, note, ui.actor(), ui.role(),
                                    ui.take_elapsed(), success=f"Clause {res['clause_no']} sent to remediation."))

    history = store.list_actions(pid, res["requirement_id"])
    if history:
        with st.expander(f"History ({len(history)})"):
            ui.table([{"Time": h["at"].replace("T", " "), "Who": f"{h['actor']} ({h['role']})", "Action": h["action"],
                       "Note": h["note"] or ""} for h in history])

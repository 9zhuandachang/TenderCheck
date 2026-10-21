import html
from pathlib import Path
import streamlit as st

from tcui import config, engine, real_backend, service, store, ui
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
rnd = rounds[0]
round_id = rnd["id"]
st.session_state["ws_round"] = round_id

# ----------------------------------------------------------------- top bar
top = st.columns([3.5, 2.5], vertical_alignment="center")
used = [docs[d]["label"] for d in (rnd["doc_ids"] or []) if d in docs]
top[0].markdown(f"**{project['name']}**  \n<span class='tc-muted'>{' · '.join(used)}</span>", unsafe_allow_html=True)
summary = service.round_summary(round_id)
np_total = summary.get("non_passing", summary["non_compliant"] + summary["insufficient"])
np_rev = summary.get("non_passing_reviewed", 0)
top[1].progress(
    np_rev / np_total if np_total else 1.0,
    text=f"Non-passing reviewed: {np_rev}/{np_total}",
)
signed = bool(rnd["signed_at"])
if signed:
    st.info(f"Signed off by {rnd['signer']}; it is read-only.", icon=":material/lock:")

# ----------------------------------------------------------------- filters
results = store.list_results(round_id)
is_real = summary.get("is_real_backend", False)
FILTERS = ["All", NON_COMPLIANT, INSUFFICIENT, COMPLIANT, "Unreviewed", "In remediation", "Not applicable"]
if st.session_state.get("ws_filter") not in FILTERS:
    st.session_state["ws_filter"] = "All"
SHORT = {INSUFFICIENT: "Insufficient", "In remediation": "Remediation", "Not applicable": "N/A"}

SUB_FILTERS = ["All"] + list(real_backend.SUB_VERDICT_LABELS.keys())
if st.session_state.get("ws_sub_filter") not in SUB_FILTERS:
    st.session_state["ws_sub_filter"] = "All"

if is_real:
    f1, f_sub, f2, f3, f4 = st.columns([5.0, 1.9, 1.2, 1.1, 0.8], vertical_alignment="bottom")
    flt = f1.segmented_control("Show", FILTERS, key="ws_filter", required=True, format_func=lambda f: SHORT.get(f, f))
    sub_flt = f_sub.selectbox(
        "Review Route / Sub-type",
        SUB_FILTERS,
        key="ws_sub_filter",
        format_func=lambda k: "All sub-types" if k == "All" else real_backend.SUB_VERDICT_LABELS[k][0],
    )
    cats = f2.multiselect("Category", config.CATEGORIES, key="ws_cats", placeholder="All categories")
    search = f3.text_input("Search", key="ws_search", placeholder="Clause No. (e.g. T0056)")
    show_nav = f4.toggle("List", value=True, key="ws_show_nav")
else:
    f1, f2, f3, f4 = st.columns([6.6, 1.4, 1.3, 0.7], vertical_alignment="bottom")
    flt = f1.segmented_control("Show", FILTERS, key="ws_filter", required=True, format_func=lambda f: SHORT.get(f, f))
    sub_flt = "All"
    cats = f2.multiselect("Category", config.CATEGORIES, key="ws_cats", placeholder="All categories")
    search = f3.text_input("Search", key="ws_search", placeholder="Clause No. (e.g. T0056)")
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
    if sub_flt != "All":
        r_sub = (r.get("chain") or {}).get("sub_verdict")
        if sub_flt == "ATTACHMENT_OR_FORMULA" and r_sub in ("ATTACHMENT_OR_FORMULA", "PERFORMANCE_LINKAGE"):
            pass
        elif r_sub != sub_flt:
            return False
    if cats and r["category"] not in cats:
        return False
    q = search.strip().lower()
    return not q or q in r["clause_no"].lower()


shown = [r for r in results if _match(r)]
if not shown:
    st.info("No clauses match the filters.")
    st.stop()

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
    hit = next((r["id"] for r in shown if r["requirement_id"] == focus_req), None)
    if hit:
        st.session_state["ws_result"] = hit
if st.session_state.get("ws_result") not in ids:
    st.session_state["ws_result"] = _pick_default_non_passing(shown)
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
                r_sub = (r.get("chain") or {}).get("sub_verdict")
                sub_tag = ""
                if r_sub == "CONFLICTING_EVIDENCE":
                    sub_tag = " · ⚠Conflict"
                elif r_sub == "SEAL_SIGNATURE_CHECK":
                    sub_tag = " · 🔍Seal/Sign"
                elif r_sub == "EXTERNAL_WEB_CHECK":
                    sub_tag = " · 🌐WebCheck"
                elif r_sub == "SCORED":
                    sub_tag = " · ★Scored"
                selected = r["id"] == current_id
                meta = f"{r['category']}{sub_tag} · {status}"
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
    head, ctrl = st.container(height=44, border=False).columns([1, 1.3], vertical_alignment="center")
    if amended and res["origin"] != "addendum":
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
    if res.get("req_text"):
        st.caption(f"**Tender Requirement ({res['clause_no']} · p.{t_page or 1})**: :orange-background[{res['req_text']}]")

with cols[2]:
    bid_doc = docs.get(rnd["bid_doc_id"])
    evidence = res["evidence"] or []
    ev_key = f"ws_ev_{current_id}"
    head, ctrl = st.container(height=44, border=False).columns([1, 1.3], vertical_alignment="center")
    if evidence:
        idx = ctrl.segmented_control(
            "Evidence", list(range(len(evidence))), default=0, key=ev_key, required=True,
            format_func=lambda i: f"p.{evidence[i]['page']}", label_visibility="collapsed",
            help="Evidence pages: verified quotes first, then retrieved candidate pages",
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
            f"**BID** <span class='tc-muted'>{ev_doc['label'] if ev_doc else ''}</span>",
            unsafe_allow_html=True,
            help=ev.get("role", ""),
        )
        ui.pdf_pane("bid", ev_doc, ev["page"], ev.get("snippet", ""))
        if ev.get("snippet"):
            st.caption(f"**{ev.get('role', 'Evidence')}**: {ev['snippet'][:220]}")
    else:
        head.markdown(f"**BID** <span class='tc-muted'>{bid_doc['label'] if bid_doc else ''} · no evidence found</span>",
                      unsafe_allow_html=True)
        ui.pdf_pane("bid", bid_doc, 1)

# ----------------------------------------------------------------- verification panel
with st.container(border=True):
    h1, h2 = st.columns([2.6, 2.4], vertical_alignment="center")
    tags = [ui.verdict_md(res["verdict"])]
    sub_badge = ui.sub_verdict_md(res.get("chain"))
    if sub_badge:
        tags.append(sub_badge)
    tags.append(ui.status_md(res["human_status"] if res["applicable"] else "Not applicable"))
    if res["corrected"]:
        tags.append(f":gray-badge[Corrected · system said {res['original_verdict']}]")
    h1.markdown(f"#### {res['clause_no']} · {res['category']}")
    h2.markdown(" ".join(tags))
    st.markdown(
        f"<div class='tc-req'><mark>Tender Requirement</mark> &nbsp;{html.escape(str(res['req_text']))}</div>",
        unsafe_allow_html=True,
    )

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

    @st.dialog("Correct extracted values")
    def correct_dialog():
        c = dict(res["constraint"] or {})
        if c.get("type") != "numeric":
            st.info("This clause is checked for presence/qualification, not a single numeric value. Use **Add evidence** "
                    "to point at the right bid page, then confirm with a note.")
            return
        st.caption("The rule is re-run with the corrected values. The original values stay in the audit log.")
        actual_txt = (res["fields"] or [{}])[0].get("actual", "")
        x1, x2, x3 = st.columns(3)
        ops = ["<=", ">=", "=="]
        cur_op = c.get("operator") if c.get("operator") in ops else "<="
        op = x1.selectbox("Operator", ops, index=ops.index(cur_op), format_func=lambda o: engine.OP_SYMBOL[o])
        req_v = x2.number_input("Required value", value=float(c.get("value") or 0.0))
        unit = x3.text_input("Unit", c.get("unit") or "")
        actual_hint = (res["chain"] or {}).get("extract", {}).get("actual", {})
        y1, y2 = st.columns(2)
        act_v = y1.text_input("Bid value", "" if actual_hint.get("value") is None else f"{actual_hint['value']:g}",
                              help=f"Extracted: {actual_txt}")
        act_u = y2.text_input("Bid unit", actual_hint.get("unit") or c.get("unit") or "")
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
        max_pg = (bid_doc["pages"] if bid_doc and bid_doc.get("pages") else 2000)
        pg = st.number_input("Bid page", min_value=1, max_value=max_pg, value=1,
                             key=f"ws_addev_page_{current_id}")
        ev_note = st.text_input("What does this page show?", key=f"ws_addev_note_{current_id}")
        if st.button("Add", key=f"ws_addev_btn_{current_id}"):
            _run(service.add_evidence, current_id, pg, ev_note, ui.actor(), ui.role(), ui.take_elapsed(),
                 success="Evidence added.")
            st.rerun()

    with a4.popover("Not applicable", icon=":material/block:", width="stretch",
                    disabled=signed or not res["applicable"]):
        na = st.text_input("Reason (required)", key=f"ws_na_{current_id}")
        if st.button("Mark not applicable", key=f"ws_na_{current_id}_btn"):
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

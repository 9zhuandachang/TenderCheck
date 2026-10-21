import streamlit as st

from tcui import config, demo, engine, service, store, ui
from tcui.config import CLOSED, COMPLIANT, TO_REINSPECT, TO_REMEDIATE
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()
docs = ui.doc_map(pid)

st.title("Remediation & Re-inspection")
reinspecting = ui.job_progress(pid, "review")
ui.job_error(pid, "review")

items = store.list_remediations(pid)
open_items = [i for i in items if i["status"] != CLOSED]
latest = store.latest_round(pid)
bid = store.selected_bid(pid)

m1, m2, m3, m4 = st.columns(4)
m1.metric("To remediate", sum(i["status"] == TO_REMEDIATE for i in items))
m2.metric("Awaiting re-inspection", sum(i["status"] == TO_REINSPECT for i in items))
m3.metric("Closed", sum(i["status"] == CLOSED for i in items))
m4.metric("Current bid", bid["label"] if bid else "—")

tab_items, tab_reinspect = st.tabs(["Issues and drafts", "Revised bid and re-inspection"])

# ================================================================= issues and drafts
with tab_items:
    if not items:
        st.info("No remediation items. In the Review Workspace, use **To remediation** on a non-compliant clause.")
    else:
        show_closed = st.toggle("Show closed items", key="rem_show_closed")
        listed = items if show_closed else open_items
        if not listed:
            st.success("All remediation items are closed.")
        else:
            by_id = {i["id"]: i for i in listed}
            left, right = st.columns([1, 2.6], gap="large")
            with left:
                sel = st.radio(
                    "Issues", list(by_id), key="rem_selected", label_visibility="collapsed",
                    format_func=lambda i: f"{by_id[i]['clause_no']} · {by_id[i]['req_text'][:34]}… "
                                          f"({by_id[i]['status']})",
                )
            item = by_id[sel]
            req = store.get_requirement(item["requirement_id"])
            res = store.result_for(latest["id"], item["requirement_id"]) if latest else None
            with right:
                st.markdown(f"#### {item['clause_no']} · {item['category']}  {ui.status_md(item['status'])}")
                st.markdown(f"<div class='tc-req'>{item['req_text']}</div>", unsafe_allow_html=True)
                st.markdown(f"**Issue:** {item['issue']}")
                if res and res["evidence"]:
                    ev = ", ".join(f"p.{e['page']}" for e in res["evidence"])
                    st.caption(f"Bid evidence in {docs.get(latest['bid_doc_id'], {}).get('label', '')}: {ev}")

                st.markdown("##### Knowledge base")
                suggestions = engine.suggest_kb(req["text"], req["category"], engine.load_kb())
                chosen = []
                if not suggestions:
                    st.caption("No matching knowledge base material.")
                for kb in suggestions:
                    with st.container(border=True):
                        k1, k2 = st.columns([5, 1.3], vertical_alignment="top")
                        k1.markdown(f"**{kb['id']} · {kb['title']}**  \n:gray[{kb['type']} · matched on "
                                    f"{kb['reason']}]")
                        use = k2.checkbox("Use", key=f"rem_kb_{item['id']}_{kb['id']}",
                                          value=kb["id"] in (item["kb_refs"] or []))
                        with st.expander("Source text and applicability"):
                            st.markdown(kb["content"])
                            st.caption(f"Applicability: {kb['applicability']}")
                        if use:
                            chosen.append(kb)

                st.markdown("##### Revision draft")
                draft_key = f"rem_draft_{item['id']}"
                if draft_key not in st.session_state:
                    st.session_state[draft_key] = item["draft"] or ""

                def _generate(item=item, req=req, chosen=chosen, draft_key=draft_key):
                    st.session_state[draft_key] = engine.draft_revision(
                        item["clause_no"], req["text"], req["constraint"] or {}, chosen)

                st.button("Generate draft from selected references", icon=":material/auto_awesome:",
                          on_click=_generate, disabled=item["status"] == CLOSED)
                st.text_area("Draft", key=draft_key, height=200, label_visibility="collapsed",
                             disabled=item["status"] == CLOSED)
                target = st.text_input("Where this goes in the bid", value=item["target"] or "",
                                       key=f"rem_target_{item['id']}", placeholder="e.g. Bid section 3, programme")
                if st.button("Save draft", type="primary", disabled=item["status"] == CLOSED):
                    service.save_draft(item["id"], [k["id"] for k in chosen], st.session_state[draft_key], target,
                                       ui.actor(), ui.role())
                    ui.flash("Draft saved. The item stays 'To remediate' until a revised bid is uploaded.")
                    st.rerun()
                st.caption("The draft is reference text. Edit the bid itself, then upload the revised bid on the "
                           "next tab to re-inspect.")

# ================================================================= revised bid and re-inspection
with tab_reinspect:
    st.markdown("**1 · Upload the revised bid.** It becomes the next bid version; items waiting for "
                "remediation move to *To re-inspect*.")
    counter = st.session_state.setdefault("rem_upload_counter", 0)
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    up = c1.file_uploader("Revised bid PDF", type=["pdf"], key=f"rem_upload_{counter}")
    if c1.button("Upload revised bid", disabled=up is None):
        try:
            doc_id, created = service.add_document(pid, "bid", up.name, up.getvalue())
            n = service.link_revised_bid(pid, doc_id, ui.actor(), ui.role())
            ui.flash(f"Uploaded as {store.get_document(doc_id)['label']}; {n} item(s) now await re-inspection."
                     if created else "This file is already in the project.")
            st.session_state["rem_upload_counter"] = counter + 1
            st.rerun()
        except ServiceError as e:
            st.error(str(e))
    demo_bid = demo.revised_bid_path(pid)
    if project["is_demo"] and demo_bid.exists() and len(store.list_documents(pid, "bid")) < 2:
        if c2.button("Use demo revised bid", icon=":material/science:"):
            doc_id, _ = service.add_document(pid, "bid", demo_bid.name, demo_bid.read_bytes())
            n = service.link_revised_bid(pid, doc_id, ui.actor(), ui.role())
            ui.flash(f"Demo revised bid uploaded; {n} item(s) now await re-inspection.")
            st.rerun()
    ui.job_progress(pid, "parse:")

    st.markdown("**2 · Re-inspect.** All confirmed requirements are checked again against the new bid version; "
                "the previous round is kept for comparison.")
    can = bool(latest and bid and bid["id"] != latest["bid_doc_id"] and bid["pages"])
    if st.button("Start re-inspection", type="primary", icon=":material/replay:",
                 disabled=not can or reinspecting):
        try:
            service.start_reinspection(pid, ui.actor(), ui.role())
            st.rerun()
        except ServiceError as e:
            st.error(str(e))
    if not can and latest and not reinspecting:
        st.caption("Upload a revised bid (and wait for parsing) to enable re-inspection.")

    st.markdown("**3 · Compare and close.**")
    if latest and store.previous_round(latest):
        prev = store.previous_round(latest)
        rows = service.compare_rounds(latest["id"])
        st.caption(f"Round {latest['no']} ({docs.get(latest['bid_doc_id'], {}).get('label', '')}) compared with "
                   f"Round {prev['no']} ({docs.get(prev['bid_doc_id'], {}).get('label', '')})")
        counts = {c: sum(r["change"] == c for r in rows) for c in service.COMPARISON_CLASSES[:4]}
        cc = st.columns(4)
        for col, (label, n) in zip(cc, counts.items()):
            col.metric(label, n)
        changed = [r for r in rows if r["change"] != "Unchanged"]
        ui.table([{"Clause": r["clause_no"], "Change": r["change"], "Before": r["previous_verdict"] or "—",
                   "Now": r["verdict"], "Reviewer status": r["human_status"]} for r in changed])
        to_close = [r for r in rows if r["change"] == "Resolved" and r["human_status"] == TO_REINSPECT]
        if to_close:
            st.markdown("Resolved items need a reviewer confirmation to close:")
            for r in to_close:
                k1, k2 = st.columns([4, 1], vertical_alignment="center")
                k1.markdown(f"**{r['clause_no']}** {r['req_text'][:90]} — {ui.verdict_md(r['verdict'])}")
                if k2.button("Confirm & close", key=f"close_{r['id']}"):
                    try:
                        service.confirm_result(r["id"], ui.actor(), ui.role(), "Resolved after re-inspection")
                        ui.flash(f"Clause {r['clause_no']} closed.")
                        st.rerun()
                    except ServiceError as e:
                        st.error(str(e))
            st.caption("Or open each clause in the Review Workspace to check the new evidence before confirming.")
    else:
        st.caption("A comparison appears after the first re-inspection.")

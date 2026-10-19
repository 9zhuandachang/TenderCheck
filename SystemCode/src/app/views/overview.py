import streamlit as st

from tcui import config, demo, service, store, ui
from tcui.service import ServiceError

project = ui.current_project()

if not project:
    st.title("TenderCheck")
    st.markdown("Evidence-based tender compliance review: check every tender requirement against the bid, "
                "trace the evidence, remediate issues and sign off a compliance matrix.")
    c1, c2 = st.columns(2, border=True)
    with c1:
        st.subheader("New project")
        with st.form("new_project"):
            name = st.text_input("Project name", placeholder="e.g. Contract C-2026-014 Riverside Drainage")
            if st.form_submit_button("Create project", type="primary"):
                if name.strip():
                    ui.select_project(store.create_project(name.strip()))
                    ui.go("documents")
                else:
                    st.error("Enter a project name.")
    with c2:
        st.subheader("Demo project")
        st.markdown("A fictitious English drainage tender with one addendum and a bid, already parsed. "
                    "A revised bid is provided for the re-inspection step.")
        if st.button("Load demo project", icon=":material/play_circle:"):
            with st.spinner("Building demo documents"):
                ui.select_project(demo.create_demo_project())
            st.rerun()
    st.stop()

pid = project["id"]
ui.show_flash()
rnd = store.latest_round(pid)
bid = store.selected_bid(pid)

st.title(project["name"])
meta = [f"Bid version: **{bid['label']}**" if bid else "No bid uploaded"]
if rnd:
    meta.append(f"Latest round: **Round {rnd['no']}** ({rnd['created_at'].replace('T', ' ')})")
    meta.append("Signed off" if rnd["signed_at"] else "Not signed off")
st.markdown(" · ".join(meta))

if ui.job_progress(pid, "review"):
    st.info("Review in progress. You can switch pages; the task keeps running.")
ui.job_error(pid, "review")

# ----------------------------------------------------------------- headline numbers
if rnd:
    s = service.round_summary(rnd["id"])
    cards = [
        ("Awaiting review", s["unreviewed"], {"ws_filter": "Unreviewed"}),
        ("Non-compliant", s["non_compliant"], {"ws_filter": config.NON_COMPLIANT}),
        ("Insufficient evidence", s["insufficient"], {"ws_filter": config.INSUFFICIENT}),
        ("In remediation", s["to_remediate"], {"ws_filter": "In remediation"}),
    ]
    cols = st.columns(4)
    for col, (label, value, state) in zip(cols, cards):
        with col:
            st.metric(label, value)
            if st.button("View", key=f"card_{label}", width="stretch"):
                ui.go("workspace", ws_round=rnd["id"], **state)

    p1, p2 = st.columns(2)
    p1.progress(1.0, text=f"System checks: {s['total']}/{s['total']} clauses checked")
    p2.progress(s["reviewed"] / s["applicable"] if s["applicable"] else 0.0,
                text=f"Reviewer decisions: {s['reviewed']}/{s['applicable']} applicable clauses")

st.write("")
left, right = st.columns([1.1, 1], gap="large")

# ----------------------------------------------------------------- readiness and start
with left:
    st.subheader("Readiness")
    checks = service.review_readiness(pid)
    for c in checks:
        icon = ":green[:material/check_circle:]" if c["ok"] else ":orange[:material/radio_button_unchecked:]"
        detail = f" — {c['detail']}" if c.get("detail") else ""
        st.markdown(f"{icon} {c['item']}{detail}")
    ready = all(c["ok"] for c in checks)
    reviewing = bool(store.active_jobs(pid, "review"))
    already = rnd and bid and rnd["bid_doc_id"] == bid["id"]
    label = "Start review" if not rnd else ("Re-run review on current bid" if already else "Start review of new bid")
    if st.button(label, type="secondary" if already else "primary", disabled=not ready or reviewing,
                 icon=":material/play_arrow:"):
        try:
            service.start_review(pid, ui.actor(), ui.role())
            st.rerun()
        except ServiceError as e:
            st.error(str(e))
    if not ready:
        missing = [c for c in checks if not c["ok"]]
        page = "requirements" if any("Requirement" in c["item"] or "Addendum" in c["item"] for c in missing) \
            else "documents"
        st.page_link(ui.PAGES[page], label=f"Next step: open {page.title()}", icon=":material/arrow_forward:")

# ----------------------------------------------------------------- to-do by role
with right:
    st.subheader(f"To do · {ui.role()}")
    if not rnd:
        st.markdown("Run the first review to see open items.")
    elif ui.role() == "Reviewer":
        s = service.round_summary(rnd["id"])
        items = []
        if s["unreviewed"]:
            items.append(f"Review **{s['unreviewed']}** clause(s) without a decision")
        if s["insufficient"]:
            items.append(f"Decide **{s['insufficient']}** clause(s) with insufficient evidence")
        open_rem = store.list_remediations(pid, open_only=True)
        if open_rem:
            items.append(f"Work on **{len(open_rem)}** remediation item(s)")
        for item in items or ["Nothing open. Hand over to the Manager for sign-off."]:
            st.markdown(f"- {item}")
        last = project["last_requirement_id"]
        if st.button("Continue review", icon=":material/resume:"):
            ui.go("workspace", ws_round=rnd["id"], ws_focus_requirement=last)
    else:
        blockers = service.signoff_blockers(rnd["id"])
        if rnd["signed_at"]:
            st.success(f"Round {rnd['no']} signed by {rnd['signer']} on {rnd['signed_at'].replace('T', ' ')}.")
        elif blockers:
            st.markdown("Sign-off is blocked:")
            for b in blockers:
                st.markdown(f"- {b}")
        else:
            st.markdown("Round is ready for sign-off.")
        st.page_link(ui.PAGES["report"], label="Open Report & Sign-off", icon=":material/verified:")

# ----------------------------------------------------------------- rounds
rounds = store.list_rounds(pid)
if rounds:
    st.subheader("Review rounds")
    docs = ui.doc_map(pid)
    ui.table([{
        "Round": r["no"],
        "Bid": docs.get(r["bid_doc_id"], {}).get("label", ""),
        "Started": r["created_at"].replace("T", " "),
        "Status": r["status"],
        "Signed off": f"{r['signer']} · {r['signed_at'].replace('T', ' ')}" if r["signed_at"] else "",
        "Rules": r["rule_version"],
    } for r in rounds])

with st.expander("Project data"):
    st.markdown("Project files and results are stored locally under "
                f"`{store.project_dir(pid)}` and `{config.DB_PATH.name}`.")
    confirm = st.checkbox("I understand this permanently deletes all files, results and the audit log of "
                          "this project.")
    if st.button("Delete project data", disabled=not confirm, icon=":material/delete:"):
        store.delete_project(pid)
        ui.select_project(None)
        st.rerun()

import streamlit as st

from tcui import config, demo, service, store, ui
from tcui.service import ServiceError

project = ui.current_project()

if not project or st.session_state.get("_show_project_start", False):
    st.title("TenderCheck")
    st.markdown("**Evidence-based bid compliance review.**")
    st.markdown("Check tender requirements against the bid, trace findings to source pages, "
                "and resolve issues before sign-off.")
    if project:
        if st.button("Back to current project", icon=":material/arrow_back:"):
            ui.select_project(project["id"])
            st.rerun()
    c1, c2 = st.columns(2, gap="medium", wrap=True)
    with c1.container(border=True, height="stretch", key="welcome_new_project"):
        st.subheader("New project")
        st.markdown("Start with your own tender and bid documents.")
        with st.form("new_project", border=False, height="stretch"):
            name = st.text_input("Project name", placeholder="e.g. Contract C-2026-014 Riverside Drainage")
            validation = st.empty()
            st.space("stretch")
            if st.form_submit_button("Create project", type="primary", icon=":material/add:", width="stretch", wrap=True):
                if name.strip():
                    ui.select_project(store.create_project(name.strip()))
                    ui.go("documents")
                else:
                    validation.error("Enter a project name.")
    with c2.container(border=True, height="stretch", key="welcome_demo"):
        st.subheader("Explore demo")
        st.markdown("Try a prepared sample project. No file upload needed.")
        with st.expander("What's included"):
            st.markdown("A fictitious English drainage tender, Addendum 1, and Bid v1, already parsed "
                        "with requirements extracted. A revised Bid v2 is available in Remediation "
                        "for re-inspection.")
        st.space("stretch")
        if st.button("Explore demo", icon=":material/play_circle:", width="stretch", wrap=True):
            with st.spinner("Building demo documents"):
                ui.select_project(demo.create_demo_project())
            st.rerun()
    ui.workflow_steps()
    st.stop()

pid = project["id"]
ui.show_flash()
rnd = store.latest_round(pid)
bid = store.selected_bid(pid)
checks = service.review_readiness(pid)
ready = all(c["ok"] for c in checks)
reviewing = bool(store.active_jobs(pid, "review"))
already = rnd and bid and rnd["bid_doc_id"] == bid["id"]
review_label = "Start review" if not rnd else ("Re-run review on current bid" if already else "Start review of new bid")
next_stage, next_detail, next_page = ui.project_next_step(project, checks, rnd)


def review_button(primary: bool = False) -> None:
    if st.button(review_label, type="primary" if primary else "secondary", disabled=not ready or reviewing,
                 icon=":material/play_arrow:", key="overview_start_review", wrap=True):
        try:
            service.start_review(pid, ui.actor(), ui.role())
            st.rerun()
        except ServiceError as e:
            st.error(str(e))

st.title(project["name"])
meta = [f"Bid version: **{bid['label']}**" if bid else "No bid uploaded"]
if rnd:
    meta.append(f"Latest round: **Round {rnd['no']}** ({rnd['created_at'].replace('T', ' ')})")
    meta.append("Signed off" if rnd["signed_at"] else "Not signed off")
st.caption(" · ".join(meta))

with st.container(border=True, key="overview_next_step"):
    st.subheader(f"Next step · {next_stage}")
    st.markdown(next_detail)
    if next_page == "overview":
        review_button(primary=True)
    else:
        page_labels = {"documents": "Documents", "requirements": "Requirements", "workspace": "Review Workspace",
                       "remediation": "Remediation", "report": "Report & Sign-off"}
        if st.button(f"Open {page_labels[next_page]}", type="primary", icon=":material/arrow_forward:",
                     key="overview_next_action", wrap=True):
            ui.go(next_page)
        if next_page == "remediation" and rnd and not rnd["signed_at"]:
            st.page_link(ui.PAGES["report"], label="View sign-off requirements and disclosed risks",
                         icon=":material/verified:")

ui.workflow_steps(next_stage, ui.workflow_completed_steps(project, checks, rnd))

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
    st.subheader("Review summary")
    cols = st.columns(4, gap="medium", wrap=True)
    for col, (label, value, state) in zip(cols, cards):
        with col:
            st.caption(label)
            st.subheader(str(value))
            if st.button("View", key=f"card_{label}", width="stretch", type="tertiary", wrap=True,
                         help=f"Open {label.lower()} clauses in the Review Workspace"):
                ui.go("workspace", ws_round=rnd["id"], **state)

    p1, p2 = st.columns(2, wrap=True)
    p1.progress(1.0, text=f"System checks: {s['total']}/{s['total']} clauses checked")
    p2.progress(s["reviewed"] / s["applicable"] if s["applicable"] else 0.0,
                text=f"Reviewer decisions: {s['reviewed']}/{s['applicable']} applicable clauses")

left, right = st.columns([1.1, 1], gap="large", wrap=True)

# ----------------------------------------------------------------- readiness and start
with left:
    pending_checks = sum(not c["ok"] for c in checks)
    readiness_label = "Readiness · Ready" if ready else f"Readiness · {pending_checks} checks remaining"
    with st.expander(readiness_label, expanded=not ready):
        for c in checks:
            icon = ":green[:material/check_circle:]" if c["ok"] else ":orange[:material/radio_button_unchecked:]"
            detail = f" — {c['detail']}" if c.get("detail") else ""
            st.markdown(f"{icon} {c['item']}{detail}")
    if next_page != "overview":
        review_button()

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
    latest = rounds[0]
    st.markdown(f"**Round {latest['no']}** · {docs.get(latest['bid_doc_id'], {}).get('label', '')}")
    st.caption(f"{latest['status'].title()} · Started {latest['created_at'].replace('T', ' ')}"
               + (f" · Signed by {latest['signer']}" if latest["signed_at"] else " · Not signed off"))
    if st.button("Open latest results", icon=":material/arrow_forward:", disabled=latest["status"] != "done", wrap=True):
        ui.go("workspace", ws_round=latest["id"])
    with st.expander(f"All review rounds ({len(rounds)})"):
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

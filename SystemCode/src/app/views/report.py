import csv
import io

import streamlit as st

from tcui import config, export, service, store, ui
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()

st.title("Report & Sign-off")
rounds = [r for r in store.list_rounds(pid) if r["status"] == "done"]
if not rounds:
    st.info("Run a review first.")
    st.stop()

by_id = {r["id"]: r for r in rounds}
docs = ui.doc_map(pid)
c1, c2 = st.columns([2, 3], vertical_alignment="center")
round_id = c1.selectbox("Round", list(by_id), key="rep_round",
                        format_func=lambda i: f"Round {by_id[i]['no']} · "
                                              f"{docs.get(by_id[i]['bid_doc_id'], {}).get('label', '')}")
rnd = store.get_round(round_id)
if rnd["signed_at"]:
    c2.markdown(f":green-badge[:material/verified: Signed] by **{rnd['signer']}** on "
                f"{rnd['signed_at'].replace('T', ' ')}")
else:
    c2.markdown(":orange-badge[:material/draft: Draft] Not signed off — exports are marked DRAFT.")

s = service.round_summary(round_id)
m = st.columns(5)
m[0].metric("Applicable clauses", s["applicable"])
m[1].metric("Compliant", s["compliant"])
m[2].metric("Non-compliant", s["non_compliant"])
m[3].metric("Insufficient evidence", s["insufficient"])
m[4].metric("Reviewer decisions", f"{s['reviewed']}/{s['applicable']}")

tab_matrix, tab_open, tab_sign, tab_eval, tab_log = st.tabs(
    ["Compliance matrix", "Outstanding", "Sign-off & export", "Evaluation", "Audit log"])

with tab_matrix:
    rows = export.matrix_rows(round_id)
    ui.table(rows, column_config={"Requirement": st.column_config.TextColumn(width="large")})

with tab_open:
    groups = export.outstanding(round_id)
    for group, items in groups.items():
        st.markdown(f"**{group}** ({len(items)})")
        if items:
            ui.table([{"Clause": r.get("clause_no"), "Requirement": r.get("req_text"),
                       "System verdict": r.get("verdict", ""), "Reviewer": r.get("human_status", ""),
                       "Note": r.get("na_reason") or r.get("note") or ""} for r in items])

with tab_sign:
    blockers = service.signoff_blockers(round_id)
    st.markdown("Sign-off records that every applicable clause has a reviewer decision. Unresolved "
                "non-compliance can remain, but it is disclosed in the report; sign-off does not state that the "
                "bid is fully compliant.")
    if rnd["signed_at"]:
        st.success(f"Signed by {rnd['signer']} on {rnd['signed_at'].replace('T', ' ')}. "
                   f"Comment: {rnd['sign_comment'] or '—'}")
    elif blockers:
        st.warning("Sign-off is not yet possible:\n\n" + "\n".join(f"- {b}" for b in blockers))
    elif ui.role() != "Manager":
        st.info("The round is ready. Switch the role to **Manager** in the sidebar to sign off.")
    else:
        with st.form("signoff"):
            signer = st.text_input("Signed by", value=st.session_state.get("user_name", ""))
            comment = st.text_area("Sign-off comment",
                                   placeholder="e.g. Open items 5.4 and 4.1 accepted as disclosed risks.")
            if st.form_submit_button("Sign off this round", type="primary"):
                try:
                    service.sign_round(round_id, signer, comment, ui.role())
                    ui.flash(f"Round {rnd['no']} signed off.")
                    st.rerun()
                except ServiceError as e:
                    st.error(str(e))

    st.divider()
    tag = "" if rnd["signed_at"] else "_DRAFT"
    base = f"TenderCheck_{project['name'][:30].replace(' ', '_')}_Round{rnd['no']}{tag}"
    e1, e2 = st.columns(2)
    e1.download_button("Compliance matrix (Excel)", export.excel_bytes(round_id), f"{base}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       icon=":material/table:", on_click="ignore", width="stretch")
    e2.download_button("Review report (PDF)", export.pdf_bytes(round_id), f"{base}.pdf", mime="application/pdf",
                       icon=":material/picture_as_pdf:", on_click="ignore", width="stretch")

with tab_eval:
    st.markdown("Supports the Proposal's end-to-end validation: replay a historical case against its known "
                "issues, and compare reviewer time with a manual cross-check.")
    st.markdown("**Historical case replay**")
    eval_round = st.selectbox(
        "Round to evaluate", list(by_id), index=len(by_id) - 1, key="rep_eval_round",
        format_func=lambda i: f"Round {by_id[i]['no']} · {docs.get(by_id[i]['bid_doc_id'], {}).get('label', '')}",
        help="Known issues describe the submitted bid, so evaluate the round that reviewed it (usually Round 1).",
    )
    ev = service.evaluate_round(eval_round)
    if ev["known"]:
        k = st.columns(3)
        k[0].metric("Known issues flagged", f"{ev['hits']}/{ev['known']}")
        k[1].metric("Additional flags", len(ev["false_alarms"]),
                    help="Clauses flagged by the system that are not in the known-issue list.")
        k[2].metric("Addendum changes applied", f"{ev['amendments_applied']}/{len(ev['amendments'])}")
        ui.table(ev["rows"])
        if ev["false_alarms"]:
            st.caption("Additional flags: " + ", ".join(ev["false_alarms"]))
    else:
        st.caption("No known-issue list for this project yet.")
    with st.expander("Upload known-issue list (CSV)"):
        st.caption("Columns: clause_no, expected, note")
        f = st.file_uploader("Known issues", type=["csv"], key="known_csv", label_visibility="collapsed")
        if f and st.button("Replace known-issue list"):
            rows = list(csv.DictReader(io.StringIO(f.getvalue().decode("utf-8-sig"))))
            ui.flash(f"Loaded {service.set_known_issues(pid, rows)} known issue(s).")
            st.rerun()

    st.markdown("**Review effort**")
    t = st.columns(3)
    reviewed = max(s["reviewed"], 1)
    t[0].metric("Reviewer time in this round", ui.fmt_minutes(s["review_seconds"]))
    t[1].metric("Average per decided clause", ui.fmt_minutes(s["review_seconds"] / reviewed))
    baseline = t[2].number_input("Manual cross-check baseline (minutes)", min_value=0.0,
                                 value=float(project["baseline_minutes"] or 0), step=10.0)
    if baseline != (project["baseline_minutes"] or 0):
        store.update("projects", pid, {"baseline_minutes": baseline})
    if baseline:
        saved = 1 - (s["review_seconds"] / 60) / baseline
        st.caption(f"Reviewer time is {s['review_seconds'] / 60:.1f} min against a {baseline:.0f}-min manual "
                   f"baseline ({saved:.0%} less). Time is measured from opening a clause to recording a decision.")

with tab_log:
    log = store.list_actions(pid)
    ui.table([{"Time": a["at"].replace("T", " "), "Who": a["actor"] or "", "Role": a["role"] or "",
               "Action": a["action"], "Note": a["note"] or "", "Details": str(a["payload"] or "")} for a in log])

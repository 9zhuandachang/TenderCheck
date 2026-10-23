import streamlit as st

from tcui import config, export, service, store, ui

project = ui.require_project()
pid = project["id"]
ui.show_flash()

st.title("Report & Export")
st.caption("View and export the compliance review matrix.")

rounds = [r for r in store.list_rounds(pid) if r["status"] == "done"]
if not rounds:
    st.info("No completed review round yet. Confirm documents and run the review pipeline on the Overview page first.")
    st.page_link(ui.PAGES["overview"], label="Go to Overview", icon=":material/dashboard:")
    st.stop()

rnd = rounds[0]
round_id = rnd["id"]
base = f"TenderCheck_Matrix_{project['name'][:25].replace(' ', '_')}"

# 1. Top summary metrics aligned with Review Workspace
s = service.round_summary(round_id)
all_total = s.get("applicable") or s.get("total") or 0
np_total = s.get("non_passing", s["non_compliant"] + s["insufficient"])
np_rev = s.get("non_passing_reviewed", 0)
remediated_cnt = sum(1 for i in store.list_remediations(pid) if i["status"] == config.CLOSED)

m = st.columns(4)
m[0].metric("Total Clauses", all_total)
m[1].metric("Compliant", s["compliant"])
m[2].metric("Non-passing", np_total)
m[3].metric("Reviewed / Remediated", f"{np_rev} (已改 {remediated_cnt})")

# 2. Primary Excel Matrix export button + view filter
bar_l, bar_r = st.columns([2.2, 1.8], vertical_alignment="bottom")
view_mode = bar_l.segmented_control(
    "Matrix Filter",
    ["All Clauses", "Non-passing Only", "Remediated Only"],
    default="All Clauses",
    label_visibility="collapsed",
)
bar_r.download_button(
    "Export Compliance Matrix (Excel)",
    export.excel_bytes(round_id),
    f"{base}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    icon=":material/table:",
    on_click="ignore",
    width="stretch",
)

# 3. 7-Column Compliance Matrix
rows = export.matrix_rows(round_id)
if view_mode == "Non-passing Only":
    rows = [r for r in rows if r["System Verdict"] != config.COMPLIANT]
elif view_mode == "Remediated Only":
    rows = [r for r in rows if "Remediated" in r["Review Status"]]

ui.table(
    rows,
    column_config={
        "Clause": st.column_config.TextColumn(width="small"),
        "Category": st.column_config.TextColumn(width="small"),
        "Tender Requirement": st.column_config.TextColumn(width="large"),
        "Bid Response": st.column_config.TextColumn(width="large"),
        "Bid Evidence": st.column_config.TextColumn(width="medium"),
    },
)

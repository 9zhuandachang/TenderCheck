import streamlit as st

from tcui import export, service, store, ui

project = ui.require_project()
pid = project["id"]
ui.show_flash()

st.title("Report & Export")
st.caption("View and export the compliance review matrix for the selected review round.")

rounds = [r for r in store.list_rounds(pid) if r["status"] == "done"]
if not rounds:
    st.info("No completed review round yet. Confirm documents and run the review pipeline on the Overview page first.")
    st.page_link(ui.PAGES["overview"], label="Go to Overview", icon=":material/dashboard:")
    st.stop()

rnd = rounds[0]
round_id = rnd["id"]
docs = ui.doc_map(pid)
base = f"TenderCheck_{project['name'][:30].replace(' ', '_')}"

c1, c2 = st.columns(2)
c1.download_button(
    "Export Compliance Matrix (Excel)",
    export.excel_bytes(round_id),
    f"{base}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    icon=":material/table:",
    on_click="ignore",
    width="stretch",
)
c2.download_button(
    "Export Review Report (PDF)",
    export.pdf_bytes(round_id),
    f"{base}.pdf",
    mime="application/pdf",
    icon=":material/picture_as_pdf:",
    on_click="ignore",
    width="stretch",
)

s = service.round_summary(round_id)
m = st.columns(5)
m[0].metric("Applicable clauses", s["applicable"])
m[1].metric("Compliant / Scored", s["compliant"])
m[2].metric("Non-compliant / Conflict", s["non_compliant"])
m[3].metric("Insufficient evidence", s["insufficient"])
m[4].metric("Reviewer decisions", f"{s['reviewed']}/{s['applicable']}")

st.subheader("Compliance Matrix")
rows = export.matrix_rows(round_id)
ui.table(rows, column_config={"Requirement": st.column_config.TextColumn(width="large")})

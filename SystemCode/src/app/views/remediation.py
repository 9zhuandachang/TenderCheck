import streamlit as st

from tcui import store, ui
from tcui.config import CLOSED, TO_REINSPECT, TO_REMEDIATE

project = ui.require_project()
pid = project["id"]
ui.show_flash()

st.title("Remediation")
st.caption("Bid issue tracking, remediation drafting, and revised bid re-inspection.")

items = store.list_remediations(pid)
bid = store.selected_bid(pid)

m1, m2, m3, m4 = st.columns(4)
m1.metric("To remediate", sum(i["status"] == TO_REMEDIATE for i in items))
m2.metric("Awaiting re-inspection", sum(i["status"] == TO_REINSPECT for i in items))
m3.metric("Closed", sum(i["status"] == CLOSED for i in items))
m4.metric("Current bid", bid["label"] if bid else "—")

with st.container(border=True):
    st.info(
        "**Module reserved for upcoming iteration.**  \n"
        "Automated remediation drafting and revised-bid differential re-inspection will be enabled in a future release. "
        "Please use the **Review Workspace** to inspect clause evidence and record review decisions.",
        icon=":material/construction:",
    )
    if items:
        ui.table(
            [
                {
                    "Clause": i["clause_no"],
                    "Category": i["category"],
                    "Requirement": i["req_text"],
                    "Issue": i["issue"],
                    "Status": i["status"],
                }
                for i in items
            ]
        )
    st.page_link(ui.PAGES["workspace"], label="Return to Review Workspace", icon=":material/compare:")

"""TenderCheck review workspace.

Run from SystemCode/:  streamlit run src/app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from tcui import store, ui  # noqa: E402

st.set_page_config(page_title="TenderCheck", page_icon=":material/fact_check:", layout="wide")


@st.cache_resource
def _init_storage() -> bool:
    store.init_db()
    return True


_init_storage()
ui.apply_style()

navigation = st.navigation({
    "TenderCheck": [
        st.Page(ui.PAGES["overview"], title="Overview", icon=":material/dashboard:", default=True),
    ],
    "Review": [
        st.Page(ui.PAGES["documents"], title="Documents", icon=":material/folder_open:"),
        st.Page(ui.PAGES["requirements"], title="Requirements", icon=":material/checklist:"),
        st.Page(ui.PAGES["workspace"], title="Review Workspace", icon=":material/compare:"),
        st.Page(ui.PAGES["remediation"], title="Remediation", icon=":material/build:"),
    ],
    "Delivery": [
        st.Page(ui.PAGES["report"], title="Report & Sign-off", icon=":material/verified:"),
    ],
})
ui.sidebar()
navigation.run()

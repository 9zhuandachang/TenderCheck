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
    section: [st.Page(ui.PAGES[page], title=label, icon=icon, default=page == "overview")
              for page, label, icon in pages]
    for section, pages in ui.NAVIGATION.items()
}, position="hidden")
ui.sidebar(navigation.url_path)
navigation.run()

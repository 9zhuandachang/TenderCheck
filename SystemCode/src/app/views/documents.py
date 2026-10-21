import csv
import io

import streamlit as st

from tcui import config, engine, service, store, ui
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()

st.title("Documents")
st.caption("Tender, addenda and bid PDFs for this project. Only searchable (text) PDFs are supported.")

if config.LLM_PROVIDER:
    st.warning(f"Uploaded text is sent to {config.LLM_PROVIDER} during extraction.", icon=":material/cloud_upload:")

# ----------------------------------------------------------------- package size
pages = service.package_pages(pid)
c1, c2 = st.columns([1, 3], vertical_alignment="center")
c1.metric("Pages in this review package", f"{pages} / {config.MAX_PACKAGE_PAGES}")
with c2:
    st.progress(min(pages / config.MAX_PACKAGE_PAGES, 1.0))
    if pages > config.MAX_PACKAGE_PAGES:
        st.error("The selected tender, addenda and bid exceed the 100-page limit. Deselect superseded "
                 "documents or split the package.")
    else:
        st.caption("Counted over the documents selected for the current round; older unselected versions "
                   "do not count.")

# ----------------------------------------------------------------- upload
st.subheader("Upload")
counter = st.session_state.setdefault("upload_counter", 0)
with st.form("upload_form", clear_on_submit=True, border=True):
    cols = st.columns(3)
    uploads = {}
    for col, (cat, label) in zip(cols, config.DOC_CATEGORIES.items()):
        uploads[cat] = col.file_uploader(label, type=["pdf"], accept_multiple_files=True, key=f"up_{cat}_{counter}")
    submitted = st.form_submit_button("Upload and parse", type="primary", icon=":material/upload:")
if submitted:
    added, skipped = [], []
    for cat, files in uploads.items():
        for f in files or []:
            try:
                doc_id, created = service.add_document(pid, cat, f.name, f.getvalue())
                (added if created else skipped).append(f.name)
            except ServiceError as e:
                st.error(str(e))
    if added:
        ui.flash(f"Uploaded {len(added)} file(s): {', '.join(added)}. Parsing has started.")
    if skipped:
        ui.flash(f"Already in this project (identical file): {', '.join(skipped)}", "info")
    st.session_state["upload_counter"] = counter + 1
    st.rerun()

ui.job_progress(pid, "parse:")

# ----------------------------------------------------------------- document list
docs = store.list_documents(pid)
if not docs:
    st.info("No documents yet.")
    st.stop()

st.subheader("Document versions")
st.caption("Tick the documents that take part in the next review round. Only one tender and one bid version "
           "can be selected; uploading a new bid version selects it automatically.")
rows = [{
    "id": d["id"],
    "In review": bool(d["selected"]),
    "Document": d["label"],
    "Type": config.DOC_CATEGORIES[d["category"]],
    "File": d["filename"],
    "Pages": d["pages"],
    "Parse status": d["parse_status"],
    "Unreadable pages": ", ".join(map(str, d["failed_pages"] or [])) or "",
    "Uploaded": (d["uploaded_at"] or "").replace("T", " "),
} for d in docs]
edited = st.data_editor(
    rows, hide_index=True, width="stretch",
    # A new key whenever the stored selection changes, so stale edits are not re-applied to fresh data.
    key="doc_editor_" + "_".join(f"{d['id']}{d['selected']}" for d in docs),
    disabled=["Document", "Type", "File", "Pages", "Parse status", "Unreadable pages", "Uploaded"],
    column_config={"id": None},
)
changed = [(r["id"], r["In review"]) for r, old in zip(edited, rows) if r["In review"] != old["In review"]]
if changed:
    for doc_id, sel in changed:
        service.set_document_selected(doc_id, sel)
    st.rerun()

problems = [d for d in docs if d["parse_status"] == "Failed" or d["failed_pages"]]
for d in problems:
    with st.container(border=True):
        if d["parse_status"] == "Failed":
            st.error(f"**{d['label']}** could not be parsed: {d['parse_error']}")
        else:
            st.warning(f"**{d['label']}**: pages {', '.join(map(str, d['failed_pages']))} have no text layer "
                       "(scanned?). Clauses or evidence on these pages cannot be checked automatically. "
                       "Upload a searchable version, or review those pages manually.")
        if st.button("Retry parsing", key=f"retry_{d['id']}"):
            service.parse_document(d["id"])
            st.rerun()

pending = store.pending_amendments(pid)
if pending:
    st.info(f"{len(pending)} addendum change(s) need a decision before review.", icon=":material/edit_note:")
    st.page_link(ui.PAGES["requirements"], label="Decide addendum changes", icon=":material/arrow_forward:")

# ----------------------------------------------------------------- drawing register
st.subheader("Drawing register")
drawings = store.query("SELECT * FROM drawings WHERE project_id=? ORDER BY seq, id", (pid,))
by_id = ui.doc_map(pid)
current = engine.current_register(drawings)
if drawings:
    ui.table([{
        "Drawing": d["drawing_no"],
        "Title": d["title"],
        "Revision": d["revision"],
        "Date": d["date"],
        "Source": f"{by_id.get(d['doc_id'], {}).get('label', 'CSV upload')}"
                  + (f" p.{d['page']}" if d["page"] else ""),
        "Status": "Current" if current.get(d["drawing_no"]) is d else "Superseded",
    } for d in drawings])
else:
    st.caption("No drawing register yet. It is read from the tender and addenda when requirements are "
               "extracted, or you can upload one as CSV.")

with st.expander("Upload drawing register (CSV)"):
    st.caption("Columns: drawing_no, title, revision, date. Rows are added after the extracted register and "
               "take precedence for the same drawing number.")
    f = st.file_uploader("Drawing register CSV", type=["csv"], key=f"dwg_csv_{counter}")
    if f and st.button("Add to register"):
        reader = csv.DictReader(io.StringIO(f.getvalue().decode("utf-8-sig")))
        seq = (store.query_one("SELECT MAX(seq) AS s FROM drawings WHERE project_id=?", (pid,))["s"] or 0)
        n = 0
        for row in reader:
            if row.get("drawing_no"):
                seq += 1
                store.insert("drawings", {"project_id": pid, "doc_id": None, "source": "csv", "seq": seq,
                                          "drawing_no": row["drawing_no"].strip(),
                                          "title": (row.get("title") or "").strip(),
                                          "revision": (row.get("revision") or "").strip().upper(),
                                          "date": (row.get("date") or "").strip()})
                n += 1
        store.log_action(pid, "upload_drawing_register", ui.actor(), ui.role(), payload={"rows": n})
        ui.flash(f"Added {n} drawing(s).")
        st.session_state["upload_counter"] = counter + 1
        st.rerun()

bid = store.selected_bid(pid)
if drawings and bid and bid["pages"]:
    findings = engine.check_drawings(drawings, engine.extract_drawing_refs(engine.load_page_texts(bid["path"])))
    issues = [f for f in findings if f["kind"] != "ok"]
    st.markdown(f"**Drawing check against {bid['label']}**")
    if issues:
        ui.table([{"Drawing": f["key"].replace("DWG ", ""), "Issue": {
            "duplicate": "Duplicate number in register", "superseded": "Bid uses superseded revision",
            "unknown": "Not in register"}[f["kind"]], "Detail": f["explanation"],
            "Bid page": f.get("bid_page") or ""} for f in issues])
        st.caption("These findings are added to the Review Workspace as Consistency items when the review runs.")
    else:
        st.success("No drawing conflicts found.")

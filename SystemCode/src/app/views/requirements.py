import csv
import io
import json

import streamlit as st

from tcui import config, engine, service, store, ui
from tcui.service import ServiceError

project = ui.require_project()
pid = project["id"]
ui.show_flash()
docs = ui.doc_map(pid)

st.title("Requirements")
st.caption("Requirements extracted from the tender and addenda. Correct categories and constraints, mark "
           "clauses that do not apply, then confirm the list to enable the review.")

extracting = ui.job_progress(pid, "extract")
ui.job_error(pid, "extract")
reqs = store.list_requirements(pid, include_drawings=False)
has_rounds = bool(store.list_rounds(pid))

# ----------------------------------------------------------------- extraction / import
if not reqs and not extracting:
    c1, c2 = st.columns(2, border=True)
    with c1:
        st.subheader("Extract from documents")
        st.markdown("Find numbered clauses with obligations (shall / must) in the selected tender, apply "
                    "addendum changes, and read the drawing register.")
        if st.button("Extract requirements", type="primary", icon=":material/auto_awesome:"):
            try:
                service.extract_requirements(pid)
                st.rerun()
            except ServiceError as e:
                st.error(str(e))
    with c2:
        st.subheader("Import an annotated list")
        st.markdown("JSON list or CSV with `clause_no`, `text`, optional `category` and `page`. Use this for "
                    "historical case replay.")
        f = st.file_uploader("Requirement file", type=["json", "csv"], label_visibility="collapsed")
        if f and st.button("Import"):
            raw = f.getvalue().decode("utf-8-sig")
            rows = json.loads(raw) if f.name.endswith(".json") else list(csv.DictReader(io.StringIO(raw)))
            n = service.import_requirements(pid, rows, ui.actor(), ui.role())
            ui.flash(f"Imported {n} requirement(s).")
            st.rerun()
    st.stop()
if extracting:
    st.stop()

# ----------------------------------------------------------------- addendum decisions
pending = store.pending_amendments(pid)
if pending:
    st.subheader(f"Addendum changes to decide ({len(pending)})")
    for r in pending:
        with st.container(border=True):
            src = docs.get(r["amended_by_doc_id"], {}).get("label", "Addendum")
            if r["origin"] == "addendum":
                st.markdown(f"**New clause {r['clause_no']}** from {src} p.{r['amendment_page']}")
                st.markdown(f"<div class='tc-req'>{r['amendment_text']}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"**Clause {r['clause_no']}** amended by {src} p.{r['amendment_page']}")
                a, b = st.columns(2)
                a.caption("Original")
                a.markdown(f"<div class='tc-req'>{r['text']}</div>", unsafe_allow_html=True)
                b.caption("Amended")
                b.markdown(f"<div class='tc-req'>{r['amendment_text']}</div>", unsafe_allow_html=True)
            c1, c2, c3 = st.columns([1, 1, 4])
            if c1.button("Apply", key=f"acc_{r['id']}", type="primary"):
                service.decide_amendment(r["id"], True, ui.actor(), ui.role())
                st.rerun()
            note = c3.text_input("Reason if not applied", key=f"rej_note_{r['id']}", label_visibility="collapsed",
                                 placeholder="Reason if not applied")
            if c2.button("Do not apply", key=f"rej_{r['id']}"):
                service.decide_amendment(r["id"], False, ui.actor(), ui.role(), note)
                st.rerun()

# ----------------------------------------------------------------- status and confirmation
applicable = [r for r in reqs if r["applicable"]]
confirmed = bool(project["req_confirmed_at"])
m1, m2, m3, m4 = st.columns(4)
m1.metric("Requirements", len(reqs))
m2.metric("Applicable", len(applicable))
m3.metric("Amended by addenda", sum(r["amendment_status"] == "accepted" for r in reqs))
m4.metric("List status", "Confirmed" if confirmed else "Not confirmed")

if not confirmed:
    if st.button("Confirm requirement list", type="primary", icon=":material/task_alt:", disabled=bool(pending)):
        try:
            service.confirm_requirements(pid, ui.actor(), ui.role())
            ui.flash("Requirement list confirmed. You can start the review from the Overview page.")
            st.rerun()
        except ServiceError as e:
            st.error(str(e))
    if pending:
        st.caption("Decide the addendum changes above first.")
else:
    st.success(f"Confirmed on {project['req_confirmed_at'].replace('T', ' ')}. Later edits are logged and, "
               "if a round is open, the affected clause is re-checked.", icon=":material/task_alt:")

# ----------------------------------------------------------------- editable list
st.subheader("Requirement list")
category_filter = st.multiselect("Category", config.CATEGORIES, placeholder="All categories")
shown = [r for r in reqs if not category_filter or r["category"] in category_filter]


def _source(r):
    if r["amendment_status"] == "accepted" and r["amended_by_doc_id"]:
        return f"{docs.get(r['amended_by_doc_id'], {}).get('label', '')} p.{r['amendment_page']}"
    doc = docs.get(r["source_doc_id"])
    return f"{doc['label']} p.{r['page']}" if doc and r["page"] else ""


rows = [{
    "id": r["id"],
    "Clause": r["clause_no"],
    "Category": r["category"],
    "Requirement": r["text"],
    "Applicable": bool(r["applicable"]),
    "Reason if not applicable": r["na_reason"] or "",
    "Constraint": engine.describe_constraint(r["constraint"]),
    "Source": _source(r),
    "Origin": r["origin"] + (" · amended" if r["amendment_status"] == "accepted" and r["origin"] != "addendum" else ""),
} for r in shown]
editor_key = "req_editor_" + str(hash(tuple((r["id"], r["text"], r["category"], r["applicable"], r["na_reason"])
                                          for r in shown)))
edited = st.data_editor(
    rows, hide_index=True, width="stretch", key=editor_key,
    disabled=["Clause", "Constraint", "Source", "Origin"],
    column_config={
        "id": None,
        "Category": st.column_config.SelectboxColumn(options=config.CATEGORIES, required=True, width="small"),
        "Requirement": st.column_config.TextColumn(width="large"),
        "Applicable": st.column_config.CheckboxColumn(width="small"),
    },
)
changes = {}
for new, old in zip(edited, rows):
    diff = {}
    if new["Category"] != old["Category"]:
        diff["category"] = new["Category"]
    if new["Requirement"] != old["Requirement"]:
        diff["text"] = new["Requirement"]
    if new["Applicable"] != old["Applicable"]:
        diff["applicable"] = int(new["Applicable"])
    if new["Reason if not applicable"] != old["Reason if not applicable"]:
        diff["na_reason"] = new["Reason if not applicable"]
    if diff:
        changes[old["id"]] = diff
if changes:
    with st.container(border=True):
        st.markdown(f"**{len(changes)} edited requirement(s).** Original values are kept in the audit log.")
        c1, c2, c3 = st.columns([4, 1, 1], vertical_alignment="bottom")
        reason = c1.text_input("Reason for the changes", key="req_change_reason")
        if c2.button("Save", type="primary"):
            try:
                for req_id, diff in changes.items():
                    service.update_requirement(req_id, diff, reason, ui.actor(), ui.role())
                ui.flash("Changes saved.")
                st.rerun()
            except ServiceError as e:
                st.error(str(e))
        if c3.button("Discard"):
            del st.session_state[editor_key]
            st.rerun()

# ----------------------------------------------------------------- constraint editor
with st.expander("Correct an extracted constraint"):
    by_id = {r["id"]: r for r in reqs}
    req_id = st.selectbox("Requirement", list(by_id), format_func=lambda i: f"{by_id[i]['clause_no']} · "
                          f"{by_id[i]['text'][:80]}", key="cons_req")
    if req_id:
        c = dict(by_id[req_id]["constraint"] or {})
        st.markdown(f"<div class='tc-req'>{by_id[req_id]['text']}</div>", unsafe_allow_html=True)
        types = ["numeric", "presence", "document"]
        ctype = st.radio("Check type", types, index=types.index(c.get("type", "presence")) if c.get("type") in types
                         else 1, horizontal=True, key=f"cons_type_{req_id}",
                         captions=["Compare a value", "Statement present", "Submission present"])
        field = st.text_input("Field", c.get("field", ""), key=f"cons_field_{req_id}")
        new_c = {"type": ctype, "field": field}
        if ctype == "numeric":
            a, b, d = st.columns(3)
            ops = ["<=", ">=", "=="]
            new_c["operator"] = a.selectbox("Operator", ops, index=ops.index(c.get("operator", "<=")),
                                            format_func=lambda o: engine.OP_SYMBOL[o], key=f"cons_op_{req_id}")
            new_c["value"] = b.number_input("Value", value=float(c.get("value") or 0), key=f"cons_val_{req_id}")
            new_c["unit"] = d.text_input("Unit", c.get("unit", ""), key=f"cons_unit_{req_id}",
                                         help="e.g. days, weeks, months, years, %, USD, MPa")
        reason = st.text_input("Reason", key=f"cons_reason_{req_id}")
        if st.button("Save constraint", key=f"cons_save_{req_id}"):
            try:
                service.update_requirement(req_id, {"constraint": new_c}, reason, ui.actor(), ui.role())
                ui.flash("Constraint updated.")
                st.rerun()
            except ServiceError as e:
                st.error(str(e))

# ----------------------------------------------------------------- add requirement
with st.expander("Add a missing requirement"):
    with st.form("add_req", clear_on_submit=True):
        a, b = st.columns([1, 2])
        clause = a.text_input("Clause number")
        category = b.selectbox("Category", ["Auto-detect"] + config.CATEGORIES)
        text = st.text_area("Requirement text")
        src_docs = [d for d in docs.values() if d["category"] in ("tender", "addendum")]
        a, b = st.columns([2, 1])
        doc_id = a.selectbox("Source document", [d["id"] for d in src_docs],
                             format_func=lambda i: docs[i]["label"]) if src_docs else None
        page = b.number_input("Page", min_value=1, value=1)
        reason = st.text_input("Reason")
        if st.form_submit_button("Add requirement"):
            try:
                service.add_requirement(pid, clause, text, None if category == "Auto-detect" else category,
                                        doc_id, page, reason, ui.actor(), ui.role())
                ui.flash(f"Added clause {clause}.")
                st.rerun()
            except ServiceError as e:
                st.error(str(e))

if not has_rounds:
    with st.expander("Re-extract"):
        st.caption("Replaces extracted and addendum requirements (manual and imported ones are kept). "
                   "Only possible before the first review round.")
        if st.button("Re-extract requirements"):
            try:
                service.extract_requirements(pid)
                st.rerun()
            except ServiceError as e:
                st.error(str(e))

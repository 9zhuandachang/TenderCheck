"""Compliance matrix (Excel) and review report (PDF) for a review round."""

import io
import textwrap
from typing import Any, Dict, List

import pandas as pd
import pymupdf as fitz
from openpyxl.styles import Font

from . import config, service, store


def _doc_label(docs: Dict[int, Dict[str, Any]], doc_id) -> str:
    doc = docs.get(doc_id)
    return doc["label"] if doc else ""


def matrix_rows(round_id: int) -> List[Dict[str, Any]]:
    rnd = store.get_round(round_id)
    docs = service.documents_by_id(rnd["project_id"])
    bid_label = _doc_label(docs, rnd["bid_doc_id"])
    remediation = {}
    for item in store.list_remediations(rnd["project_id"]):
        remediation[item["requirement_id"]] = item["status"]
    rows = []
    for r in store.list_results(round_id):
        source_doc = r["amended_by_doc_id"] if r["amendment_status"] == "accepted" else r["source_doc_id"]
        source_page = r["amendment_page"] if r["amendment_status"] == "accepted" else r["req_page"]
        source = f"{_doc_label(docs, source_doc)} p.{source_page}" if source_doc and source_page else ""
        evidence = "; ".join(f"{bid_label} p.{e['page']}" for e in (r["evidence"] or []) if e.get("page"))
        rows.append({
            "Clause": r["clause_no"],
            "Category": r["category"],
            "Requirement": r["req_text"],
            "Source": source,
            "Amended by": _doc_label(docs, r["amended_by_doc_id"]) if r["amendment_status"] == "accepted" else "",
            "System verdict": r["verdict"],
            "Original system verdict": r["original_verdict"] if r["corrected"] else "",
            "Applicable": "Yes" if r["applicable"] else f"No - {r['na_reason'] or ''}",
            "Reviewer decision": r["human_status"],
            "Reviewer note": r["note"] or "",
            "Bid evidence": evidence,
            "Remediation": remediation.get(r["requirement_id"], ""),
            "Rule": r["rule_id"],
            "Round": rnd["no"],
            "Bid version": bid_label,
        })
    return rows


def outstanding(round_id: int) -> Dict[str, List[Dict[str, Any]]]:
    rnd = store.get_round(round_id)
    results = store.list_results(round_id)
    applicable = [r for r in results if r["applicable"]]
    failed_docs = [d for d in store.list_documents(rnd["project_id"], selected_only=True) if d["failed_pages"]]
    return {
        "Unreviewed": [r for r in applicable if r["human_status"] == config.UNREVIEWED],
        "Insufficient evidence": [r for r in applicable if r["verdict"] == config.INSUFFICIENT],
        "Unresolved non-compliance": [r for r in applicable
                                      if r["verdict"] == config.NON_COMPLIANT and r["human_status"] != config.CLOSED],
        "Not applicable": [r for r in results if not r["applicable"]],
        "Unreadable pages": [{"clause_no": d["label"], "req_text": f"Pages without text: {d['failed_pages']}"}
                             for d in failed_docs],
    }


def _signoff_rows(rnd: Dict[str, Any], project: Dict[str, Any]) -> List[Dict[str, Any]]:
    docs = store.list_documents(project["id"])
    used = [d["label"] for d in docs if d["id"] in (rnd["doc_ids"] or [])]
    return [
        {"Item": "Project", "Value": project["name"]},
        {"Item": "Review round", "Value": rnd["no"]},
        {"Item": "Report status", "Value": "SIGNED" if rnd["signed_at"] else "DRAFT - not signed off"},
        {"Item": "Documents", "Value": ", ".join(used)},
        {"Item": "Engine", "Value": rnd["engine"]},
        {"Item": "Rule version", "Value": rnd["rule_version"]},
        {"Item": "Signed by", "Value": rnd["signer"] or ""},
        {"Item": "Signed at", "Value": (rnd["signed_at"] or "").replace("T", " ")},
        {"Item": "Sign-off comment", "Value": rnd["sign_comment"] or ""},
        {"Item": "Exported at", "Value": store.now().replace("T", " ")},
    ]


def excel_bytes(round_id: int) -> bytes:
    rnd = store.get_round(round_id)
    project = store.get_project(rnd["project_id"])
    open_items = outstanding(round_id)
    outstanding_rows = [
        {"Group": group, "Clause": r.get("clause_no"), "Requirement": r.get("req_text"),
         "System verdict": r.get("verdict", ""), "Reviewer decision": r.get("human_status", "")}
        for group, items in open_items.items() for r in items
    ]
    audit = [
        {"Time": a["at"], "Actor": a["actor"], "Role": a["role"], "Action": a["action"], "Note": a["note"],
         "Details": str(a["payload"])}
        for a in store.list_actions(project["id"])
    ]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sheets = {
            "Compliance matrix": pd.DataFrame(matrix_rows(round_id)),
            "Outstanding": pd.DataFrame(outstanding_rows or [{"Group": "None"}]),
            "Sign-off": pd.DataFrame(_signoff_rows(rnd, project)),
            "Audit log": pd.DataFrame(audit or [{"Action": "None"}]),
        }
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 70)
            for cell in ws[1]:
                cell.font = Font(bold=True)
    return buf.getvalue()


class _PdfWriter:
    W, H, M = 595, 842, 50

    def __init__(self, header: str):
        self.doc = fitz.open()
        self.header = header
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=self.W, height=self.H)
        self.page.insert_text((self.M, 30), self.header, fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
        self.y = self.M + 10

    def line(self, text: str, size: float = 9.5, bold: bool = False, indent: int = 0, color=(0, 0, 0)) -> None:
        width = int((self.W - 2 * self.M - indent) / (size * 0.5))
        for part in textwrap.wrap(text, width) or [""]:
            if self.y > self.H - self.M:
                self._new_page()
            self.page.insert_text((self.M + indent, self.y), part, fontsize=size,
                                  fontname="hebo" if bold else "helv", color=color)
            self.y += size * 1.45

    def gap(self, h: float = 8) -> None:
        self.y += h

    def tobytes(self) -> bytes:
        total = len(self.doc)
        for i, page in enumerate(self.doc, start=1):
            page.insert_text((self.W / 2 - 20, self.H - 25), f"Page {i} of {total}", fontsize=8, fontname="helv")
        return self.doc.tobytes()


def pdf_bytes(round_id: int) -> bytes:
    rnd = store.get_round(round_id)
    project = store.get_project(rnd["project_id"])
    status = "SIGNED" if rnd["signed_at"] else "DRAFT - NOT SIGNED OFF"
    w = _PdfWriter(f"TenderCheck review report - {project['name']} - Round {rnd['no']} - {status}")
    w.line("TenderCheck Compliance Review Report", 16, bold=True)
    w.line(status, 11, bold=True, color=(0.7, 0.1, 0.1) if not rnd["signed_at"] else (0.1, 0.5, 0.2))
    w.gap()
    for row in _signoff_rows(rnd, project):
        w.line(f"{row['Item']}: {row['Value']}")
    w.gap()
    s = service.round_summary(round_id)
    w.line("Summary", 12, bold=True)
    w.line(f"Applicable clauses: {s['applicable']} (not applicable: {s['not_applicable']})")
    w.line(f"Compliant: {s['compliant']}   Non-compliant: {s['non_compliant']}   "
           f"Insufficient evidence: {s['insufficient']}")
    w.line(f"Reviewer decisions recorded: {s['reviewed']}/{s['applicable']}   "
           f"In remediation: {s['to_remediate']}")
    w.line("Sign-off confirms the review results; it does not state that the bid is fully compliant. "
           "Open issues are disclosed below.", 8.5, color=(0.35, 0.35, 0.35))
    w.gap()
    w.line("Outstanding items", 12, bold=True)
    any_open = False
    for group, items in outstanding(round_id).items():
        if not items:
            continue
        any_open = True
        w.line(f"{group} ({len(items)})", 10, bold=True)
        for r in items:
            w.line(f"{r.get('clause_no')}: {r.get('req_text')}", 9, indent=12)
    if not any_open:
        w.line("None.")
    w.gap()
    w.line("Compliance matrix", 12, bold=True)
    for row in matrix_rows(round_id):
        w.line(f"{row['Clause']}  [{row['Category']}]  {row['System verdict']}  /  {row['Reviewer decision']}",
               9.5, bold=True)
        w.line(row["Requirement"], 9, indent=12)
        details = [f"Source: {row['Source']}" if row["Source"] else "", f"Evidence: {row['Bid evidence']}"
                   if row["Bid evidence"] else "Evidence: none", f"Rule: {row['Rule']}"]
        if row["Reviewer note"]:
            details.append(f"Note: {row['Reviewer note']}")
        if row["Remediation"]:
            details.append(f"Remediation: {row['Remediation']}")
        w.line("   ".join(d for d in details if d), 8.5, indent=12, color=(0.3, 0.3, 0.3))
        w.gap(3)
    return w.tobytes()

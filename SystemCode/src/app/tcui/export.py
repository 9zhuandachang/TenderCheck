"""Compliance matrix (Excel) and review report (PDF) for a review round."""

import io
import textwrap
from typing import Any, Dict, List

import pandas as pd
import pymupdf as fitz
from openpyxl.styles import Alignment, Font, PatternFill

from . import config, service, store


def _doc_label(docs: Dict[int, Dict[str, Any]], doc_id) -> str:
    doc = docs.get(doc_id)
    return doc["label"] if doc else ""


def _extract_bid_response_summary(r: Dict[str, Any], rem_draft: str = "") -> str:
    """Extract the clean Bid Response summary for the compliance matrix."""
    if rem_draft:
        preview = rem_draft.replace("\n", " ").strip()
        return f"[已修改] {preview[:120]}" + ("…" if len(preview) > 120 else "")
    fields = r.get("fields") or []
    if fields and fields[0].get("actual"):
        return str(fields[0]["actual"]).strip()
    evidence = r.get("evidence") or []
    if evidence and evidence[0].get("snippet"):
        snip = str(evidence[0]["snippet"]).replace("\n", " ").strip()
        return snip[:120] + ("…" if len(snip) > 120 else "")
    return "—"


def _extract_bid_evidence_locations(r: Dict[str, Any], default_bid_label: str) -> str:
    """Format multi-volume Bid evidence locations (`分册名 p.页码`)."""
    locs = []
    for e in (r.get("evidence") or []):
        pg = e.get("page")
        if not pg:
            continue
        lbl = e.get("doc_label") or default_bid_label or "Bid"
        loc = f"{lbl} p.{pg}"
        if loc not in locs:
            locs.append(loc)
    return "; ".join(locs) if locs else "—"


def matrix_rows(round_id: int) -> List[Dict[str, Any]]:
    rnd = store.get_round(round_id)
    docs = service.documents_by_id(rnd["project_id"])
    bid_label = _doc_label(docs, rnd["bid_doc_id"])
    remediation_map = {}
    for item in store.list_remediations(rnd["project_id"]):
        remediation_map[item["requirement_id"]] = item

    rows = []
    for r in store.list_results(round_id):
        rem_item = remediation_map.get(r["requirement_id"])
        rem_status = rem_item["status"] if rem_item else ""
        rem_draft = (rem_item.get("draft") or "") if (rem_item and rem_status == config.CLOSED) else ""

        if rem_status == config.CLOSED:
            review_status = "Remediated (已修改)"
        elif r["human_status"] == config.TO_REMEDIATE:
            review_status = "To remediate (待修改)"
        elif r["human_status"] == config.CONFIRMED:
            review_status = "Confirmed"
        else:
            review_status = r["human_status"] or "Unreviewed"

        rows.append({
            "Clause": r["clause_no"],
            "Category": r["category"],
            "Tender Requirement": r["req_text"],
            "Bid Response": _extract_bid_response_summary(r, rem_draft),
            "Bid Evidence": _extract_bid_evidence_locations(r, bid_label),
            "System Verdict": r["verdict"],
            "Review Status": review_status,
        })
    return rows


def issues_and_remediation_rows(round_id: int) -> List[Dict[str, Any]]:
    """Build rows specifically for the 70 non-passing / remediated clauses sheet."""
    all_rows = matrix_rows(round_id)
    results_by_clause = {r["clause_no"]: r for r in store.list_results(round_id)}
    out = []
    for row in all_rows:
        r = results_by_clause.get(row["Clause"], {})
        if row["System Verdict"] != config.COMPLIANT or r.get("corrected"):
            out.append({
                "Clause": row["Clause"],
                "Category": row["Category"],
                "Tender Requirement": row["Tender Requirement"],
                "Bid Response": row["Bid Response"],
                "Bid Evidence": row["Bid Evidence"],
                "System Verdict": row["System Verdict"],
                "Review Status": row["Review Status"],
                "Diagnostic / Note": r.get("explanation") or "",
            })
    return out


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
        {"Item": "Documents", "Value": ", ".join(used)},
        {"Item": "Engine", "Value": rnd["engine"]},
        {"Item": "Rule version", "Value": rnd["rule_version"]},
        {"Item": "Exported at", "Value": store.now().replace("T", " ")},
    ]


def excel_bytes(round_id: int) -> bytes:
    buf = io.BytesIO()
    matrix_df = pd.DataFrame(matrix_rows(round_id))
    issues_list = issues_and_remediation_rows(round_id)
    issues_df = pd.DataFrame(issues_list if issues_list else [{"Clause": "None"}])

    header_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        sheets = {
            "Compliance Matrix": matrix_df,
            "Issues & Remediation": issues_df,
        }
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            ws.freeze_panes = "A2"
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(12, width + 3), 65)
            for cell in ws[1]:
                cell.font = Font(bold=True)
                cell.fill = header_fill
                cell.alignment = Alignment(vertical="center")
    return buf.getvalue()


class _PdfWriter:
    W, H, M = 595, 842, 50

    def __init__(self, header: str):
        self.doc = fitz.open()
        self.header = header
        self._new_page()

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=self.W, height=self.H)
        self.page.insert_text((self.M, 30), self.header, fontsize=8, fontname="china-s", color=(0.4, 0.4, 0.4))
        self.y = self.M + 10

    def line(self, text: str, size: float = 9.5, bold: bool = False, indent: int = 0, color=(0, 0, 0)) -> None:
        width = int((self.W - 2 * self.M - indent) / (size * 0.6))
        for part in textwrap.wrap(str(text or ""), max(20, width)) or [""]:
            if self.y > self.H - self.M:
                self._new_page()
            self.page.insert_text((self.M + indent, self.y), part, fontsize=size,
                                  fontname="china-s", color=color)
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
    w = _PdfWriter(f"TenderCheck Compliance Matrix - {project['name']}")
    w.line("TenderCheck Compliance Matrix Report", 15, bold=True)
    w.gap()
    for row in _signoff_rows(rnd, project):
        w.line(f"{row['Item']}: {row['Value']}")
    w.gap()
    s = service.round_summary(round_id)
    w.line(f"Total: {s['applicable']}   Compliant: {s['compliant']}   Non-passing: {s.get('non_passing', 0)}", 10)
    w.gap()
    for row in matrix_rows(round_id):
        w.line(f"{row['Clause']} [{row['Category']}] {row['System Verdict']} / {row['Review Status']}", 9.5, bold=True)
        w.line(f"Req: {row['Tender Requirement']}", 9, indent=10)
        w.line(f"Bid: {row['Bid Response']} ({row['Bid Evidence']})", 8.5, indent=10, color=(0.3, 0.3, 0.3))
        w.gap(3)
    return w.tobytes()

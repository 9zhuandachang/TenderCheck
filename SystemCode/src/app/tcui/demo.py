"""Synthetic English demo package: tender, one addendum, an initial bid and a revised bid.

The documents are fictitious and generated with PyMuPDF so the full review loop can be shown without
confidential project data.
"""

import textwrap
from pathlib import Path
from typing import Dict, List, Tuple

import pymupdf as fitz

from . import service, store

PAGE_W, PAGE_H = 595, 842
MARGIN = 64
LINE_H = 15
WRAP = 92

# Each document is a list of pages; each page is a list of (style, text) blocks.
Block = Tuple[str, str]

TENDER: List[List[Block]] = [
    [
        ("title", "RIVERSIDE DRAINAGE UPGRADE"),
        ("title", "CONTRACT C-2026-014 - INSTRUCTIONS TO TENDERERS"),
        ("p", "Issued by Riverside Municipal Council. Tenders close on 15 November 2026 at 12:00 noon. "
              "The Employer is not bound to accept the lowest or any tender."),
        ("h", "1. GENERAL"),
        ("p", "1.1 These Instructions apply to the construction of approximately 2.4 km of stormwater drainage "
              "along Riverside Drive, including manholes, outfalls and road reinstatement."),
        ("p", "1.2 Tenders shall remain valid for a period of at least 90 days from the closing date."),
        ("p", "1.3 The Tenderer shall declare any conflict of interest in the Form of Tender."),
    ],
    [
        ("h", "2. QUALIFICATION"),
        ("p", "2.1 The Tenderer shall hold a valid ISO 9001 quality management certificate."),
        ("p", "2.2 The Tenderer shall have at least 5 years of experience in drainage works."),
        ("h", "3. PROGRAMME"),
        ("p", "3.1 The Works shall be completed within 360 calendar days from the Date of Commencement."),
        ("p", "3.2 The defects liability period shall be not less than 12 months from completion."),
    ],
    [
        ("h", "4. COMMERCIAL"),
        ("p", "4.1 The Tenderer shall provide a performance bond of not less than 10% of the Contract Sum."),
        ("p", "4.2 The Tenderer shall maintain professional indemnity insurance of at least USD 2,000,000."),
        ("h", "5. TECHNICAL"),
        ("p", "5.1 Structural concrete shall achieve a minimum compressive strength of 40 MPa."),
        ("p", "5.2 The Tenderer shall submit a Method Statement for the drainage works."),
        ("p", "5.3 The Tenderer shall submit a Site Safety Plan with the tender."),
    ],
    [
        ("h", "6. DRAWINGS"),
        ("p", "6.1 The drawings listed in the register below form part of the tender documents."),
        ("h", "DRAWING REGISTER"),
        ("row", "C-101   General Arrangement Plan   Rev A   2026-08-01"),
        ("row", "C-102   Drainage Layout Plan   Rev B   2026-08-01"),
        ("row", "C-103   Longitudinal Sections   Rev A   2026-08-01"),
        ("row", "C-104   Manhole Details   Rev A   2026-08-01"),
        ("row", "C-104   Pipe Bedding Details   Rev A   2026-08-01"),
    ],
]

ADDENDUM: List[List[Block]] = [
    [
        ("title", "ADDENDUM NO. 1"),
        ("title", "CONTRACT C-2026-014 - RIVERSIDE DRAINAGE UPGRADE"),
        ("p", "Issued 20 October 2026. This addendum forms part of the tender documents and prevails over "
              "the original documents where they differ."),
        ("p", "A1.1 Clause 3.1 is amended to read: The Works shall be completed within 330 calendar days from "
              "the Date of Commencement."),
        ("p", "A1.2 New Clause 5.4: The Tenderer shall submit a Traffic Management Plan for works on public roads."),
        ("p", "A1.3 Drawing C-102 is reissued at the revision listed below."),
        ("h", "REVISED DRAWING REGISTER ENTRIES"),
        ("row", "C-102   Drainage Layout Plan   Rev C   2026-10-20"),
    ]
]


def _bid(revised: bool) -> List[List[Block]]:
    days = "320" if revised else "345"
    concrete = "40" if revised else "35"
    insurance = "USD 2,000,000" if revised else "USD 1,500,000"
    c102 = "C" if revised else "B"
    safety = ("Our Site Safety Plan is provided in Appendix D and covers excavation, confined spaces and "
              "traffic interfaces." if revised else
              "Site safety: our team will comply with all applicable site safety regulations.")
    drawings = f"Our programme is based on Drawing C-101 Rev A and Drawing C-102 Rev {c102}."
    if not revised:
        drawings += " Pipe jacking details follow Drawing C-105 Rev A."
    return [
        [
            ("title", "TENDER SUBMISSION - CONTRACT C-2026-014"),
            ("title", "HARBOUR CIVIL ENGINEERING PTE LTD"),
            ("h", "FORM OF TENDER"),
            ("p", "We, Harbour Civil Engineering Pte Ltd, offer to execute the Works for Contract C-2026-014 "
                  "in accordance with the tender documents and addenda."),
            ("p", "Our tender remains valid for 90 days from the tender closing date."),
            ("p", "We declare that we have no conflict of interest with the Employer or its consultants."),
        ],
        [
            ("h", "COMPANY QUALIFICATIONS"),
            ("p", "We hold a valid ISO 9001:2015 quality management certificate, attached as Appendix C."),
            ("p", "We have 7 years of experience in drainage and sewer works across the region."),
            ("p", safety),
        ],
        [
            ("h", "PROGRAMME"),
            ("p", f"We will complete the Works within {days} calendar days from the date of commencement."),
            ("p", "We accept a defects liability period of 12 months from completion."),
            ("p", drawings),
        ],
        [
            ("h", "COMMERCIAL OFFER"),
            ("p", "We will provide a performance bond of 10% of the Contract Sum."),
            ("p", f"Professional indemnity insurance cover of {insurance} is held with Pacific Insurance."),
        ],
        [
            ("h", "TECHNICAL PROPOSAL"),
            ("p", "Section 5 contains our Method Statement for the drainage works, covering excavation, "
                  "pipe laying and reinstatement."),
            ("p", f"Structural concrete will achieve a compressive strength of {concrete} MPa."),
        ],
    ]


KNOWN_ISSUES = [
    {"clause_no": "3.1", "expected": "Non-compliant", "note": "Programme exceeds the amended completion period"},
    {"clause_no": "4.2", "expected": "Non-compliant", "note": "PI insurance below the required cover"},
    {"clause_no": "5.1", "expected": "Non-compliant", "note": "Concrete strength below specification"},
    {"clause_no": "5.3", "expected": "Non-compliant", "note": "No site safety plan submitted"},
    {"clause_no": "5.4", "expected": "Non-compliant", "note": "No traffic management plan (Addendum 1)"},
    {"clause_no": "DWG C-102", "expected": "Non-compliant", "note": "Bid uses superseded drawing revision"},
    {"clause_no": "DWG C-105", "expected": "Non-compliant", "note": "Bid references a drawing not in the register"},
    {"clause_no": "4.1", "expected": "Non-compliant", "note": "Bond issuer not on the approved list"},
]


def render_pdf(pages: List[List[Block]]) -> bytes:
    doc = fitz.open()
    for blocks in pages:
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        y = MARGIN
        for style, text in blocks:
            if style == "title":
                page.insert_text((MARGIN, y), text, fontsize=13, fontname="hebo")
                y += LINE_H + 6
            elif style == "h":
                y += 8
                page.insert_text((MARGIN, y), text, fontsize=11, fontname="hebo")
                y += LINE_H + 2
            elif style == "row":
                page.insert_text((MARGIN + 12, y), text, fontsize=10, fontname="cour")
                y += LINE_H
            else:
                for line in textwrap.wrap(text, WRAP):
                    page.insert_text((MARGIN, y), line, fontsize=10, fontname="helv")
                    y += LINE_H
                y += 6
    total = len(doc)
    for i, page in enumerate(doc, start=1):
        page.insert_text((PAGE_W / 2 - 25, PAGE_H - 36), f"Page {i} of {total}", fontsize=8, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def demo_files() -> Dict[str, bytes]:
    return {
        "Riverside_Tender.pdf": render_pdf(TENDER),
        "Riverside_Addendum_1.pdf": render_pdf(ADDENDUM),
        "Harbour_Bid_v1.pdf": render_pdf(_bid(revised=False)),
        "Harbour_Bid_v2_revised.pdf": render_pdf(_bid(revised=True)),
    }


def revised_bid_path(project_id: int) -> Path:
    return store.project_dir(project_id) / "demo_sources" / "Harbour_Bid_v2_revised.pdf"


def create_demo_project() -> int:
    """Create a project with the demo tender, addendum and first bid parsed and requirements extracted."""
    project_id = store.create_project("Riverside Drainage Upgrade (demo)", is_demo=True)
    files = demo_files()
    folder = store.project_dir(project_id) / "demo_sources"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    service.add_document(project_id, "tender", "Riverside_Tender.pdf", files["Riverside_Tender.pdf"], background=False)
    service.add_document(project_id, "addendum", "Riverside_Addendum_1.pdf", files["Riverside_Addendum_1.pdf"],
                         background=False)
    service.add_document(project_id, "bid", "Harbour_Bid_v1.pdf", files["Harbour_Bid_v1.pdf"], background=False)
    service._extract_job(service._no_progress, project_id)
    service.set_known_issues(project_id, KNOWN_ISSUES)
    store.update("projects", project_id, {"baseline_minutes": 180})
    return project_id

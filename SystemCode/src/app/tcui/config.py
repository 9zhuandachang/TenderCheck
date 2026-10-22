"""Static configuration for the TenderCheck review workspace."""

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
SYSTEMCODE_DIR = APP_DIR.parents[1]

DATA_DIR = Path(os.environ.get("TENDERCHECK_DATA_DIR", SYSTEMCODE_DIR / "data" / "app"))
DB_PATH = DATA_DIR / "tendercheck.db"
PROJECTS_DIR = DATA_DIR / "projects"
KB_PATH = APP_DIR / "kb" / "knowledge_base.json"

# Support full-scale Chinese engineering tender & bid packages (up to 10,000 pages).
MAX_PACKAGE_PAGES = 10000
MIN_TEXT_CHARS = 20

ENGINE_NAME = "Hybrid RAG + DeepSeek v11-arch + GlobalContext + ComplianceRuleEngine"
RULE_VERSION = "ComplianceRuleEngine-v3 (R01-R09)"
# Name of the external LLM service that receives document text, or None when nothing leaves this machine.
LLM_PROVIDER = os.environ.get("TENDERCHECK_LLM_PROVIDER") or None

CATEGORIES = [
    "Qualification",
    "Document",
    "Technical",
    "Commercial",
    "Schedule",
    "Consistency",
    "Integrity",
    "Scoring",
]

COMPLIANT = "Compliant"
NON_COMPLIANT = "Non-compliant"
INSUFFICIENT = "Insufficient evidence"
VERDICTS = [COMPLIANT, NON_COMPLIANT, INSUFFICIENT]

UNREVIEWED = "Unreviewed"
CONFIRMED = "Confirmed"
TO_REMEDIATE = "To remediate"
TO_REINSPECT = "To re-inspect"
CLOSED = "Closed"
HUMAN_STATUSES = [UNREVIEWED, CONFIRMED, TO_REMEDIATE, TO_REINSPECT, CLOSED]

DOC_CATEGORIES = {
    "tender": "Tender",
    "addendum": "Addenda / Clarifications",
    "bid": "Bid",
}

ROLES = ["Reviewer", "Manager"]

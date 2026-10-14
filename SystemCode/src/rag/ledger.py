"""
Structured Entity Ledger Builder & SQLite Store for TenderCheck RAG.

Constructs the 4 core structured tables required for deterministic compliance
and cross-document consistency verification (`Qualification`, `Commercial`,
`Schedule`, `Consistency`, `Integrity`):
1. `company_profile`: Corporate identity, credit code, qualifications, safety permit.
2. `personnel_ledger`: Key personnel (Project Manager, Technical Lead, Safety Officer)
   with every page occurrence tracked for consistency checking.
3. `performance_ledger`: Past similar engineering project records with contract amount
   and completion dates.
4. `commercial_schedule_ledger`: Bid price (numeric & Chinese uppercase), duration days,
   validity period, and bid bond across Bid Letter and Summary Schedule.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .parser import BidChunk

logger = logging.getLogger(__name__)


@dataclass
class FieldMention:
    """Tracks a single mention of a critical entity value at a specific page and section."""

    field_name: str
    value: Any
    page_no: int
    section_path: str
    chunk_id: str


@dataclass
class BidEntityLedger:
    """
    In-memory and serializable representation of a bid document's 4 core entity ledgers,
    plus a cross-page mention registry for `Consistency` verification.
    """

    document_id: str
    company_profile: Dict[str, Any] = field(default_factory=dict)
    personnel_ledger: List[Dict[str, Any]] = field(default_factory=list)
    performance_ledger: List[Dict[str, Any]] = field(default_factory=list)
    commercial_schedule_ledger: Dict[str, Any] = field(default_factory=dict)
    document_seal_registry: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    field_mentions: List[FieldMention] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "company_profile": self.company_profile,
            "personnel_ledger": self.personnel_ledger,
            "performance_ledger": self.performance_ledger,
            "commercial_schedule_ledger": self.commercial_schedule_ledger,
            "document_seal_registry": self.document_seal_registry,
            "field_mentions": [asdict(m) for m in self.field_mentions],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BidEntityLedger":
        mentions = [
            FieldMention(**m) for m in data.get("field_mentions", []) if isinstance(m, dict)
        ]
        return cls(
            document_id=str(data.get("document_id", "")),
            company_profile=dict(data.get("company_profile") or {}),
            personnel_ledger=list(data.get("personnel_ledger") or []),
            performance_ledger=list(data.get("performance_ledger") or []),
            commercial_schedule_ledger=dict(data.get("commercial_schedule_ledger") or {}),
            document_seal_registry=dict(data.get("document_seal_registry") or {}),
            field_mentions=mentions,
        )

    def check_consistency(self, field_name: str) -> Dict[str, Any]:
        """
        Verify whether all mentions of `field_name` (e.g., `'project_manager_name'`,
        `'bid_price_yuan'`, `'duration_days'`) across the entire bid are identical.
        """
        relevant = [m for m in self.field_mentions if m.field_name == field_name]
        distinct_values = sorted({str(m.value).strip() for m in relevant if str(m.value).strip()})
        is_consistent = len(distinct_values) <= 1
        return {
            "field_name": field_name,
            "is_consistent": is_consistent,
            "distinct_values": distinct_values,
            "mention_count": len(relevant),
            "mentions": [asdict(m) for m in relevant],
        }


class EntityLedgerBuilder:
    """
    Extracts and aggregates the 4 structured entity tables from parsed `BidChunk` records.
    """

    @staticmethod
    def build_from_chunks(
        document_id: str,
        parent_chunks: Sequence[BidChunk],
        child_chunks: Optional[Sequence[BidChunk]] = None,
    ) -> BidEntityLedger:
        ledger = BidEntityLedger(document_id=document_id)

        company_info: Dict[str, Any] = {
            "company_name": None,
            "credit_code": None,
            "qualifications": [],
            "safety_permit_valid": False,
            "evidence_pages": [],
        }
        commercial_info: Dict[str, Any] = {
            "bid_price_yuan": None,
            "bid_price_upper": None,
            "duration_days": None,
            "validity_days": None,
            "bond_amount_yuan": None,
            "quality_target": None,
            "evidence_pages": [],
        }

        all_chunks = list(parent_chunks) + list(child_chunks or [])

        for chunk in parent_chunks:
            # 1. Update Document & Seal Registry (for `Document` category verification)
            cat_entry = ledger.document_seal_registry.setdefault(
                chunk.doc_category,
                {
                    "present": True,
                    "chunk_count": 0,
                    "has_official_seal": False,
                    "has_signature": False,
                    "pages": [],
                },
            )
            cat_entry["chunk_count"] += 1
            cat_entry["has_official_seal"] = bool(
                cat_entry["has_official_seal"] or chunk.has_official_seal
            )
            cat_entry["has_signature"] = bool(
                cat_entry["has_signature"] or chunk.has_signature
            )
            if chunk.page_start not in cat_entry["pages"]:
                cat_entry["pages"].append(chunk.page_start)

            kv = chunk.extracted_kv or {}
            text = chunk.text

            # 2. Populate Company Profile
            if kv.get("credit_code") and not company_info["credit_code"]:
                company_info["credit_code"] = kv["credit_code"]
                company_info["evidence_pages"].append(chunk.page_start)

            if "安全生产许可证" in text or chunk.doc_category == "安全生产许可证":
                company_info["safety_permit_valid"] = True
                if chunk.page_start not in company_info["evidence_pages"]:
                    company_info["evidence_pages"].append(chunk.page_start)

            for qual_match in re.findall(
                r"([\u4e00-\u9fa5]{2,12}工程施工总承包[特一二三壹贰叁]级|[\u4e00-\u9fa5]{2,12}专业承包[一二三壹贰叁]级)",
                text,
            ):
                if qual_match not in company_info["qualifications"]:
                    company_info["qualifications"].append(qual_match)

            # 3. Populate Commercial & Schedule Summary + Mentions
            if kv.get("bid_price_yuan") is not None:
                val = kv["bid_price_yuan"]
                if commercial_info["bid_price_yuan"] is None:
                    commercial_info["bid_price_yuan"] = val
                commercial_info["evidence_pages"].append(chunk.page_start)
                ledger.field_mentions.append(
                    FieldMention(
                        field_name="bid_price_yuan",
                        value=val,
                        page_no=chunk.page_start,
                        section_path=chunk.section_path,
                        chunk_id=chunk.chunk_id,
                    )
                )

            if kv.get("bid_price_upper"):
                val_up = kv["bid_price_upper"]
                if not commercial_info["bid_price_upper"]:
                    commercial_info["bid_price_upper"] = val_up
                ledger.field_mentions.append(
                    FieldMention(
                        field_name="bid_price_upper",
                        value=val_up,
                        page_no=chunk.page_start,
                        section_path=chunk.section_path,
                        chunk_id=chunk.chunk_id,
                    )
                )

            if kv.get("duration_days"):
                dur = kv["duration_days"]
                if not commercial_info["duration_days"]:
                    commercial_info["duration_days"] = dur
                ledger.field_mentions.append(
                    FieldMention(
                        field_name="duration_days",
                        value=dur,
                        page_no=chunk.page_start,
                        section_path=chunk.section_path,
                        chunk_id=chunk.chunk_id,
                    )
                )

            m_val_days = re.search(r"投标有效期[为：:\s]*(\d{2,3})\s*(?:日历天|天)", text)
            if m_val_days and commercial_info["validity_days"] is None:
                commercial_info["validity_days"] = int(m_val_days.group(1))

            # 4. Populate Personnel Ledger
            if kv.get("person_name") or chunk.doc_category == "项目管理机构与人员简历证书":
                p_name = kv.get("person_name")
                if p_name:
                    role = kv.get("person_role") or (
                        "项目经理" if "项目经理" in text or "项目经理" in chunk.section_path else "核心管理人员"
                    )
                    ledger.personnel_ledger.append(
                        {
                            "name": p_name,
                            "role": role,
                            "id_card": kv.get("id_card"),
                            "valid_until": kv.get("valid_until"),
                            "page_no": chunk.page_start,
                            "section_path": chunk.section_path,
                            "chunk_id": chunk.chunk_id,
                        }
                    )
                    if role == "项目经理":
                        ledger.field_mentions.append(
                            FieldMention(
                                field_name="project_manager_name",
                                value=p_name,
                                page_no=chunk.page_start,
                                section_path=chunk.section_path,
                                chunk_id=chunk.chunk_id,
                            )
                        )

            # 5. Populate Engineering Performance Ledger
            if chunk.doc_category == "近年类似工程业绩证明" or "类似项目情况表" in chunk.section_path:
                has_contract = "合同" in text
                has_notice = "中标通知书" in text
                has_acceptance = "竣工" in text or "验收" in text
                ledger.performance_ledger.append(
                    {
                        "section_path": chunk.section_path,
                        "page_no": chunk.page_start,
                        "has_contract": has_contract,
                        "has_award_notice": has_notice,
                        "has_completion_acceptance": has_acceptance,
                        "excerpt": text[:240],
                        "chunk_id": chunk.chunk_id,
                    }
                )

        ledger.company_profile = company_info
        ledger.commercial_schedule_ledger = commercial_info
        return ledger

    @staticmethod
    def save_ledger(ledger: BidEntityLedger, output_dir: Path) -> Tuple[Path, Path]:
        """
        Persist `BidEntityLedger` to both `ledger.json` and a relational SQLite
        database `ledger.db` inside `output_dir`.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        json_path = output_dir / "ledger.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(ledger.to_dict(), f, ensure_ascii=False, indent=2)

        db_path = output_dir / "ledger.db"
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS personnel_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT,
                    name TEXT,
                    role TEXT,
                    id_card TEXT,
                    valid_until TEXT,
                    page_no INTEGER,
                    section_path TEXT,
                    chunk_id TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS field_mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id TEXT,
                    field_name TEXT,
                    value TEXT,
                    page_no INTEGER,
                    section_path TEXT,
                    chunk_id TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS document_seal_registry (
                    doc_category TEXT PRIMARY KEY,
                    present INTEGER,
                    chunk_count INTEGER,
                    has_official_seal INTEGER,
                    has_signature INTEGER,
                    pages_json TEXT
                )
                """
            )
            cur.execute("DELETE FROM personnel_ledger WHERE document_id = ?", (ledger.document_id,))
            cur.execute("DELETE FROM field_mentions WHERE document_id = ?", (ledger.document_id,))
            cur.execute("DELETE FROM document_seal_registry")

            for p in ledger.personnel_ledger:
                cur.execute(
                    """
                    INSERT INTO personnel_ledger
                    (document_id, name, role, id_card, valid_until, page_no, section_path, chunk_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ledger.document_id,
                        p.get("name"),
                        p.get("role"),
                        p.get("id_card"),
                        p.get("valid_until"),
                        p.get("page_no"),
                        p.get("section_path"),
                        p.get("chunk_id"),
                    ),
                )

            for m in ledger.field_mentions:
                cur.execute(
                    """
                    INSERT INTO field_mentions
                    (document_id, field_name, value, page_no, section_path, chunk_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ledger.document_id,
                        m.field_name,
                        str(m.value),
                        m.page_no,
                        m.section_path,
                        m.chunk_id,
                    ),
                )

            for cat, info in ledger.document_seal_registry.items():
                cur.execute(
                    """
                    INSERT INTO document_seal_registry
                    (doc_category, present, chunk_count, has_official_seal, has_signature, pages_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        cat,
                        int(bool(info.get("present", True))),
                        int(info.get("chunk_count", 0)),
                        int(bool(info.get("has_official_seal", False))),
                        int(bool(info.get("has_signature", False))),
                        json.dumps(info.get("pages", []), ensure_ascii=False),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        return json_path, db_path

    @staticmethod
    def load_ledger(ledger_json_path: Path) -> BidEntityLedger:
        with open(ledger_json_path, "r", encoding="utf-8") as f:
            return BidEntityLedger.from_dict(json.load(f))

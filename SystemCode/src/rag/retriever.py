"""
8-Category Aware Hybrid Router Retriever for TenderCheck RAG.

Routes each tender requirement clause based on its predicted or ground-truth
`Requirement Type` (`Qualification`, `Document`, `Technical`, `Commercial`,
`Schedule`, `Consistency`, `Integrity`, `Scoring`) across both:
1. The Structured Entity Ledger (`BidEntityLedger`: SQLite / JSON)
2. The Hybrid Dense+BM25 Chunk Store (`HybridBidIndex`)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..data.schema import LABEL_CN_NAMES, REQUIREMENT_TYPES
from .indexer import HybridBidIndex, RetrievedChunkHit
from .ledger import BidEntityLedger
from .parser import BidChunk

logger = logging.getLogger(__name__)


@dataclass
class RetrievedEvidenceBundle:
    """
    Unified evidence bundle returned by `CategoryRouterRetriever` for downstream
    Symbolic Rule Engine / LLM compliance verification.
    """

    requirement_id: str
    requirement_text: str
    section_text: str
    requirement_type: str
    requirement_type_cn: str
    routing_strategy: str
    ledger_evidence: Dict[str, Any] = field(default_factory=dict)
    chunk_hits: List[RetrievedChunkHit] = field(default_factory=list)
    evidence_pages: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "requirement_text": self.requirement_text,
            "section_text": self.section_text,
            "requirement_type": self.requirement_type,
            "requirement_type_cn": self.requirement_type_cn,
            "routing_strategy": self.routing_strategy,
            "evidence_pages": self.evidence_pages,
            "ledger_evidence": self.ledger_evidence,
            "chunk_hits": [h.to_dict() for h in self.chunk_hits],
        }


class CategoryRouterRetriever:
    """
    Executes category-specific retrieval strategies tailored to the 8 official
    TenderCheck requirement categories.
    """

    def __init__(
        self,
        index: HybridBidIndex,
        ledger: BidEntityLedger,
        classifier=None,
    ) -> None:
        """
        Args:
            index: Built `HybridBidIndex` containing Parent and Child chunks.
            ledger: Extracted `BidEntityLedger` containing the 4 core structured tables.
            classifier: Optional `BaseRequirementClassifier` to auto-predict
                `requirement_type` when not explicitly provided.
        """
        self.index = index
        self.ledger = ledger
        self.classifier = classifier

    def retrieve(
        self,
        requirement_text: str,
        section_text: str = "",
        requirement_type: Optional[str] = None,
        requirement_id: str = "REQ_0001",
        top_k: int = 5,
    ) -> RetrievedEvidenceBundle:
        """
        Route and retrieve multi-source evidence for a single tender requirement.
        """
        req_type = requirement_type
        if not req_type or req_type not in REQUIREMENT_TYPES:
            if self.classifier is not None:
                pred_res = self.classifier.predict_single(
                    requirement_text=requirement_text,
                    section_text=section_text,
                )
                req_type = str(pred_res["label"])
            else:
                req_type = "Technical"

        handler_map: Dict[str, Callable[[str, str, int], tuple[str, Dict[str, Any], List[RetrievedChunkHit]]]] = {
            "Qualification": self._route_qualification,
            "Document": self._route_document,
            "Technical": self._route_technical,
            "Commercial": self._route_commercial,
            "Schedule": self._route_schedule,
            "Consistency": self._route_consistency,
            "Integrity": self._route_integrity,
            "Scoring": self._route_scoring,
        }
        handler = handler_map.get(req_type, self._route_technical)
        strategy_desc, ledger_ev, hits = handler(requirement_text, section_text, top_k)

        pages = sorted(
            {
                *{h.child_chunk.page_start for h in hits},
                *{h.child_chunk.page_end for h in hits},
                *ledger_ev.get("referenced_pages", []),
            }
        )

        return RetrievedEvidenceBundle(
            requirement_id=requirement_id,
            requirement_text=requirement_text,
            section_text=section_text,
            requirement_type=req_type,
            requirement_type_cn=LABEL_CN_NAMES.get(req_type, req_type),
            routing_strategy=strategy_desc,
            ledger_evidence=ledger_ev,
            chunk_hits=hits,
            evidence_pages=pages,
        )

    def _route_qualification(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        1. Qualification: Query Company Profile, Personnel Ledger, and Performance Ledger,
        plus hybrid chunk search filtered to qualification/certificate categories.
        """
        strategy = "EntityLedger(Company+Personnel+Performance) + Certificate/Table Hybrid Search"
        qual_cats = {
            "营业执照与企业资质证书",
            "安全生产许可证",
            "项目管理机构与人员简历证书",
            "社保证明材料",
            "近年类似工程业绩证明",
            "财务状况与审计报告",
        }
        hits = self.index.search(
            query=req_text,
            top_k=top_k,
            filter_fn=lambda c: c.doc_category in qual_cats or c.volume_type != "技术标",
        )
        ledger_ev = {
            "company_profile": self.ledger.company_profile,
            "personnel_ledger": self.ledger.personnel_ledger[:15],
            "performance_ledger": self.ledger.performance_ledger[:10],
            "referenced_pages": self.ledger.company_profile.get("evidence_pages", []),
        }
        return strategy, ledger_ev, hits

    def _route_document(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        2. Document: Check Document & Official Seal Registry for required forms,
        signatures, and corporate stamps, plus BM25-weighted search.
        """
        strategy = "DocumentSealRegistry Lookup + BM25-Weighted Form/Seal Search"
        wants_tech_doc = any(k in req_text for k in ("施工组织设计", "暗标", "技术文件"))
        hits = self.index.search(
            query=req_text,
            top_k=top_k,
            filter_fn=(
                None
                if wants_tech_doc
                else lambda c: c.volume_type != "技术标"
            ),
            dense_weight=0.35,
            sparse_weight=0.65,
        )
        seal_pages = [
            h.child_chunk.page_start for h in hits if h.child_chunk.has_official_seal
        ]
        ledger_ev = {
            "document_seal_registry": self.ledger.document_seal_registry,
            "matched_chunks_with_seal": sum(1 for h in hits if h.child_chunk.has_official_seal),
            "matched_chunks_with_signature": sum(1 for h in hits if h.child_chunk.has_signature),
            "referenced_pages": seal_pages,
        }
        return strategy, ledger_ev, hits

    def _route_technical(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        3. Technical: Filter to Technical Volume (`技术标` / `施工组织设计与技术方案`),
        plus Bid Letter / Subcontracting tables when querying quality/safety targets or subcontracting.
        """
        strategy = "VolumeFilter(技术标+投标函承诺) + Dense/BM25 Hybrid RRF + Reranker"
        wants_bid_letter_or_subcontract = any(
            k in req_text for k in ("质量评定", "安全目标", "分包", "实质性要求")
        )
        hits = self.index.search(
            query=req_text,
            top_k=top_k,
            filter_fn=lambda c: (
                c.volume_type == "技术标"
                or c.doc_category == "施工组织设计与技术方案"
                or (
                    wants_bid_letter_or_subcontract
                    and c.doc_category in {"投标函与承诺书", "其他投标材料"}
                )
            ),
            dense_weight=0.5,
            sparse_weight=0.5,
        )
        comm_ledger = self.ledger.commercial_schedule_ledger
        return (
            strategy,
            {
                "quality_target": comm_ledger.get("quality_target"),
                "referenced_pages": comm_ledger.get("evidence_pages", []),
            },
            hits,
        )

    def _route_commercial(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        4. Commercial: Query Commercial Summary Ledger (bid price, uppercase price,
        bid bond) + retrieve Bid Letter and Bill of Quantities chunks.
        """
        strategy = "CommercialScheduleLedger Lookup + BidLetter/BOQ Table Search"
        comm_cats = {"投标函与承诺书", "投标保证金证明", "报价文件与工程量清单"}
        hits = self.index.search(
            query=req_text,
            top_k=top_k,
            filter_fn=lambda c: c.doc_category in comm_cats or c.volume_type == "报价标",
        )
        comm_ledger = self.ledger.commercial_schedule_ledger
        ledger_ev = {
            "commercial_summary": comm_ledger,
            "referenced_pages": comm_ledger.get("evidence_pages", []),
        }
        return strategy, ledger_ev, hits

    def _route_schedule(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        5. Schedule: Query Duration & Validity days from Commercial/Schedule Ledger +
        retrieve Bid Letter and Construction Schedule Plan chunks.
        """
        strategy = "ScheduleLedger(duration_days/validity_days) + BidLetter/ProgressPlan Search"
        hits = self.index.search(
            query=f"工期 日历天 投标有效期 进度计划 {req_text}",
            top_k=top_k,
            filter_fn=lambda c: (
                c.doc_category == "投标函与承诺书"
                or "进度" in c.section_path
                or "工期" in c.text
                or "有效期" in c.text
            ),
        )
        comm_ledger = self.ledger.commercial_schedule_ledger
        ledger_ev = {
            "duration_days": comm_ledger.get("duration_days"),
            "validity_days": comm_ledger.get("validity_days"),
            "referenced_pages": comm_ledger.get("evidence_pages", []),
        }
        return strategy, ledger_ev, hits

    def _route_consistency(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        6. Consistency: Cross-Document Entity Aggregation across all occurrences of
        `project_manager_name`, `bid_price_yuan`, `bid_price_upper`, and `duration_days`.
        """
        strategy = "Cross-Document Entity Mention Aggregation (All-Equal Check) + Hybrid Search"
        consistency_checks = {
            "project_manager_name": self.ledger.check_consistency("project_manager_name"),
            "bid_price_yuan": self.ledger.check_consistency("bid_price_yuan"),
            "bid_price_upper": self.ledger.check_consistency("bid_price_upper"),
            "duration_days": self.ledger.check_consistency("duration_days"),
        }
        ref_pages: List[int] = []
        for check in consistency_checks.values():
            for m in check.get("mentions", []):
                pno = m.get("page_no")
                if isinstance(pno, int) and pno not in ref_pages:
                    ref_pages.append(pno)

        hits = self.index.search(
            query=req_text,
            top_k=top_k,
            filter_fn=lambda c: c.volume_type != "技术标",
        )
        ledger_ev = {
            "cross_page_consistency_report": consistency_checks,
            "company_profile": self.ledger.company_profile,
            "commercial_summary": self.ledger.commercial_schedule_ledger,
            "referenced_pages": ref_pages,
        }
        return strategy, ledger_ev, hits

    def _route_integrity(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        7. Integrity: Retrieve non-collusion / authenticity commitment letters and
        cross-check corporate seal identity against bidder profile.
        """
        strategy = "Integrity Commitment Retrieval + Corporate Seal Identity Verification"
        hits = self.index.search(
            query=f"投标诚信承诺书 承诺函 弄虚作假 串通投标 信誉情况表 {req_text}",
            top_k=top_k,
            filter_fn=lambda c: c.doc_category
            in {"投标函与承诺书", "企业信誉与无违法承诺", "营业执照与企业资质证书"},
        )
        ledger_ev = {
            "company_profile": self.ledger.company_profile,
            "referenced_pages": self.ledger.company_profile.get("evidence_pages", []),
        }
        return strategy, ledger_ev, hits

    def _route_scoring(
        self, req_text: str, sec_text: str, top_k: int
    ) -> tuple[str, Dict[str, Any], List[RetrievedChunkHit]]:
        """
        8. Scoring: Multi-source retrieval combining Performance/Personnel Ledgers
        with targeted hybrid search across Technical or Qualification/Financial volumes.
        """
        strategy = "Multi-Hop Scoring Evidence Retrieval (Performance/Awards + Technical Plan)"
        wants_tech_plan = any(
            k in req_text
            for k in (
                "施工组织",
                "施工方案",
                "保证体系",
                "管理体系",
                "技术措施",
                "环保",
                "文明施工",
                "风险",
                "资源计划",
            )
        )
        hits = self.index.search(
            query=req_text,
            top_k=max(top_k, 5),
            filter_fn=(
                (lambda c: c.volume_type == "技术标")
                if wants_tech_plan
                else (lambda c: c.volume_type != "技术标")
            ),
            dense_weight=0.55,
            sparse_weight=0.45,
        )
        ledger_ev = {
            "performance_count": len(self.ledger.performance_ledger),
            "performance_ledger": self.ledger.performance_ledger[:10],
            "personnel_ledger": self.ledger.personnel_ledger[:10],
            "referenced_pages": [],
        }
        return strategy, ledger_ev, hits

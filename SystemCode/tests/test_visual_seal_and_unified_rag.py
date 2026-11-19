"""
Unit tests for Phase 2.3:
1. Multimodal visual seal & signature evidence chain (`parser -> retriever -> evidence -> ground_extraction -> R04 -> UI card`).
2. Strict zero-false-pass when only text placeholders exist (no visual verification).
3. Unified RAG execution trunk (`chinese_pipeline` delegating directly to `src/rag/parser.py` & `src/rag/indexer.py`).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf

from src.app.tcui.real_backend import _build_fields_comparison
from src.extraction.chinese_pipeline import FastChineseBidRetriever, parse_bid_pdfs_for_rag
from src.extraction.evidence import evidence_context, ground_extraction
from src.extraction.schema import BidEvidenceSlots, StructuredExtractionResult, TenderConstraintSlots
from src.rag.indexer import DEFAULT_EMBEDDING_MODEL, HybridBidIndex, resolve_local_or_hf_model
from src.rag.vision import BaseVisionBackend, PageVisionResult
from src.rules.rule_engine import ComplianceRuleEngine


class _StubVLMBackend(BaseVisionBackend):
    """Deterministic stub simulating Qwen2.5-VL page-level seal & signature inspection."""

    def __init__(
        self,
        *,
        has_seal: bool,
        seal_company: str | None,
        has_sig: bool,
        backend_name: str = "qwen_vl_transformers",
    ) -> None:
        self.has_seal = has_seal
        self.seal_company = seal_company
        self.has_sig = has_sig
        self.backend_name = backend_name
        self.calls: list[int] = []

    def analyze_page(
        self,
        doc,
        pdf_page: int,
        existing_text: str = "",
        heading_hint: str = "",
    ) -> PageVisionResult:
        self.calls.append(pdf_page)
        return PageVisionResult(
            pdf_page=pdf_page,
            ocr_text=existing_text,
            doc_category="投标函与诚信承诺",
            has_official_seal=self.has_seal,
            seal_company_name=self.seal_company,
            has_signature=self.has_sig,
            backend_name=self.backend_name,
        )


def _write_simple_pdf(path: Path, pages_text: list[str]) -> Path:
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 80), text, fontsize=11, fontname="china-ss")
    doc.save(str(path))
    doc.close()
    return path


class VisualSealAndUnifiedRAGTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ComplianceRuleEngine()

    def test_verified_visual_seal_flows_end_to_end_to_r04_and_ui_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = _write_simple_pdf(
                Path(tmp) / "bid.pdf",
                [
                    "投标函\n致：某市水利局\n我方江苏水利建设集团有限公司承诺完全响应招标文件全部实质性要求，投标总报价为48500000元。\n投标人：江苏水利建设集团有限公司（盖单位章）\n法定代表人：张明（签字）",
                ],
            )
            stub_vlm = _StubVLMBackend(
                has_seal=True,
                seal_company="江苏水利建设集团有限公司",
                has_sig=True,
            )
            children, parent_map, doc_sources = parse_bid_pdfs_for_rag(
                [{"doc_id": "bid_1", "path": str(pdf_path), "label": "商务标.pdf"}],
                vision_backend=stub_vlm,
            )
            self.assertEqual(stub_vlm.calls, [1])
            self.assertTrue(any(p.visual_seal_verified for p in parent_map.values()))

            retriever = FastChineseBidRetriever(children, parent_map, doc_sources, use_reranker=False)
            self.assertIsInstance(retriever.hybrid_index, HybridBidIndex)
            passages = retriever.retrieve_passages("投标函须加盖投标人单位公章并由法定代表人签字", "Document", top_k=3)
            self.assertTrue(passages)
            self.assertTrue(passages[0]["visual_seal_verified"])
            self.assertEqual(passages[0]["seal_company_name"], "江苏水利建设集团有限公司")

            ctx = evidence_context(passages)
            ext = StructuredExtractionResult(
                clause_id="T_SEAL_01",
                requirement_text="投标函须加盖投标人单位公章并由法定代表人签字。",
                category="Document",
                tender_slots=TenderConstraintSlots(
                    constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                    target_field="bid_letter",
                    operator="PRESENT",
                    requires_official_seal=True,
                    requires_signature=True,
                    source_quotes={
                        "requires_official_seal": "加盖投标人单位公章",
                        "requires_signature": "由法定代表人签字",
                    },
                ),
                bid_slots=BidEvidenceSlots(
                    evidence_found=True,
                    document_present=True,
                    entity_primary="江苏水利建设集团有限公司",
                    evidence_references=[
                        {
                            "field": "document_present",
                            "evidence_id": "E1",
                            "quote": "我方江苏水利建设集团有限公司承诺完全响应招标文件全部实质性要求，投标总报价为48500000元。",
                        },
                        {
                            "field": "entity_primary",
                            "evidence_id": "E1",
                            "quote": "投标人：江苏水利建设集团有限公司",
                        },
                    ],
                ),
            )
            grounded = ground_extraction(ext, ctx)
            self.assertEqual(grounded.extraction_issues, [])
            self.assertTrue(grounded.bid_slots.visual_seal_verified)
            self.assertTrue(grounded.bid_slots.has_official_seal)
            self.assertTrue(grounded.bid_slots.has_signature)
            self.assertEqual(grounded.bid_slots.seal_company_name, "江苏水利建设集团有限公司")
            self.assertEqual(grounded.bid_slots.visual_seal_backend, "qwen_vl_transformers")

            res = self.engine.evaluate(grounded)
            self.assertEqual(res.verdict, "COMPLIANT")
            self.assertIn("qwen_vl_transformers", res.symbolic_trace)

            rows = _build_fields_comparison(grounded.to_dict(), res.to_dict(), "Compliant", "COMPLIANT")
            self.assertTrue(any(r["field"] == "多模态印章与签字核验" for r in rows))
            vis_row = next(r for r in rows if r["field"] == "多模态印章与签字核验")
            self.assertIn("公章: 已加盖", vis_row["actual"])
            self.assertIn("江苏水利建设集团有限公司", vis_row["actual"])
            self.assertIn("签字: 已签字", vis_row["actual"])

    def test_visual_seal_missing_and_company_mismatch_are_rejected_by_r04(self) -> None:
        # Case 1: VLM confirms no seal on required-seal page -> NON_COMPLIANT
        passages_no_seal = [
            {
                "document_id": "bid_1",
                "source_sha256": "abc",
                "page_start": 2,
                "page_end": 2,
                "section_path": "投标函",
                "parent_text": "投标函：我方承诺完全响应招标文件全部实质性要求，保证按期完工。",
                "visual_seal_verified": True,
                "visual_backend": "qwen_vl_transformers",
                "has_official_seal": False,
                "seal_company_name": None,
                "has_signature": True,
            }
        ]
        ext_no_seal = StructuredExtractionResult(
            clause_id="T_SEAL_02",
            requirement_text="投标函须加盖投标人单位公章。",
            category="Document",
            tender_slots=TenderConstraintSlots(
                constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                operator="PRESENT",
                requires_official_seal=True,
                source_quotes={"requires_official_seal": "加盖投标人单位公章"},
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                document_present=True,
                evidence_references=[
                    {
                        "field": "document_present",
                        "evidence_id": "E1",
                        "quote": "投标函：我方承诺完全响应招标文件全部实质性要求，保证按期完工。",
                    }
                ],
            ),
        )
        grounded_no_seal = ground_extraction(ext_no_seal, evidence_context(passages_no_seal))
        res_no_seal = self.engine.evaluate(grounded_no_seal)
        self.assertEqual(res_no_seal.verdict, "NON_COMPLIANT")
        self.assertIn("未加盖单位公章", res_no_seal.symbolic_trace)

        # Case 2: VLM detects seal from a different company than the bidder -> NON_COMPLIANT
        passages_mismatch = [
            {
                "document_id": "bid_1",
                "source_sha256": "abc",
                "page_start": 2,
                "page_end": 2,
                "section_path": "投标函",
                "parent_text": "投标函：我方江苏水利建设集团有限公司承诺完全响应招标文件全部实质性要求。",
                "visual_seal_verified": True,
                "visual_backend": "qwen_vl_transformers",
                "has_official_seal": True,
                "seal_company_name": "某第三方劳务分包有限公司",
                "has_signature": True,
            }
        ]
        ext_mismatch = StructuredExtractionResult(
            clause_id="T_SEAL_03",
            requirement_text="投标函须加盖投标人单位公章。",
            category="Document",
            tender_slots=TenderConstraintSlots(
                constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                operator="PRESENT",
                requires_official_seal=True,
                source_quotes={"requires_official_seal": "加盖投标人单位公章"},
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                document_present=True,
                entity_primary="江苏水利建设集团有限公司",
                evidence_references=[
                    {
                        "field": "document_present",
                        "evidence_id": "E1",
                        "quote": "投标函：我方江苏水利建设集团有限公司承诺完全响应招标文件全部实质性要求。",
                    },
                    {
                        "field": "entity_primary",
                        "evidence_id": "E1",
                        "quote": "江苏水利建设集团有限公司",
                    },
                ],
            ),
        )
        grounded_mismatch = ground_extraction(ext_mismatch, evidence_context(passages_mismatch))
        res_mismatch = self.engine.evaluate(grounded_mismatch)
        self.assertEqual(res_mismatch.verdict, "NON_COMPLIANT")
        self.assertIn("不一致", res_mismatch.symbolic_trace)

    def test_text_only_placeholders_never_produce_false_seal_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Plain narrative page containing placeholder text "盖单位章" / "签字" (not a vision-verified page)
            pdf_path = _write_simple_pdf(
                Path(tmp) / "narrative.pdf",
                [
                    "施工组织设计总说明\n本工程严格按照国家规范组织施工，编制完整的质量控制方案与进度保障措施，按期交付。\n编制单位：（盖单位章）\n法定代表人：（签字）",
                ],
            )
            stub_vlm = _StubVLMBackend(has_seal=True, seal_company="不应触发", has_sig=True)
            children, parent_map, doc_sources = parse_bid_pdfs_for_rag(
                [{"doc_id": "bid_nar", "path": str(pdf_path), "label": "技术标.pdf"}],
                vision_backend=stub_vlm,
            )
            # Plain narrative page with >160 chars or non-seal-trigger text does not fake visual_seal_verified
            for chunk in parent_map.values():
                if not chunk.visual_seal_verified:
                    self.assertFalse(chunk.has_official_seal)
                    self.assertFalse(chunk.has_signature)

        # Even if LLM attempts to guess has_official_seal=True on unverified text passage, ground_extraction clears it
        text_only_passages = [
            {
                "document_id": "bid_nar",
                "source_sha256": "def",
                "page_start": 1,
                "page_end": 1,
                "section_path": "施工组织设计",
                "parent_text": "施工组织设计方案：本工程严格执行国家质量标准。（盖单位章）（签字）",
                "visual_seal_verified": False,
                "has_official_seal": True,  # malicious/legacy unverified flag must be stripped!
                "has_signature": True,
            }
        ]
        ext = StructuredExtractionResult(
            clause_id="T_SEAL_04",
            requirement_text="施工组织设计封面须加盖单位公章并签字。",
            category="Document",
            tender_slots=TenderConstraintSlots(
                constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                operator="PRESENT",
                requires_official_seal=True,
                requires_signature=True,
                source_quotes={
                    "requires_official_seal": "加盖单位公章",
                    "requires_signature": "签字",
                },
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                document_present=True,
                has_official_seal=True,
                has_signature=True,
                evidence_references=[
                    {
                        "field": "document_present",
                        "evidence_id": "E1",
                        "quote": "施工组织设计方案：本工程严格执行国家质量标准。",
                    }
                ],
            ),
        )
        grounded = ground_extraction(ext, evidence_context(text_only_passages))
        self.assertFalse(grounded.bid_slots.visual_seal_verified)
        self.assertIsNone(grounded.bid_slots.has_official_seal)
        self.assertIsNone(grounded.bid_slots.has_signature)

        res = self.engine.evaluate(grounded)
        self.assertEqual(res.verdict, "INSUFFICIENT_EVIDENCE")

    def test_resolve_local_or_hf_model_helper(self) -> None:
        resolved = resolve_local_or_hf_model("nonexistent-model-dir-xyz", DEFAULT_EMBEDDING_MODEL)
        self.assertEqual(resolved, DEFAULT_EMBEDDING_MODEL)


if __name__ == "__main__":
    unittest.main()

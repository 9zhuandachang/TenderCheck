"""Unit tests for Stage 3 (`src/extraction`) and Stage 4 (`src/rules`) Neuro-Symbolic Compliance Verification."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.extraction import (
    BidEvidenceSlots,
    HeuristicSlotExtractor,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules import (
    ComplianceRuleEngine,
    number_to_rmb_upper,
    parse_qualification_rank,
)


class ComplianceRulesTests(unittest.TestCase):
    def setUp(self):
        self.engine = ComplianceRuleEngine()
        self.heuristic_extractor = HeuristicSlotExtractor()

    def test_rmb_uppercase_conversion_exact(self):
        self.assertEqual(
            number_to_rmb_upper(97984949.58),
            "玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分",
        )
        self.assertEqual(number_to_rmb_upper(115046243.64), "壹亿壹仟伍佰零肆万陆仟贰佰肆拾叁元陆角肆分")
        self.assertEqual(number_to_rmb_upper(8000000.00), "捌佰万元整")
        self.assertEqual(number_to_rmb_upper(100.0), "壹佰元整")

    def test_r01_numeric_bound_pass_and_fail(self):
        # Compliant bid price <= control price + matching provisional sum
        ext_ok = StructuredExtractionResult(
            clause_id="T0002",
            requirement_text="项目招标控制价115046243.64元，含暂列金8000000.00元。",
            category="Commercial",
            tender_slots=TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="bid_price_yuan",
                operator="<=",
                expected_numeric=115046243.64,
                expected_unit="元",
                secondary_numeric=8000000.00,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_numeric=97984949.58,
                actual_unit="元",
                secondary_actual_numeric=8000000.00,
                evidence_pages=[565, 569],
            ),
        )
        rep_ok = self.engine.evaluate(ext_ok)
        self.assertEqual(rep_ok.verdict, "COMPLIANT")
        self.assertEqual(rep_ok.triggered_rule_id, "R01_NUMERIC_BOUND")

        # Non-compliant bid price > control price
        ext_fail = StructuredExtractionResult.from_dict(ext_ok.to_dict())
        ext_fail.bid_slots.actual_numeric = 116000000.00
        rep_fail = self.engine.evaluate(ext_fail)
        self.assertEqual(rep_fail.verdict, "NON_COMPLIANT")

    def test_r02_qualification_hierarchy_partial_order(self):
        # 特级 (rank 4) >= 三级 (rank 1) in 公路工程 -> COMPLIANT
        ext_ok = StructuredExtractionResult(
            clause_id="T0004",
            requirement_text="投标人须具备公路工程施工总承包三级及以上资质。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                target_field="qualification_level",
                operator="LEVEL_GE",
                expected_text="公路工程施工总承包三级",
                required_domain="公路工程",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="公路工程施工总承包特级",
                actual_domain="公路工程",
                certificate_valid=True,
                evidence_pages=[42],
            ),
        )
        self.assertEqual(self.engine.evaluate(ext_ok).verdict, "COMPLIANT")

        # Domain mismatch: 市政公用工程特级 vs 公路工程三级 -> NON_COMPLIANT
        ext_wrong_domain = StructuredExtractionResult.from_dict(ext_ok.to_dict())
        ext_wrong_domain.bid_slots.actual_text = "市政公用工程施工总承包特级"
        ext_wrong_domain.bid_slots.actual_domain = "市政公用工程"
        self.assertEqual(self.engine.evaluate(ext_wrong_domain).verdict, "NON_COMPLIANT")

    def test_r03_rmb_upper_and_entity_consistency(self):
        # Exact match between 97984949.58 and 玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分
        ext_ok = StructuredExtractionResult(
            clause_id="T0127",
            requirement_text="采用固化清单时，已标价清单投标报价须与投标函大写报价一致。",
            category="Consistency",
            tender_slots=TenderConstraintSlots(
                constraint_type="ENTITY_CONSISTENCY",
                target_field="rmb_upper_consistency",
                operator="CONSISTENT",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_numeric=97984949.58,
                upper_amount_text="玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分",
                entity_primary="97984949.58",
                entity_secondary="97984949.58",
                evidence_pages=[565, 569],
            ),
        )
        self.assertEqual(self.engine.evaluate(ext_ok).verdict, "COMPLIANT")

        # Subtle 8-cent discrepancy in uppercase text -> caught deterministically by R03!
        ext_mismatch = StructuredExtractionResult.from_dict(ext_ok.to_dict())
        ext_mismatch.bid_slots.upper_amount_text = "玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角整"
        self.assertEqual(self.engine.evaluate(ext_mismatch).verdict, "NON_COMPLIANT")

    def test_r04_document_and_seal_verification(self):
        ext_missing_seal = StructuredExtractionResult(
            clause_id="T0014",
            requirement_text="项目经理业绩须提供交工或竣工验收证明，须有建设、设计、监理、施工单位等盖章确认。",
            category="Document",
            tender_slots=TenderConstraintSlots(
                constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
                target_field="document_presence",
                operator="PRESENT",
                requires_official_seal=True,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                has_official_seal=False,
                evidence_pages=[220],
            ),
        )
        self.assertEqual(self.engine.evaluate(ext_missing_seal).verdict, "NON_COMPLIANT")

    def test_r06_scoring_bracket_calculation(self):
        ext_score = StructuredExtractionResult(
            clause_id="T0214",
            requirement_text="资产负债率75%以下得6分，75%（含）—80%（含）得4分，80%—85%（含）得2分，85%以上不得分。",
            category="Scoring",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALITATIVE_OR_SCORING",
                target_field="debt_ratio_score",
                operator="SCORE_BRACKET",
                scoring_max_points=6.0,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_numeric=68.4,
                actual_unit="%",
                qualitative_match=True,
                evidence_pages=[150],
            ),
        )
        rep = self.engine.evaluate(ext_score)
        self.assertEqual(rep.verdict, "COMPLIANT")
        self.assertEqual(rep.awarded_score, 6.0)


if __name__ == "__main__":
    unittest.main()

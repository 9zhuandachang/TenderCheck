"""Cross-domain generalization tests (Phase 2.1): Water Conservancy, Municipal EPC, Jiangsu/Anhui 2024-2026 rules, and Consortium routing."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SYSTEMCODE_DIR = Path(__file__).resolve().parents[1]
if str(SYSTEMCODE_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMCODE_DIR))

from src.extraction.evidence import evidence_context, ground_extraction
from src.extraction.global_context import (
    build_project_global_context,
    hydrate_with_global_context,
)
from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules import ComplianceRuleEngine


def _ctx(*texts: str) -> str:
    return evidence_context(
        [
            {
                "document_id": f"doc_{i}",
                "source_sha256": f"{i:064x}",
                "page_start": i,
                "page_end": i,
                "parent_text": txt,
            }
            for i, txt in enumerate(texts, 1)
        ]
    )


class DomainGeneralizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ComplianceRuleEngine()

    def test_cross_sector_enterprise_qualification_mismatch_rejected(self) -> None:
        """Highway Grade-1 bidder MUST NOT pass a Water Conservancy Grade-3 tender requirement."""
        req = "投标人须具有水利水电工程施工总承包叁级及以上资质。"
        wrong_sector = StructuredExtractionResult(
            clause_id="W0003",
            requirement_text=req,
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="水利水电工程施工总承包叁级",
                required_role="bidder",
                review_reference_date="2026-04-10",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="公路工程施工总承包壹级",
                subject_role="bidder",
                valid_to="2028-12-31",
            ),
        )
        self.assertEqual(self.engine.evaluate(wrong_sector).verdict, "NON_COMPLIANT")

        right_sector = StructuredExtractionResult(
            clause_id="W0003",
            requirement_text=req,
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="水利水电工程施工总承包叁级",
                required_role="bidder",
                review_reference_date="2026-04-10",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="水利水电工程施工总承包壹级",
                subject_role="bidder",
                valid_to="2028-12-31",
            ),
        )
        self.assertEqual(self.engine.evaluate(right_sector).verdict, "COMPLIANT")

    def test_water_conservancy_and_jiangsu_safety_abc_and_site_officer_certs(self) -> None:
        # 1. Enterprise leader Safety A-cert (水安A / 苏建安A)
        req_a = "企业主要负责人须持有效A类安全生产考核合格证书。"
        res_a = StructuredExtractionResult(
            clause_id="W0037",
            requirement_text=req_a,
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="A类安全生产考核合格证书",
                required_role="企业主要负责人",
                review_reference_date="2026-04-10",
                requires_validity_date=True,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="水安A(2024)00128号安全生产考核合格证书",
                subject_role="法定代表人",
                valid_to="2027-06-30",
            ),
        )
        self.assertEqual(self.engine.evaluate(res_a).verdict, "COMPLIANT")

        # 2. Staffing R07: 2 Quality Officers with 质检员岗位证书 & Water/Municipal Technical Leader title
        res_q = StructuredExtractionResult(
            clause_id="W0042",
            requirement_text="拟派专职质检员须为2人，均持质检员岗位证书。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
                required_role="专职质检员",
                required_count=2,
                expected_text="质检员岗位证书",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                personnel_records=[
                    {"role": "质检员", "name": "赵一", "qualification_text": "质检员岗位证书"},
                    {"role": "专职质检员", "name": "钱二", "qualification_text": "水利质检员岗位证书"},
                ],
            ),
        )
        self.assertEqual(self.engine.evaluate(res_q).verdict, "COMPLIANT")

        # 3. Water Conservancy related major matching (农田水利 / 水工结构 -> 水利水电相关专业)
        res_ce = StructuredExtractionResult(
            clause_id="W0035",
            requirement_text="拟派技术负责人须具有水利水电相关专业高级工程师职称。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="高级工程师",
                required_domain="水利水电相关专业",
                required_role="技术负责人",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="高级工程师",
                actual_domain="水工结构",
                subject_role="项目总工",
            ),
        )
        self.assertEqual(self.engine.evaluate(res_ce).verdict, "COMPLIANT")

        # 4. Registered Cost Engineer rank check (二级 < 一级 -> NON_COMPLIANT)
        res_cost = StructuredExtractionResult(
            clause_id="W0117",
            requirement_text="造价人员须具有一级造价工程师注册证书。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="一级造价工程师注册证书",
                required_role="造价人员",
                review_reference_date="2026-04-10",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="二级水利造价工程师注册证书",
                subject_role="造价工程师",
                valid_to="2027-12-31",
            ),
        )
        self.assertEqual(self.engine.evaluate(res_cost).verdict, "NON_COMPLIANT")

    def test_parameterized_credit_scoring_2023_2025_and_water_single_grade(self) -> None:
        # 1. Dynamic 2023-2025 multi-year credit scoring (relative '近三年' resolved from 2026 review date)
        res_multi = StructuredExtractionResult(
            clause_id="C2026_01",
            requirement_text="近三年信用等级三年均AAA得6分，两年AAA得4分，一年AAA得2分，无AAA得0分。",
            category="Scoring",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALITATIVE_OR_SCORING",
                scoring_mode="CREDIT_MULTI_YEAR",
                scoring_max_points=6.0,
                review_reference_date="2026-05-01",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                credit_ratings=[
                    {"year": 2023, "provincial_grade": "AAA"},
                    {"year": 2024, "provincial_grade": "AA"},
                    {"year": 2025, "provincial_grade": "AAA"},
                ],
            ),
        )
        rep_multi = self.engine.evaluate(res_multi)
        self.assertEqual(rep_multi.verdict, "SCORED")
        self.assertEqual(rep_multi.awarded_score, 4.0)

        # 2. Water Conservancy single-grade credit scoring (T0110: AAA->3, AA->2, A->1)
        res_single = StructuredExtractionResult(
            clause_id="W0110",
            requirement_text="水利建设市场信用等级AAA得3分、AA得2分、A得1分。",
            category="Scoring",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALITATIVE_OR_SCORING",
                scoring_mode="CREDIT_SINGLE_GRADE",
                scoring_max_points=3.0,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                credit_ratings=[{"year": 2025, "national_grade": "AA"}],
            ),
        )
        rep_single = self.engine.evaluate(res_single)
        self.assertEqual(rep_single.verdict, "SCORED")
        self.assertEqual(rep_single.awarded_score, 2.0)

    def test_interchangeable_pm_or_technical_leader_performance_in_r08(self) -> None:
        res = StructuredExtractionResult(
            clause_id="W0113",
            requirement_text="技术负责人有1项合同价不低于7000万元且担任技术负责人或项目经理的水利工程施工业绩得1分。",
            category="Scoring",
            tender_slots=TenderConstraintSlots(
                constraint_type="PERFORMANCE_CHAIN_AND_SCORING",
                required_role="技术负责人",
                min_amount_wanyuan=7000.0,
                min_project_count=1,
                scoring_mode="SINGLE_PROJECT_BONUS",
                scoring_max_points=1.0,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                proposed_person_name="孙工",
                performance_records=[
                    {
                        "project_name": "滁河防洪治理工程二标",
                        "person_name": "孙工",
                        "performed_role": "项目经理",
                        "contract_amount_wanyuan": 8200.0,
                    }
                ],
            ),
        )
        rep = self.engine.evaluate(res)
        self.assertEqual(rep.verdict, "SCORED")
        self.assertEqual(rep.awarded_score, 1.0)

    def test_specific_commitment_not_falsely_passed_by_generic_bid_letter(self) -> None:
        generic_letter = {
            "evidence_id": "E1",
            "document_id": "bid_1",
            "source_sha256": "a" * 64,
            "page_start": 2,
            "page_end": 2,
            "text": "投标函：我方已仔细研究招标文件全部内容，愿意按合同约定实施和完成承包工程，工期：150日历天。",
        }
        ev_str = json.dumps([generic_letter], ensure_ascii=False)
        gctx = build_project_global_context(
            [{"clause_id": "W0038", "requirement": "计划工期为150日历天。", "category": "Schedule", "evidence_context": ev_str}]
        )

        # Specific prohibition ("不得在全省在建水利项目担任项目经理") without specific evidence MUST abstain!
        specific_res = StructuredExtractionResult(
            clause_id="W0038",
            requirement_text="全省在建水利项目现场人员名单公示范围内的人员不得在本项目担任项目经理。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="PROHIBITION_AND_COMMITMENT",
                operator="NOT_VIOLATED",
                expected_text="全省在建水利项目现场人员名单公示范围内的人员不得在本项目担任项目经理。",
                source_quotes={"expected_text": "全省在建水利项目现场人员名单公示范围内的人员不得在本项目担任项目经理。"},
            ),
            bid_slots=BidEvidenceSlots(evidence_found=False),
        )
        specific_res = ground_extraction(specific_res, ev_str)
        specific_res, _ = hydrate_with_global_context(specific_res, ev_str, gctx)
        self.assertEqual(self.engine.evaluate(specific_res).verdict, "INSUFFICIENT_EVIDENCE")

    def test_consortium_detection_rejects_prohibited_and_routes_allowed_to_human_review(self) -> None:
        # 1. Independent bidder '联合体协议书：无。' MUST NOT trigger consortium_detected
        non_consortium_ctx = _ctx("三、联合体协议书\n无。\n商务文件", "投标人名称：安徽某水利工程有限公司\n水利水电工程施工总承包壹级")
        ind_res = StructuredExtractionResult(
            clause_id="W0003",
            requirement_text="投标人须具有水利水电工程施工总承包壹级及以上资质。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="水利水电工程施工总承包壹级",
                source_quotes={"expected_text": "水利水电工程施工总承包壹级"},
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="水利水电工程施工总承包壹级",
                evidence_references=[{"field": "actual_text", "evidence_id": "E2", "quote": "水利水电工程施工总承包壹级"}],
                subject_bindings=[
                    {
                        "field": "actual_text",
                        "evidence_id": "E2",
                        "subject_name": "安徽某水利工程有限公司",
                        "role": "bidder",
                        "quote": "投标人名称：安徽某水利工程有限公司\n水利水电工程施工总承包壹级",
                        "role_evidence_id": "E2",
                        "role_quote": "投标人名称：安徽某水利工程有限公司",
                    }
                ],
            ),
        )
        ind_res = ground_extraction(ind_res, non_consortium_ctx)
        self.assertFalse(ind_res.bid_slots.consortium_detected)
        self.assertEqual(self.engine.evaluate(ind_res).verdict, "COMPLIANT")

        # 2. Substantive Consortium Agreement:
        #    - Fails '本项目不接受联合体投标' (NON_COMPLIANT)
        #    - Routes subject-bound R02 qualification to human review (INSUFFICIENT_EVIDENCE)
        real_consortium_ctx = _ctx(
            "联合体协议书\n甲乙双方自愿组成联合体共同参加本项目投标。\n联合体牵头人：甲水利建设集团有限公司\n联合体成员：乙市政设计研究院有限公司",
            "投标人名称：甲水利建设集团有限公司\n水利水电工程施工总承包壹级",
        )
        prohibit_res = StructuredExtractionResult(
            clause_id="W0010",
            requirement_text="本项目不接受联合体投标。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="PROHIBITION_AND_COMMITMENT",
                operator="NOT_VIOLATED",
                expected_text="本项目不接受联合体投标。",
                source_quotes={"expected_text": "本项目不接受联合体投标。"},
            ),
            bid_slots=BidEvidenceSlots(evidence_found=False),
        )
        prohibit_res = ground_extraction(prohibit_res, real_consortium_ctx)
        self.assertTrue(prohibit_res.bid_slots.consortium_detected)
        self.assertEqual(self.engine.evaluate(prohibit_res).verdict, "NON_COMPLIANT")

        consortium_r02 = ground_extraction(ind_res, real_consortium_ctx)
        self.assertTrue(consortium_r02.bid_slots.consortium_detected)
        rep_r02 = self.engine.evaluate(consortium_r02)
        self.assertEqual(rep_r02.verdict, "INSUFFICIENT_EVIDENCE")
        self.assertIn("联合体", rep_r02.explanation)


if __name__ == "__main__":
    unittest.main()

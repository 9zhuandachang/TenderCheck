"""
Unit tests for Steps 1, 2, and 3:
  - S604 359-clause rule inventory mapping (`s604_clause_rule_mapping.json`)
  - Extended rule input contracts & schema validation (`src/extraction/schema.py`)
  - Certificate timeliness, multi-person staffing (`R07`), performance chain & change approval (`R08`),
    and net-base formula calculation (`R09`) in `src/rules/rule_engine.py`
  - Standalone rule evaluation runner and diagnostic buckets (`src/evaluation/rule_eval.py`)
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.rule_eval import evaluate_rule_suite
from src.extraction.evidence import evidence_context, ground_extraction
from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules.rule_engine import ComplianceRuleEngine


class RuleContractsAndSuiteTests(unittest.TestCase):
    def setUp(self):
        self.base_dir = Path(__file__).resolve().parents[1]
        self.suite_path = self.base_dir / "data" / "rule_eval" / "s604_rule_validation_suite.json"
        self.mapping_path = self.base_dir / "data" / "rule_eval" / "s604_clause_rule_mapping.json"
        self.holdout_path = self.base_dir / "data" / "holdout_eval" / "2022__11.11annotations.json"
        self.engine = ComplianceRuleEngine()

    def test_step1_359_clause_mapping_completeness(self):
        mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        holdout = json.loads(self.holdout_path.read_text(encoding="utf-8"))
        self.assertEqual(mapping["total_clauses"], 359)
        self.assertEqual(len(mapping["clauses"]), len(holdout))
        mapped_ids = [c["clause_id"] for c in mapping["clauses"]]
        holdout_ids = [r["ID"] for r in holdout]
        self.assertEqual(mapped_ids, holdout_ids)
        self.assertEqual(
            mapping["formalizable_clauses"] + mapping["human_review_clauses"],
            359,
        )
        # All 50 RAG benchmark clauses are flagged in the mapping
        self.assertEqual(sum(1 for c in mapping["clauses"] if c["in_s604_50_subset"]), 50)

    def test_step2_extended_schema_type_validation(self):
        with self.assertRaises(ValueError):
            TenderConstraintSlots.from_dict({"requires_validity_date": "true"})
        with self.assertRaises(ValueError):
            TenderConstraintSlots.from_dict({"required_count": "2"})
        with self.assertRaises(ValueError):
            BidEvidenceSlots.from_dict({"personnel_records": "not_a_list"})
        with self.assertRaises(ValueError):
            BidEvidenceSlots.from_dict({"component_amounts": {"safety_fee": "not_a_number"}})

    def test_step3_standalone_validation_suite_passes_100_percent(self):
        suite = json.loads(self.suite_path.read_text(encoding="utf-8"))
        report = evaluate_rule_suite(suite)
        self.assertEqual(report["total_cases"], 69)
        self.assertEqual(report["total_passed"], 69)
        self.assertEqual(report["overall_pass_rate"], 1.0)
        buckets = report["diagnostic_buckets"]
        self.assertEqual(buckets["false_pass_count"], 0)
        self.assertEqual(buckets["false_reject_count"], 0)
        self.assertEqual(buckets["unjustified_abstain_count"], 0)
        self.assertEqual(buckets["score_error_count"], 0)
        self.assertEqual(buckets["justified_abstain_count"], 17)

    def test_step3_diagnostic_buckets_catch_false_pass_reject_and_score_error(self):
        suite = json.loads(self.suite_path.read_text(encoding="utf-8"))
        # Construct a mini suite where expected labels differ from engine outputs to verify bucket counting
        by_id = {c["case_id"]: c for c in suite["cases"]}
        mini = {
            "schema_version": 1,
            "evaluation_scope": "standalone_rule_engine_verification",
            "cases": [
                # Engine outputs COMPLIANT, if expected was NON_COMPLIANT -> false_pass
                dict(copy.deepcopy(by_id["R01_POS_01"]), case_id="FP_TEST", expected_verdict="NON_COMPLIANT"),
                # Engine outputs NON_COMPLIANT, if expected was COMPLIANT -> false_reject
                dict(copy.deepcopy(by_id["R01_NEG_01"]), case_id="FR_TEST", expected_verdict="COMPLIANT"),
                # Engine outputs SCORED 6.0, if expected was 4.0 -> score_error
                dict(copy.deepcopy(by_id["R06_POS_01_DEBT_6PTS"]), case_id="SE_TEST", expected_score=4.0),
            ],
        }
        res = evaluate_rule_suite(mini)
        b = res["diagnostic_buckets"]
        self.assertEqual(b["false_pass_cases"], ["FP_TEST"])
        self.assertEqual(b["false_reject_cases"], ["FR_TEST"])
        self.assertEqual(b["score_error_cases"], ["SE_TEST"])

    def test_t0004_self_declared_basic_info_table_blocks_false_pass(self):
        req = "投标人须具备公路工程施工总承包三级及以上资质。"
        table_passage = "（一）投标人基本情况表\n投标人名称 | 安徽水利开发有限公司\n企业名称：安徽水利开发有限公司\n资质等级：公路工程施工总承包特级"
        ctx = evidence_context([
            dict(
                document_id="sub16",
                source_sha256="hash_sub16",
                page_start=1,
                page_end=1,
                parent_text=table_passage,
            )
        ])
        result = StructuredExtractionResult(
            clause_id="T0004",
            requirement_text=req,
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="QUALIFICATION_LEVEL",
                operator="LEVEL_GE",
                expected_text="公路工程施工总承包三级",
                required_domain="公路工程",
                source_quotes={"expected_text": req, "required_domain": req},
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_text="公路工程施工总承包特级",
                actual_domain="公路工程",
                evidence_references=[
                    dict(field="actual_text", evidence_id="E1", quote="公路工程施工总承包特级"),
                    dict(field="actual_domain", evidence_id="E1", quote="公路工程施工总承包特级"),
                ],
                subject_bindings=[
                    dict(
                        field=f,
                        evidence_id="E1",
                        subject_name="安徽水利开发有限公司",
                        role="bidder",
                        quote=table_passage,
                        role_evidence_id="E1",
                        role_quote="投标人名称 | 安徽水利开发有限公司",
                    )
                    for f in ("actual_text", "actual_domain")
                ],
            ),
        )
        grounded = ground_extraction(result, ctx)
        self.assertEqual(grounded.bid_slots.evidence_source_type, "SELF_DECLARED_TABLE")
        rep = self.engine.evaluate(grounded)
        self.assertEqual(rep.verdict, "INSUFFICIENT_EVIDENCE")

    def test_performance_document_with_owner_manager_change_approval_links_correctly(self):
        req = "项目经理业绩须提供合同。"
        contract_text = "合同协议书\n发包人：郎溪县交通局\n承包人：安徽水利开发有限公司\n合同价：49123万元\n项目经理：向阳"
        change_approval = "关于项目经理变更的批复\n同意将项目经理变更为王文年同志。"
        roster_text = "拟派项目经理：王文年"
        ctx = evidence_context([
            dict(document_id="d1", source_sha256="h1", page_start=1, page_end=1, parent_text=contract_text),
            dict(document_id="d2", source_sha256="h2", page_start=2, page_end=2, parent_text=change_approval),
            dict(document_id="d3", source_sha256="h3", page_start=3, page_end=3, parent_text=roster_text),
        ])
        result = StructuredExtractionResult(
            clause_id="T0012",
            requirement_text=req,
            category="Document",
            tender_slots=TenderConstraintSlots(constraint_type="DOCUMENT_PRESENCE_AND_SEAL"),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                document_present=True,
                evidence_references=[
                    dict(field="document_present", evidence_id="E1", quote=contract_text),
                    dict(field="document_present", evidence_id="E2", quote=change_approval),
                ],
            ),
        )
        grounded = ground_extraction(result, ctx)
        self.assertEqual(grounded.extraction_issues, [])
        self.assertEqual(self.engine.evaluate(grounded).verdict, "COMPLIANT")


if __name__ == "__main__":
    unittest.main()

"""Unit tests for Step 4 & Step 5: S604 extraction benchmark and standalone extraction evaluator."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.extraction_eval import (
    DEFAULT_BENCHMARK_PATH,
    evaluate_clause_extraction,
    evaluate_extraction_benchmark,
    load_extraction_benchmark,
)
from src.extraction.llm_extractor import DeepSeekSlotExtractor, SYSTEM_EXTRACTION_PROMPT
from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
    VALID_CONSTRAINT_TYPES,
)


def make_gold_predictor():
    def _predict(item):
        return StructuredExtractionResult(
            clause_id=item["clause_id"],
            requirement_text=item["requirement_text"],
            category=item["category"],
            tender_slots=TenderConstraintSlots.from_dict(
                copy.deepcopy(item["gold_constraint_slots"])
            ),
            bid_slots=BidEvidenceSlots.from_dict(
                copy.deepcopy(item["gold_evidence_slots"])
            ),
            extractor_backend="gold_benchmark",
        )

    return _predict


class ExtractionBenchmarkAndEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.benchmark = load_extraction_benchmark(DEFAULT_BENCHMARK_PATH)

    def test_benchmark_structure_splits_and_coverage(self) -> None:
        self.assertEqual(self.benchmark["total_clauses"], 50)
        self.assertEqual(self.benchmark["dev_count"], 10)
        self.assertEqual(self.benchmark["test_count"], 40)
        self.assertEqual(self.benchmark["review_reference_date"], "2022-11-11")

        clauses = self.benchmark["clauses"]
        self.assertEqual(len(clauses), 50)

        dev_families = {c["rule_family"] for c in clauses if c["split"] == "dev"}
        expected_families = {
            "R01_NUMERIC_BOUND",
            "R02_QUALIFICATION_HIERARCHY",
            "R03_RMB_UPPER_AND_CONSISTENCY",
            "R04_DOCUMENT_AND_SEAL",
            "R05_PROHIBITION_AND_COMMITMENT",
            "R06_QUALITATIVE_AND_SCORING",
            "R07_STAFFING_COUNT_AND_QUALIFICATION",
            "R08_PERFORMANCE_CHAIN_AND_SCORING",
            "R09_FORMULA_BOUND",
        }
        self.assertTrue(expected_families.issubset(dev_families))

    def test_gold_benchmark_achieves_100_percent_across_all_metrics(self) -> None:
        report = evaluate_extraction_benchmark(
            self.benchmark, make_gold_predictor(), split="all"
        )
        self.assertEqual(report["evaluated_clauses"], 50)
        self.assertEqual(report["constraint_slot_accuracy"], 1.0)
        self.assertEqual(report["evidence_slot_accuracy"], 1.0)
        self.assertEqual(report["clause_exact_slot_accuracy"], 1.0)
        self.assertEqual(report["subject_binding_accuracy"], 1.0)
        self.assertEqual(report["citation_support_rate"], 1.0)
        self.assertEqual(report["unknown_abstention_rate"], 1.0)
        self.assertEqual(report["hallucination_rate"], 0.0)
        self.assertEqual(report["downstream_verdict_agreement"], 1.0)
        self.assertEqual(report["downstream_score_agreement"], 1.0)

    def test_evaluator_detects_hallucination_on_must_remain_unknown(self) -> None:
        # T0037 requires actual_numeric and actual_unit to remain unknown
        t0037 = next(
            c for c in self.benchmark["clauses"] if c["clause_id"] == "T0037"
        )
        pred = make_gold_predictor()(t0037)
        pred.bid_slots.actual_numeric = 90.0
        pred.bid_slots.actual_unit = "日"

        res = evaluate_clause_extraction(t0037, pred)
        self.assertIn("actual_numeric", res["hallucinated_fields"])
        self.assertIn("actual_unit", res["hallucinated_fields"])
        self.assertEqual(res["unknown_abstained"], 0)
        self.assertFalse(res["citation_supported"])

    def test_evaluator_detects_broken_subject_binding(self) -> None:
        # T0007 requires project_manager subject binding for 王文年
        t0007 = next(
            c for c in self.benchmark["clauses"] if c["clause_id"] == "T0007"
        )
        pred = make_gold_predictor()(t0007)
        pred.bid_slots.certificate_holder = "张三"
        pred.bid_slots.subject_bindings = []

        res = evaluate_clause_extraction(t0007, pred)
        self.assertTrue(res["has_subject_check"])
        self.assertFalse(res["subject_binding_ok"])
        self.assertFalse(res["verdict_match"])

    def test_prompt_and_extractor_align_with_step2_schema_and_inject_ref_date(self) -> None:
        for ctype in VALID_CONSTRAINT_TYPES:
            self.assertIn(ctype, SYSTEM_EXTRACTION_PROMPT)

        t0004 = next(
            c for c in self.benchmark["clauses"] if c["clause_id"] == "T0004"
        )
        mock_payload = {
            "tender_slots": copy.deepcopy(t0004["gold_constraint_slots"]),
            "bid_slots": copy.deepcopy(t0004["gold_evidence_slots"]),
        }
        # Model output leaves review_reference_date empty; extractor injects it from config
        mock_payload["tender_slots"]["review_reference_date"] = ""

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            extractor = DeepSeekSlotExtractor(
                cache_path=Path(folder) / "cache.json",
                review_reference_date="2022-11-11",
            )
            extractor._call_chat_completion = Mock(return_value=mock_payload)
            extracted = extractor.extract(
                t0004["clause_id"],
                t0004["requirement_text"],
                t0004["category"],
                json.dumps(t0004["evidence_context"], ensure_ascii=False),
            )
            self.assertEqual(extracted.tender_slots.review_reference_date, "2022-11-11")
            eval_res = evaluate_clause_extraction(t0004, extracted)
            self.assertTrue(eval_res["verdict_match"])
            self.assertEqual(eval_res["predicted_verdict"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.evaluation.evidence import score_evidence


class EvidenceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.hit = dict(document_id="a", source_sha256="file-a", page_start=2, page_end=2,
                        parent_text="我方承诺工期为365日历天。", parent_id="p1")
        self.ref = dict(document_id="a", source_sha256="file-a", page_start=2, page_end=2,
                        quote="我方承诺工期为365日历天。", source_verified=True, verification_basis="native_pdf")
        self.query = dict(clause_id="q1",requirement="工期不得超过360日历天", review_status="assistant_verified",
                          reviewer="test",review_note="Synthetic evidence",evidence_status="present",annotation_complete=True,
                          evidence_groups=[dict(group_id="duration",alternatives=[self.ref])])
        self.annotations = dict(schema_version=2,corpus_sha256="index",queries=[self.query])
        self.runs = dict(corpus_sha256="index",top_k=5,methods=["test"],sources={
            "a":dict(sha256="file-a",page_count=3),"b":dict(sha256="file-b",page_count=3)},
            queries=[dict(clause_id="q1",requirement=self.query["requirement"],methods={"test":[self.hit]})])

    def metrics(self):
        return score_evidence(self.annotations,self.runs)["strata"]["assistant_verified"]["methods"]["test"]["metrics"]

    def test_conflicting_value_is_still_relevant_evidence(self):
        self.assertEqual(self.metrics()["hit@5"],1)

    def test_same_keywords_without_annotated_evidence_do_not_match(self):
        self.hit["parent_text"] = "施工工期需要满足招标要求，360日历天。"
        self.assertEqual(self.metrics()["hit@5"],0)

    def test_wrong_source_or_page_does_not_match(self):
        self.hit["source_sha256"]="file-b"
        self.assertEqual(self.metrics()["hit@5"],0)
        self.hit["source_sha256"]="file-a"
        self.hit["page_start"]=self.hit["page_end"]=3
        self.assertEqual(self.metrics()["hit@5"],0)

    def test_groups_require_all_parts_alternatives_do_not_double_count(self):
        self.query["evidence_groups"][0]["alternatives"].append(copy.deepcopy(self.ref))
        other=copy.deepcopy(self.ref);other["quote"]="投标有效期为90天。"
        self.query["evidence_groups"].append(dict(group_id="validity",alternatives=[other]))
        m=self.metrics()
        self.assertEqual(m["hit@5"],1)
        self.assertEqual(m["evidence_recall_macro@5"],.5)
        self.assertEqual(m["complete_evidence_hit@5"],0)
        self.assertEqual(m["num_evidence_groups"],2)

    def test_rank_beyond_k_is_not_counted(self):
        wrong=copy.deepcopy(self.hit);wrong["parent_text"]="无关段落"
        self.runs["queries"][0]["methods"]["test"]=[wrong,wrong,self.hit]
        self.assertEqual(self.metrics()["hit@1"],0)
        self.assertEqual(self.metrics()["mrr@5"],.3333)

    def test_unreviewed_and_partial_labels_do_not_become_zero_scores(self):
        self.query["review_status"]="pending"
        self.assertIsNone(self.metrics()["hit@5"])
        self.query["review_status"]="assistant_verified";self.query["annotation_complete"]=False
        self.assertIsNone(self.metrics()["hit@5"])

    def test_human_and_assistant_annotations_remain_separate(self):
        report=score_evidence(self.annotations,self.runs)
        self.assertEqual(report["strata"]["human_verified"]["scored_positive_queries"],0)
        self.assertIsNone(report["strata"]["human_verified"]["methods"]["test"]["metrics"]["hit@5"])

    def test_invalid_reference_and_changed_corpus_rejected(self):
        self.ref["source_verified"]=False
        with self.assertRaises(ValueError):self.metrics()
        self.ref["source_verified"]=True;self.annotations["corpus_sha256"]="other"
        with self.assertRaises(ValueError):self.metrics()

    def test_table_parts_keep_values_without_requiring_unneeded_quantity_cell(self):
        self.ref['quote']='安全生产费 总额 1 1324528.73'
        self.ref['quote_parts']=['安全生产费','1324528.73']
        self.hit['parent_text']='安全生产费 | 总额 | 1324528.73'
        self.assertEqual(self.metrics()['hit@5'],1)
        self.hit['parent_text']='安全生产费 | 总额 | 1324528.74'
        self.assertEqual(self.metrics()['hit@5'],0)
        self.ref['quote_parts']=['安全生产费','99999']
        with self.assertRaises(ValueError):self.metrics()

    def test_virtual_document_alias_for_same_pdf_is_allowed(self):
        self.hit['document_id']='other-section-of-same-file'
        self.assertEqual(self.metrics()['hit@5'],1)


if __name__ == "__main__":
    unittest.main()

import copy
import unittest

from src.evaluation.compliance_reference import compare_assistant_reference
from diagnose_evidence_rag import diagnose


class ReferenceAgreementTests(unittest.TestCase):
    def setUp(self):
        self.cases = [dict(clause_id='q1', requirement='客观评分'),
                      dict(clause_id='q2', requirement='主观评分')]
        self.labels = dict(schema_version=2, scope='full_source_document_review', queries=[
            dict(clause_id='q1', requirement='客观评分', review_status='assistant_verified',
                 review_note='synthetic reference', expected_verdict='SCORED', expected_score=6),
            dict(clause_id='q2', requirement='主观评分', review_status='assistant_verified',
                 review_note='human judgment required', expected_verdict=None, expected_score=None)])
        self.results = [dict(clause_id='q1', verdict='SCORED', awarded_score=2),
                        dict(clause_id='q2', verdict='COMPLIANT')]

    def test_score_errors_separate_from_verdict_and_subjective_excluded(self):
        report = compare_assistant_reference(self.cases, self.results, self.labels)
        self.assertEqual(report['assistant_reference_verdict_agreement'], 1)
        self.assertEqual(report['assistant_reference_score_agreement'], 0)
        self.assertEqual(report['excluded_subjective_cases'], 1)
        self.assertIsNone(report['formal_human_gold_accuracy'])

    def test_changed_requirement_and_duplicate_prediction_rejected(self):
        labels = copy.deepcopy(self.labels)
        labels['queries'][0]['requirement'] = 'changed'
        with self.assertRaises(ValueError):
            compare_assistant_reference(self.cases, self.results, labels)
        with self.assertRaises(ValueError):
            compare_assistant_reference(self.cases, self.results[:1]*2, self.labels)

    def test_errors_count_as_disagreement_and_human_not_pooled(self):
        results = copy.deepcopy(self.results)
        results[0]['verdict'] = 'ERROR'
        report = compare_assistant_reference(self.cases, results, self.labels)
        self.assertEqual(report['assistant_reference_verdict_agreement'], 0)
        labels = copy.deepcopy(self.labels)
        labels['queries'][0]['review_status'] = 'human_verified'
        with self.assertRaises(ValueError):
            compare_assistant_reference(self.cases, self.results, labels)

    def test_diagnosis_separates_index_text_from_ranking(self):
        refs = [dict(document_id='d', source_sha256='s', page_start=1, page_end=1,
                     quote=text, source_verified=True, verification_basis='native_pdf')
                for text in ['actual evidence', 'OCR lost text']]
        annotations = dict(schema_version=2, corpus_sha256='corpus', queries=[
            dict(clause_id='q', requirement='r', review_status='assistant_verified',
                 evidence_status='present', annotation_complete=True, reviewer='test',
                 review_note='synthetic', evidence_groups=[
                     dict(group_id=str(i), alternatives=[ref]) for i, ref in enumerate(refs)])])
        retrieval = dict(corpus_sha256='corpus', methods=['m'], sources={'d':dict(sha256='s',page_count=1)},
                         queries=[dict(clause_id='q',requirement='r',methods={'m':[]})])
        parents = [dict(document_id='d',parent_id='p',page_start=1,page_end=1,text='actual evidence')]
        counts = diagnose(annotations, retrieval, parents)['methods']['m']['group_counts']
        self.assertEqual(counts, {'indexed_but_not_top_k':1,'indexed_page_text_mismatch':1})


if __name__ == '__main__':
    unittest.main()

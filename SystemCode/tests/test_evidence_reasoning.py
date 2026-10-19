"""Missing evidence, provenance and cache boundaries for the full passage flow."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluate_compliance_reasoning import build_cases, compute_metrics
from src.extraction import DeepSeekSlotExtractor, HeuristicSlotExtractor
from src.extraction.evidence import evidence_context
from src.extraction.schema import BidEvidenceSlots, TenderConstraintSlots, StructuredExtractionResult
from src.rules import ComplianceRuleEngine


class EvidenceReasoningTests(unittest.TestCase):
    def setUp(self):
        self.passage=dict(rank=1,document_id='bid',source_sha256='sha',page_start=4,page_end=4,
                          parent_text='我方承诺工期为365日历天。',extracted_kv={'duration_days':360})
        self.context=evidence_context([self.passage])
        self.req='工期不超过360日历天。'
        self.answer=dict(tender_slots=dict(constraint_type='NUMERIC_BOUND',target_field='duration_days',
            operator='<=',expected_numeric=360,expected_unit='日历天',
            source_quotes={'expected_numeric':self.req,'expected_unit':self.req}),
            bid_slots=dict(evidence_found=True,actual_numeric=365,actual_unit='日历天',
            evidence_references=[dict(field=f,evidence_id='E1',quote='工期为365日历天')
                                 for f in ('actual_numeric','actual_unit')]))
        self.engine=ComplianceRuleEngine()

    def extract(self, answer=None, context=None):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            ex=DeepSeekSlotExtractor(cache_path=Path(folder)/'cache.json')
            ex._call_chat_completion=Mock(return_value=answer or self.answer)
            result=ex.extract('q',self.req,'Schedule',context or self.context)
            return result

    def test_real_passage_to_mock_llm_to_rule_preserves_citation_and_violation(self):
        result=self.extract()
        report=self.engine.evaluate(result)
        self.assertEqual(report.verdict,'NON_COMPLIANT')
        self.assertEqual(report.evidence_references[0]['source_sha256'],'sha')
        self.assertEqual(report.evidence_pages,[4])
        self.assertNotIn('extracted_kv',self.context)
        self.assertNotIn('360',self.context)

    def test_empty_retrieval_does_not_call_llm_or_invent_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            ex=DeepSeekSlotExtractor(cache_path=Path(folder)/'cache.json')
            ex._call_chat_completion=Mock(side_effect=AssertionError('Must not call'))
            result=ex.extract('q',self.req,'Schedule','[]')
            self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')
            ex._call_chat_completion.assert_not_called()

    def test_model_cannot_copy_requirement_value_as_bid_fact(self):
        answer=copy.deepcopy(self.answer);answer['bid_slots']['actual_numeric']=360
        result=self.extract(answer)
        self.assertTrue(result.extraction_issues)
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_invented_evidence_id_or_quote_is_rejected(self):
        for key,value in [('evidence_id','E999'),('quote','工期为360日历天')]:
            answer=copy.deepcopy(self.answer);answer['bid_slots']['evidence_references'][0][key]=value
            self.assertTrue(self.extract(answer).extraction_issues)

    def test_string_false_and_nan_are_invalid_model_outputs(self):
        for key,value in [('evidence_found','false'),('actual_numeric',float('nan'))]:
            answer=copy.deepcopy(self.answer);answer['bid_slots'][key]=value
            with self.assertRaises(ValueError):self.extract(answer)

    def test_unit_substrings_and_copied_entity_citations_are_not_accepted(self):
        answer=copy.deepcopy(self.answer)
        answer['bid_slots'].update(actual_numeric=50,actual_unit='元')
        answer['bid_slots']['evidence_references']=[dict(field=f,evidence_id='E1',quote='报价50万元')
                                                   for f in ('actual_numeric','actual_unit')]
        context=evidence_context([dict(self.passage,parent_text='报价50万元')])
        self.assertTrue(self.extract(answer,context).extraction_issues)
        answer=copy.deepcopy(self.answer)
        answer['bid_slots'].update(entity_primary='示例企业',entity_secondary='示例企业')
        answer['bid_slots']['evidence_references'] += [dict(field=f,evidence_id='E1',quote='投标人：示例企业')
                                                     for f in ('entity_primary','entity_secondary')]
        context=evidence_context([dict(self.passage,parent_text=self.passage['parent_text']+'投标人：示例企业')])
        self.assertTrue(self.extract(answer,context).extraction_issues)

    def test_cache_reuses_only_same_prompt_endpoint_and_context(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as folder:
            path=Path(folder)/'cache.json'
            ex=DeepSeekSlotExtractor(cache_path=path)
            ex._call_chat_completion=Mock(return_value=self.answer)
            ex.extract('q',self.req,'Schedule',self.context)
            ex.extract('q',self.req,'Schedule',self.context)
            self.assertEqual(ex._call_chat_completion.call_count,1)
            offline=DeepSeekSlotExtractor(cache_path=path,cache_only=True)
            offline.extract('q',self.req,'Schedule',self.context)
            changed=self.context.replace('365','364')
            with self.assertRaises(RuntimeError):offline.extract('q',self.req,'Schedule',changed)
            other=DeepSeekSlotExtractor(cache_path=path,cache_only=True,base_url='https://example.invalid')
            with self.assertRaises(RuntimeError):other.extract('q',self.req,'Schedule',self.context)

    def test_no_threshold_no_unit_and_no_secondary_stay_unknown(self):
        result=self.extract()
        for field,value in [('expected_numeric',None),('expected_unit',''),('secondary_numeric',12)]:
            test=copy.deepcopy(result);setattr(test.tender_slots,field,value)
            self.assertEqual(self.engine.evaluate(test).verdict,'INSUFFICIENT_EVIDENCE')

    def test_unknown_organization_is_not_copied(self):
        result=self.extract()
        result.tender_slots=TenderConstraintSlots(constraint_type='ENTITY_CONSISTENCY')
        result.bid_slots=BidEvidenceSlots(evidence_found=True,entity_primary='示例企业')
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_stamp_unknown_and_document_title_do_not_pass(self):
        result=self.extract()
        result.tender_slots=TenderConstraintSlots(constraint_type='DOCUMENT_PRESENCE_AND_SEAL',requires_official_seal=True)
        result.bid_slots=BidEvidenceSlots(evidence_found=True,document_present=True)
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')
        result.tender_slots.requires_official_seal=False;result.bid_slots.document_present=None
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_conflict_and_unknown_qualitative_have_separate_results(self):
        result=self.extract();result.bid_slots.conflict_detected=True
        self.assertEqual(self.engine.evaluate(result).verdict,'CONFLICTING_EVIDENCE')
        result.bid_slots.conflict_detected=False;result.tender_slots=TenderConstraintSlots()
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_offline_extractor_does_not_guess_validity_or_qualitative_response(self):
        ex=HeuristicSlotExtractor()
        for req in ['投标有效期不少于120日。','施工方案须满足要求。','投标报价不得超过最高投标限价。']:
            result=ex.extract('q',req,'Schedule',evidence_context([dict(self.passage,parent_text='我方承诺在投标有效期内不撤销。')]))
            self.assertIsNone(result.bid_slots.actual_numeric)
            self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_prepare_uses_ranked_passages_without_gold_keyword_rescue(self):
        raw=dict(schema_version=2,status='complete',top_k=5,methods=['method'],
                 sources={'bid':dict(sha256='sha',page_count=5)},queries=[
                 dict(clause_id='q',requirement=self.req,predicted_category='Schedule',
                      target_keywords=['360'],gold_category='Other',methods={'method':[self.passage]})])
        cases=build_cases(raw,'method')
        self.assertEqual(cases[0]['category'],'Schedule')
        self.assertEqual(cases[0]['evidence_context'],self.context)
        self.assertNotIn('expected_verdict',cases[0])
        raw['queries'][0]['methods']['method']=[]
        self.assertEqual(build_cases(raw,'method')[0]['evidence_context'],'[]')

    def test_unlabelled_data_has_no_accuracy_and_abstentions_not_true_negatives(self):
        cases=[{'clause_id':'q'}];results=[dict(clause_id='q',verdict='INSUFFICIENT_EVIDENCE')]
        self.assertIsNone(compute_metrics(cases,results)['verdict_accuracy'])
        labels=dict(schema_version=1,cases_sha256='hash',queries=[dict(clause_id='q',
                    review_status='human_verified',reviewer='tester',review_note='synthetic test',expected_verdict='NON_COMPLIANT')])
        self.assertEqual(compute_metrics(cases,results,labels,'hash')['verdict_accuracy'],0)
        with self.assertRaises(ValueError):compute_metrics(cases,results,labels,'wrong')

    def test_zero_scoring_is_not_noncompliance_and_no_max_score_fallback(self):
        result=self.extract()
        result.tender_slots=TenderConstraintSlots(constraint_type='QUALITATIVE_OR_SCORING',operator='SCORE_BRACKET',
            expected_unit='%',scoring_bands=[dict(min=90,max=None,min_inclusive=True,max_inclusive=False,points=0)])
        result.bid_slots=BidEvidenceSlots(evidence_found=True,actual_numeric=95,actual_unit='%')
        report=self.engine.evaluate(result)
        self.assertEqual(report.verdict,'SCORED');self.assertEqual(report.awarded_score,0)
        result.tender_slots.scoring_bands=[];result.tender_slots.scoring_max_points=6
        self.assertEqual(self.engine.evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')


if __name__=='__main__':unittest.main()

"""Counterexamples for operators, actor attribution and substantive citations."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.extraction.schema import StructuredExtractionResult, TenderConstraintSlots, BidEvidenceSlots
from src.extraction.evidence import evidence_context, ground_extraction
from src.extraction.semantics import explicit_numeric_operator
from src.rules import ComplianceRuleEngine


def context(*texts):
    return evidence_context([dict(document_id=f'doc{i}',source_sha256=f'hash{i}',
        page_start=1,page_end=1,parent_text=text) for i,text in enumerate(texts)])


def numeric(req,op='<=',actual=80,quote='投标报价80万元'):
    return StructuredExtractionResult('q',req,'Commercial',
        TenderConstraintSlots(constraint_type='NUMERIC_BOUND',operator=op,expected_numeric=100,
            expected_unit='万元',source_quotes={'expected_numeric':req,'expected_unit':req}),
        BidEvidenceSlots(evidence_found=True,actual_numeric=actual,actual_unit='万元',
            evidence_references=[dict(field=f,evidence_id='E1',quote=quote) for f in ('actual_numeric','actual_unit')]))


class ExtractionSemanticsTests(unittest.TestCase):
    def test_operator_boundary_phrases_and_ambiguity(self):
        for req,op in [('招标控制价100万元，含暂列金8万元。','<='),('最高投标限价为100万元。','<='),
            ('金额不得高于100万元。','<='),('保证金不少于100万元。','>='),('金额不得低于100万元。','>='),
            ('金额高于100万元。','>'),('金额低于100万元。','<'),('保证金为100万元。','=='),
            ('计划工期为100日历天。','=='),('金额100万元及以上。','>='),
            ('金额并非低于100万元。',None),('金额不等于100万元。',None),
            ('工期不超过100日；保证金不少于100万元。',None),('金额100万元。',None)]:
            with self.subTest(req=req):self.assertEqual(explicit_numeric_operator(req,100),op)

    def test_control_price_wrong_equality_is_blocked_and_boundaries_work(self):
        req='招标控制价100万元。'
        wrong=ground_extraction(numeric(req,op='=='),context('投标报价80万元'))
        self.assertTrue(any('operator contradicts' in e for e in wrong.extraction_issues))
        self.assertEqual(ComplianceRuleEngine().evaluate(wrong).verdict,'INSUFFICIENT_EVIDENCE')
        for amount,verdict in [(80,'COMPLIANT'),(100,'COMPLIANT'),(101,'NON_COMPLIANT')]:
            quote=f'投标报价{amount}万元'
            result=ground_extraction(numeric(req,actual=amount,quote=quote),context(quote))
            self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict,verdict)

    def test_lower_bound_does_not_become_upper_bound(self):
        req='保证金不少于100万元。'
        for amount,verdict in [(99,'NON_COMPLIANT'),(100,'COMPLIANT'),(101,'COMPLIANT')]:
            quote=f'保证金{amount}万元'
            result=ground_extraction(numeric(req,op='>=',actual=amount,quote=quote),context(quote))
            self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict,verdict)

    def test_unit_from_another_document_is_not_attached_to_number(self):
        result=numeric('招标控制价100万元。',quote='报价80')
        result.bid_slots.evidence_references[1]=dict(field='actual_unit',evidence_id='E2',quote='单位：万元')
        grounded=ground_extraction(result,context('报价80','历史合同，单位：万元'))
        self.assertTrue(any('linked source' in e for e in grounded.extraction_issues))

    def test_table_header_can_support_unit_in_same_evidence(self):
        result=numeric('招标控制价100万元。',quote='报价80')
        result.bid_slots.evidence_references[1]['quote']='金额（万元）'
        result=ground_extraction(result,context('金额（万元）\n报价80'))
        self.assertEqual(result.extraction_issues,[])

    def test_secondary_amount_cannot_borrow_main_amount_unit(self):
        result=numeric('招标控制价100万元，含暂列金8万元。')
        result.tender_slots.secondary_numeric=8
        result.tender_slots.source_quotes['secondary_numeric']='含暂列金8万元'
        result.bid_slots.secondary_actual_numeric=8
        result.bid_slots.evidence_references.append(dict(field='secondary_actual_numeric',evidence_id='E2',quote='暂列金8'))
        result=ground_extraction(result,context('投标报价80万元','暂列金8'))
        self.assertTrue(any('Secondary numeric value' in e for e in result.extraction_issues))

    def test_concatenated_numeric_cells_do_not_become_one_fact(self):
        result=numeric('招标控制价100万元。',actual=80,quote='报价80万元')
        result=ground_extraction(result,context('报价8\n0万元'))
        self.assertTrue(any('Numeric fact' in e for e in result.extraction_issues))

    def qualification(self):
        req='投标人须具备公路工程施工总承包三级及以上资质。'
        identity='投标人名称 | 甲公司'
        certificate='企业名称：甲公司\n公路工程施工总承包特级'
        ctx=context('企业名称：乙公司\n公路工程施工总承包三级',identity,certificate)
        result=StructuredExtractionResult('q',req,'Qualification',
            TenderConstraintSlots(constraint_type='QUALIFICATION_LEVEL',operator='LEVEL_GE',
                expected_text='公路工程施工总承包三级',required_domain='公路工程',
                source_quotes={'expected_text':req,'required_domain':req}),
            BidEvidenceSlots(evidence_found=True,actual_text='公路工程施工总承包特级',actual_domain='公路工程',
                evidence_references=[dict(field=f,evidence_id='E3',quote='公路工程施工总承包特级') for f in ('actual_text','actual_domain')],
                subject_bindings=[dict(field=f,evidence_id='E3',subject_name='甲公司',role='bidder',quote=certificate,
                    role_evidence_id='E2',role_quote=identity) for f in ('actual_text','actual_domain')]))
        return result,ctx

    def test_bidder_certificate_selected_despite_partner_ranked_first(self):
        result,ctx=self.qualification()
        result=ground_extraction(result,ctx)
        self.assertEqual(result.extraction_issues,[])
        report=ComplianceRuleEngine().evaluate(result)
        self.assertEqual(report.verdict,'COMPLIANT')
        self.assertEqual(report.subject_bindings[0]['role_source_sha256'],'hash1')

    def test_unbound_extra_qualification_citation_is_not_ignored(self):
        result,ctx=self.qualification()
        result.bid_slots.evidence_references.append(dict(field='actual_domain',evidence_id='E1',quote='公路工程施工总承包三级'))
        result=ground_extraction(result,ctx)
        self.assertTrue(any('lacks subject attribution' in i for i in result.extraction_issues))

    def test_organization_owner_not_replaced_by_contact_person_name(self):
        result,ctx=self.qualification()
        passages=json.loads(ctx)
        passages[2]['text']='企业名称：甲公司\n法定代表人 | 姓名 | 张三\n公路工程施工总承包特级'
        for binding in result.bid_slots.subject_bindings:binding['quote']=passages[2]['text']
        self.assertEqual(ground_extraction(result,json.dumps(passages)).extraction_issues,[])

    def test_verified_role_quote_may_support_a_separate_fact_quote(self):
        result,ctx=self.qualification()
        passages=json.loads(ctx)
        passages[2]['text']='投标人名称 | 甲公司\n公路工程施工总承包特级'
        for binding in result.bid_slots.subject_bindings:
            binding.update(quote=passages[2]['text'],role_evidence_id='E3',role_quote='投标人名称 | 甲公司')
        result.bid_slots.evidence_references.append(dict(field='actual_text',evidence_id='E3',quote='投标人名称 | 甲公司'))
        self.assertEqual(ground_extraction(result,json.dumps(passages)).extraction_issues,[])

    def test_identity_quote_without_qualification_fact_cannot_pass(self):
        result,ctx=self.qualification()
        result.bid_slots.evidence_references=[dict(field='actual_text',evidence_id='E2',quote='投标人名称 | 甲公司')]
        result=ground_extraction(result,ctx)
        self.assertTrue(result.extraction_issues)

    def test_partner_cannot_become_bidder_via_model_role_label(self):
        result,ctx=self.qualification()
        result.bid_slots.actual_text='公路工程施工总承包三级'
        for ref in result.bid_slots.evidence_references:
            ref.update(evidence_id='E1',quote='公路工程施工总承包三级')
        for link in result.bid_slots.subject_bindings:
            link.update(evidence_id='E1',subject_name='乙公司',quote='企业名称：乙公司\n公路工程施工总承包三级')
        result=ground_extraction(result,ctx)
        self.assertTrue(result.extraction_issues)
        self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict,'INSUFFICIENT_EVIDENCE')

    def test_missing_subject_binding_and_ambiguous_roles_are_blocked(self):
        result,ctx=self.qualification()
        result.bid_slots.subject_bindings=[]
        self.assertTrue(ground_extraction(result,ctx).extraction_issues)
        result,ctx=self.qualification()
        passages=json.loads(ctx)
        passages[0]['text']='投标人名称：乙公司\n企业名称：乙公司\n公路工程施工总承包三级'
        self.assertTrue(ground_extraction(result,json.dumps(passages)).extraction_issues)

    def test_borrowing_fact_from_second_owner_in_same_quote_is_blocked(self):
        result,ctx=self.qualification()
        passages=json.loads(ctx)
        passages[2]['text']='企业名称：甲公司\n其他资料\n企业名称：乙公司\n公路工程施工总承包特级'
        for binding in result.bid_slots.subject_bindings:binding['quote']=passages[2]['text']
        result=ground_extraction(result,json.dumps(passages))
        self.assertTrue(any('labelled owner' in i for i in result.extraction_issues))

    def test_title_only_rejected_but_actual_document_body_accepted(self):
        req='须提交法定代表人身份证明或授权委托书。'
        body='本人张三系甲公司法定代表人，现委托李四为我方代理人。'
        for quote,ok in [('授权委托书',False),('附件：法定代表人身份证明',False),
                         ('本人____系____法定代表人。',False),(body,True)]:
            result=StructuredExtractionResult('q',req,'Document',TenderConstraintSlots(
                constraint_type='DOCUMENT_PRESENCE_AND_SEAL'),BidEvidenceSlots(evidence_found=True,document_present=True,
                evidence_references=[dict(field='document_present',evidence_id='E1',quote=quote)]))
            result=ground_extraction(result,context(quote))
            self.assertEqual(not result.extraction_issues,ok)
            self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict,'COMPLIANT' if ok else 'INSUFFICIENT_EVIDENCE')


if __name__=='__main__':unittest.main()

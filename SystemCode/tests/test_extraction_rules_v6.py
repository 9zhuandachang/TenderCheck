"""Positive and negative fixtures independent of S604 answer labels."""
import copy
import json
import unittest

from test_extraction_semantics import context
from src.extraction.schema import StructuredExtractionResult, TenderConstraintSlots, BidEvidenceSlots
from src.extraction.evidence import ground_extraction
from src.extraction.semantics import has_document_body, role_names, qualification_owned_by, text_fact_supported
from src.extraction.conflicts import verify_conflict_pairs
from src.rules import ComplianceRuleEngine


class ExtractionRulesV6Tests(unittest.TestCase):
    def test_document_body_is_specific_to_required_document(self):
        letter = '投标函\n我方已仔细研究本标段，愿意以人民币100元的投标总价报价。'
        self.assertTrue(has_document_body(letter, '提交含报价投标函'))
        for text in ['投标函（含报价）', '附件目录：投标函', letter.replace('100', '____'),
                     '现确定甲公司为中标人，中标价为100元']:
            self.assertFalse(has_document_body(text, '提交含报价投标函'))
        self.assertFalse(has_document_body(letter.replace('100元', ''), '提交含报价投标函'))
        self.assertTrue(has_document_body('现确定甲公司为中标人，中标价为100元', '提交中标通知书'))
        self.assertFalse(has_document_body('中标通知书', '提交中标通知书'))

    def test_quality_equivalence_does_not_change_grade_or_stage(self):
        value = '交工验收质量评定为合格'
        self.assertTrue(text_fact_supported(value, '标段工程交工验收的质量评定：合格'))
        for quote in ['交工验收质量评定为不合格', '竣工验收质量评定为合格',
                      '交工验收质量评定为合格是不可能的', '未承诺交工验收质量评定为合格',
                      '招标要求交工验收质量评定为合格']:
            self.assertFalse(text_fact_supported(value, quote), quote)
        self.assertFalse(text_fact_supported('合格', '未达到合格'))
        self.assertFalse(text_fact_supported('甲公司', '乙公司'))

    def target_result(self, requirement, quote, claim=True):
        result = StructuredExtractionResult('q', requirement, 'Technical',
            TenderConstraintSlots(constraint_type='PROHIBITION_AND_COMMITMENT', operator='QUALITATIVE_MATCH'),
            BidEvidenceSlots(evidence_found=True, qualitative_match=claim,
                evidence_references=[dict(field='qualitative_match', evidence_id='E1', quote=quote)]))
        return ground_extraction(result, context(quote))

    def test_safety_target_dispatch_and_negative_quality_response(self):
        engine = ComplianceRuleEngine()
        result = self.target_result('安全目标为无生产安全责任事故。', '安全目标：无生产安全责任事故')
        self.assertEqual(engine.evaluate(result).verdict, 'COMPLIANT')
        self.assertEqual(engine.evaluate(result).triggered_rule_id, 'R06_OBJECTIVE_TARGET')
        result = self.target_result('交工验收质量评定须为合格。', '交工验收质量评定为不合格', False)
        self.assertEqual(engine.evaluate(result).verdict, 'NON_COMPLIANT')
        result.bid_slots.qualitative_match = True
        self.assertEqual(engine.evaluate(result).verdict, 'INSUFFICIENT_EVIDENCE')

    def test_one_valid_target_does_not_hide_uninterpretable_other_quote(self):
        result = self.target_result('安全目标为无生产安全责任事故。', '安全目标：无生产安全责任事故')
        result.bid_slots.evidence_references.append(dict(field='qualitative_match', quote='未承诺安全目标'))
        self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict, 'INSUFFICIENT_EVIDENCE')

    def test_roles_in_header_defined_tables_and_resume(self):
        text = '| 姓名 | 拟在本标段工程任职 | 职称 |\n| --- | --- | --- |\n| 张三 | 道路工程师 | 中级 |\n| 李四 | 桥梁工程师 | 高级 |'
        self.assertEqual(role_names(text, 'road_engineer'), {'张三'})
        self.assertEqual(role_names(text, 'bridge_engineer'), {'李四'})
        self.assertTrue(qualification_owned_by(text, '张三', '| 张三 | 道路工程师 | 中级 |', 'road_engineer'))
        self.assertFalse(qualification_owned_by(text, '张三', '高级', 'road_engineer'))
        resume = '姓\n名\n张三\n拟在本标段\n工程任职\n道路工程师\n经历\n桥梁工程师：李四'
        self.assertEqual(role_names(resume, 'road_engineer'), {'张三'})
        self.assertEqual(role_names('项目管理机构\n职务\n姓名\n专职安全员1\n张三\n专职安全员2\n李四', 'safety_officer'), {'张三', '李四'})
        reverse = '拟委任人员汇总表\n姓名\n拟在本标段工程任职\n张三\n道路工程师\n中级\n' + text
        self.assertEqual(role_names(reverse, 'road_engineer'), {'张三'})
        resume = resume.replace('经历', '工作年限\n11年\n经历')
        self.assertEqual(role_names(resume, 'road_engineer'), {'张三'})

    def test_role_and_fact_binding_end_to_end(self):
        for label, role in [('道路工程师','road_engineer'),('桥梁工程师','bridge_engineer'),('专职安全员','safety_officer')]:
            identity = f'| 姓名 | 职务 |\n| --- | --- |\n| 张三 | {label} |'
            certificate = '姓名：张三\n中级工程师'
            req = f'{label}须具备中级工程师职称。'
            result = StructuredExtractionResult('q', req, 'Qualification',
                TenderConstraintSlots(constraint_type='QUALIFICATION_LEVEL', operator='LEVEL_GE',
                    expected_text='中级工程师', source_quotes={'expected_text':req}),
                BidEvidenceSlots(evidence_found=True, actual_text='中级工程师',
                    evidence_references=[dict(field='actual_text', evidence_id='E2', quote='中级工程师')],
                    subject_bindings=[dict(field='actual_text', evidence_id='E2', subject_name='张三', role=role,
                        quote=certificate, role_evidence_id='E1', role_quote=identity)]))
            result = ground_extraction(result, context(identity, certificate))
            self.assertEqual(result.extraction_issues, [], label)
            self.assertEqual(ComplianceRuleEngine().evaluate(result).verdict, 'COMPLIANT')

    def test_award_document_for_other_manager_is_not_current_performance(self):
        quote = '现确定甲公司为中标人，中标价为100元。\n项目经理：李四'
        result = StructuredExtractionResult('q', '项目经理业绩须提供中标通知书。', 'Document',
            TenderConstraintSlots(constraint_type='DOCUMENT_PRESENCE_AND_SEAL'),
            BidEvidenceSlots(evidence_found=True, document_present=True,
                evidence_references=[dict(field='document_present', evidence_id='E1', quote=quote)]))
        result = ground_extraction(result, context(quote, '拟派项目经理：张三'))
        self.assertTrue(any('currently proposed' in issue for issue in result.extraction_issues))

    def conflict_fixture(self):
        quotes = ['投标人名称：甲公司\n项目：本标段\n工期：100天',
                  '投标人名称：甲公司\n项目：本标段\n工期：120天']
        evidence = {p['evidence_id']:p for p in json.loads(context(*quotes))}
        pair = dict(subject='甲公司', role='bidder', aspect='工期', period='本标段',
                    role_evidence_id='E1', role_quote='投标人名称：甲公司',
                    left=dict(evidence_id='E1',quote=quotes[0],value='100天'),
                    right=dict(evidence_id='E2',quote=quotes[1],value='120天'))
        return pair, evidence

    def test_two_sided_conflict_keeps_provenance_in_rule_report(self):
        pair, evidence = self.conflict_fixture()
        result = StructuredExtractionResult('q', '工期为100天。', 'Schedule', TenderConstraintSlots(),
            BidEvidenceSlots(evidence_found=True, conflict_detected=True, conflict_pairs=[pair],
                evidence_references=[dict(field='actual_text', evidence_id='E1', quote=pair['left']['quote'])]))
        grounded = ground_extraction(result, json.dumps(list(evidence.values())))
        self.assertEqual(grounded.extraction_issues, [])
        report = ComplianceRuleEngine().evaluate(grounded)
        self.assertEqual(report.verdict, 'CONFLICTING_EVIDENCE')
        self.assertEqual(report.conflict_pairs[0]['right']['source_sha256'], 'hash1')

    def test_unsupported_conflicts_stay_unknown(self):
        pair, evidence = self.conflict_fixture()
        self.assertTrue(verify_conflict_pairs([], evidence, '工期为100天')[1])
        for replacement in ['投标人名称：乙公司\n项目：本标段\n工期：120天',
                            '投标人名称：甲公司\n项目：历史标段\n工期：120天',
                            '投标人名称：甲公司\n项目：本标段\n工期：100.0天',
                            '投标人名称：甲公司\n项目：本标段\n工期：120日',
                            '投标人名称：甲公司\n项目：本标段\n工期：100天\n企业名称：乙公司\n工期：120天']:
            trial, passages = copy.deepcopy(pair), copy.deepcopy(evidence)
            passages['E2']['text'] = replacement
            trial['right']['quote'] = replacement
            trial['right']['value'] = '100.0天' if '100.0' in replacement else '120日' if '120日' in replacement else '120天'
            valid, issues = verify_conflict_pairs([trial], passages, '工期为100天')
            self.assertFalse(valid, replacement)
            self.assertTrue(issues)
        pair['role_quote'] = '企业名称：甲公司'
        self.assertFalse(verify_conflict_pairs([pair], evidence, '工期为100天')[0])

    def test_conflict_cannot_join_numeric_table_cells(self):
        pair, evidence = self.conflict_fixture()
        evidence['E2']['text'] = pair['right']['quote'].replace('120', '12\n0')
        self.assertFalse(verify_conflict_pairs([pair], evidence, '工期为100天')[0])


if __name__ == '__main__':
    unittest.main()

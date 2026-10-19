"""Check the recovery acceptance boundary without loading any neural weights."""
import json
import sys
import unittest
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.vision import QwenVLBackend

METADATA = json.dumps(dict(doc_category='财务状况与审计报告', has_official_seal=True,
                          seal_company_name=None, has_signature=False, extracted_kv={}))

def output(text, reason='eos'):
    return dict(text=text, finish_reason=reason, tokens=1, seconds=0)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.doc = fitz.open()
        self.doc.new_page(width=150, height=220)
        self.backend = QwenVLBackend(dpi=72)

    def tearDown(self):
        self.doc.close()

    def test_spatial_retry_keeps_both_halves(self):
        attempts = iter([output('incomplete', 'length'), output('上半页'),
                         output('下半页'), output(METADATA)])
        self.backend._generate_recovery = lambda *args: next(attempts)
        result, audit = self.backend.analyze_page_recovery(self.doc, 1)
        self.assertEqual(result.ocr_text, '上半页\n下半页')
        self.assertEqual(audit['status'], 'recovered')
        self.assertEqual(len(audit['ocr_attempts']), 3)

    def test_unfinished_ocr_is_not_accepted(self):
        self.backend._generate_recovery = lambda *args: output('partial', 'length')
        result, audit = self.backend.analyze_page_recovery(self.doc, 1)
        self.assertIsNone(result)
        self.assertEqual(audit['status'], 'failed')
        self.assertEqual(len(audit['ocr_attempts']), 7)

    def test_invalid_metadata_is_not_accepted(self):
        attempts = iter([output('完整正文'), output('{'), output(METADATA, 'length')])
        self.backend._generate_recovery = lambda *args: next(attempts)
        result, audit = self.backend.analyze_page_recovery(self.doc, 1)
        self.assertIsNone(result)
        self.assertEqual(audit['reason'], 'invalid_metadata')

    def test_category_normalization_is_bounded(self):
        normalize=QwenVLBackend.normalize_recovery_category
        self.assertEqual(normalize('中标通知书'),'近年类似工程业绩证明')
        self.assertEqual(normalize('商务文件','投标人近年完成的类似项目情况表'),'近年类似工程业绩证明')
        self.assertEqual(normalize('商务文件'),'商务文件')
        self.assertEqual(normalize('未知标签','投标人近年完成的类似项目情况表'),'未知标签')
        self.assertEqual(normalize('示例项目（一期）工程','10.示例项目（一期）工程投标人近年完成的类似项目情况表'),'近年类似工程业绩证明')

    def test_field_placement_normalization_preserves_values(self):
        original=dict(doc_category='授权委托书',person_name='测试姓名',id_card='123',date='不推断其用途')
        obj,changes=QwenVLBackend.normalize_recovery_metadata(original)
        self.assertEqual(obj['extracted_kv'],dict(person_name='测试姓名',id_card='123'))
        self.assertNotIn('extracted_kv',original)
        self.assertEqual(len(changes),2)

    def test_numeric_uppercase_amount_does_not_break_ledger(self):
        from src.rag.ledger import EntityLedgerBuilder
        from src.rag.parser import BidChunk
        chunks=[BidChunk.from_dict(dict(chunk_id=str(i),parent_id=str(i),document_id='test',
                      is_parent=True,text='字段类型检查',extracted_kv={'bid_price_upper':value}))
                for i,value in enumerate([123,'壹佰元整'])]
        ledger=EntityLedgerBuilder.build_from_chunks('test',chunks)
        self.assertEqual(ledger.commercial_schedule_ledger['bid_price_upper'],'壹佰元整')
        self.assertTrue(all(isinstance(x.value,str) for x in ledger.field_mentions if x.field_name=='bid_price_upper'))

if __name__ == '__main__':
    unittest.main()

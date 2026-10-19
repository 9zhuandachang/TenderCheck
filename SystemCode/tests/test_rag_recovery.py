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

    def test_entity_attribution_blocks_third_party_and_historical_overwrites(self):
        from src.rag.ledger import EntityLedgerBuilder
        from src.rag.parser import BidChunk
        chunks = [
            BidChunk.from_dict(dict(
                chunk_id='p31', parent_id='p31', document_id='test', is_parent=True,
                section_path='一、商务文件 > （四）投标保证金', doc_category='投标保证金证明',
                text='银行保函 保证人统一社会信用代码：91330000THIRD_PARTY_BANK',
                extracted_kv={'credit_code': '91330000THIRD_PARTY_BANK'}
            )),
            BidChunk.from_dict(dict(
                chunk_id='p40', parent_id='p40', document_id='test', is_parent=True,
                section_path='一、商务文件 > （六）投标人基本情况表', doc_category='企业营业执照与资质证书',
                text='投标人基本情况表 统一社会信用代码：91330100BIDDER_REAL_CODE',
                extracted_kv={'credit_code': '91330100BIDDER_REAL_CODE', 'company_name': '测试投标建设集团有限公司'}
            )),
            BidChunk.from_dict(dict(
                chunk_id='p180', parent_id='p180', document_id='test', is_parent=True,
                section_path='一、商务文件 > （十）投标人近年完成的类似项目情况表', doc_category='近年类似工程业绩证明',
                text='近年完成的类似项目情况表 合同价格：叁亿捌仟玖佰万元整（¥389000000.00）',
                extracted_kv={'bid_price_upper': '叁亿捌仟玖佰万元整（¥389000000.00）', 'bid_price_yuan': '389000000'}
            )),
            BidChunk.from_dict(dict(
                chunk_id='p565', parent_id='p565', document_id='test', is_parent=True,
                section_path='三、报价文件 > （一）报价文件投标函', doc_category='报价文件与工程量清单',
                text='报价文件投标函 投标总报价大写：壹亿陆仟陆佰玖拾叁万肆仟捌佰伍拾柒元整',
                extracted_kv={'bid_price_upper': '壹亿陆仟陆佰玖拾叁万肆仟捌佰伍拾柒元整', 'bid_price_yuan': '166934857'}
            )),
        ]
        ledger = EntityLedgerBuilder.build_from_chunks('test', chunks)
        self.assertEqual(ledger.company_profile['credit_code'], '91330100BIDDER_REAL_CODE')
        self.assertEqual(ledger.commercial_schedule_ledger['bid_price_upper'], '壹亿陆仟陆佰玖拾叁万肆仟捌佰伍拾柒元整')
        self.assertEqual(ledger.commercial_schedule_ledger['bid_price_yuan'], '166934857')

if __name__ == '__main__':
    unittest.main()


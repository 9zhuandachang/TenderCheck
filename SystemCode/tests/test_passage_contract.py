"""The downstream evidence boundary must not silently expose ledger facts."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.parser import BidDocumentParser
from src.rag.passages import passage_to_dict, source_registry
from src.rag.vision import BaseVisionBackend, PageVisionResult


class PassageContractTests(unittest.TestCase):
    def test_parser_ignores_business_fields_from_legacy_vision_cache(self):
        class CachedPage(BaseVisionBackend):
            def analyze_page(self, doc, pdf_page, **kwargs):
                return PageVisionResult(pdf_page=pdf_page,ocr_text='工期为365日历天。',
                                        extracted_kv={'duration_days':360})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bid.pdf'
            with fitz.open() as pdf:
                page=pdf.new_page()
                page.insert_text((50,50),'投标函',fontname='china-s')
                pdf.save(path)
            parents,children,_=BidDocumentParser(vision_backend=CachedPage()).parse_pdf(path)
            self.assertTrue(parents)
            self.assertIn('365',parents[0].text)
            self.assertTrue(all(not c.extracted_kv for c in parents+children))

    def test_sources_preserve_physical_pages_and_pdf_identity_across_sections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bid.pdf'
            with fitz.open() as pdf:
                for i in range(2):
                    page = pdf.new_page()
                    page.insert_text((50, 50), 'Evidence page ' + str(i + 1))
                pdf.save(path)
            parser = BidDocumentParser()
            first, _, _ = parser.parse_pdf(path, document_id='part1', start_page=1, end_page=1)
            second, _, _ = parser.parse_pdf(path, document_id='part2', start_page=2, end_page=2)
            self.assertEqual(second[0].page_start, 2)
            parents = {p.chunk_id: p for p in first + second}
            sources = source_registry(parser.document_sources, parents)
            self.assertEqual(sources['part1']['sha256'], sources['part2']['sha256'])
            self.assertEqual(sources['part2']['page_count'], 2)
            self.assertEqual(sources['part2']['path'], str(path.resolve()))

    def test_serializer_only_exposes_passages_and_citations(self):
        parent = SimpleNamespace(document_id='bid', chunk_id='p1', page_start=4, page_end=5,
                                 doc_category='letter', section_path='chapter', text='Original context',
                                 extracted_kv={'duration': 'wrong value'}, has_signature=True)
        child = SimpleNamespace(chunk_id='c1', page_start=4, page_end=4, text='Original excerpt')
        hit = SimpleNamespace(rank=1, score=.5, parent_chunk=parent, child_chunk=child)
        output = passage_to_dict(hit, {'bid': {'path':'bid.pdf', 'sha256':'file'}})
        self.assertEqual(output['parent_text'], 'Original context')
        self.assertEqual(output['page_end'], 5)
        self.assertNotIn('extracted_kv', output)
        self.assertNotIn('has_signature', output)
        self.assertNotIn('ledger', output)


if __name__ == '__main__':
    unittest.main()

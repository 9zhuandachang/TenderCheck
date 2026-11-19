"""Unit tests for Phase 2.2: Tender segmenter table isolation, main-tender OCR fallback, and atomic clause decomposition."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pymupdf
from PIL import Image

SYSTEMCODE_DIR = Path(__file__).resolve().parents[1]
if str(SYSTEMCODE_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMCODE_DIR))

from src.extraction.atomic_decomposer import decompose_compound_requirement
from src.extraction.auto_annotator import _segment_single_pdf_with_ocr_fallback
from src.extraction.tender_segmenter import segment_tender_pdf


def _draw_table(page: pymupdf.Page, x0: float, y0: float, col_widths: list[float], rows: list[list[str]], row_h: float = 28.0) -> None:
    """Draw a bordered table with Chinese text so PyMuPDF `find_tables()` detects it cleanly."""
    total_w = sum(col_widths)
    total_h = row_h * len(rows)
    # Horizontal grid lines
    for r in range(len(rows) + 1):
        y = y0 + r * row_h
        page.draw_line((x0, y), (x0 + total_w, y), width=0.8)
    # Vertical grid lines
    cur_x = x0
    for w in col_widths:
        page.draw_line((cur_x, y0), (cur_x, y0 + total_h), width=0.8)
        cur_x += w
    page.draw_line((cur_x, y0), (cur_x, y0 + total_h), width=0.8)
    # Cell text
    for r_idx, row in enumerate(rows):
        cx = x0
        for c_idx, cell_txt in enumerate(row):
            if cell_txt:
                rect = pymupdf.Rect(cx + 3, y0 + r_idx * row_h + 4, cx + col_widths[c_idx] - 3, y0 + (r_idx + 1) * row_h - 2)
                page.insert_textbox(rect, cell_txt, fontsize=9, fontname="china-s")
            cx += col_widths[c_idx]


class SegmenterAndDecomposerTests(unittest.TestCase):
    def test_cross_table_headers_reset_between_distinct_tables_and_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "tables_isolation.pdf"
            doc = pymupdf.open()

            # Page 1: Chapter 2 table
            p1 = doc.new_page()
            p1.insert_text((40, 40), "第二章 投标人须知前附表", fontsize=12, fontname="china-s")
            _draw_table(
                p1,
                40,
                60,
                [80, 120, 300],
                [
                    ["条款号", "条款名称", "编列内容"],
                    ["1.4.1", "投标人资质条件", "须具备水利水电工程施工总承包一级资质。"],
                ],
            )
            # Second distinct table on same page with blank col0 on first data row -> must NOT inherit "1.4.1"
            _draw_table(
                p1,
                40,
                220,
                [100, 400],
                [
                    ["", "投标有效期为120日历天。"],
                ],
            )

            # Page 2: New chapter table with blank col0 -> must NOT inherit previous chapter's col0
            p2 = doc.new_page()
            p2.insert_text((40, 40), "第三章 评标办法", fontsize=12, fontname="china-s")
            _draw_table(
                p2,
                40,
                60,
                [80, 120, 300],
                [
                    ["", "", "水利建设市场信用等级AAA得3分。"],
                ],
            )

            doc.save(str(pdf_path))
            doc.close()

            cands = segment_tender_pdf(pdf_path)
            validity_cands = [c for c in cands if "120日历天" in c.text]
            credit_cands = [c for c in cands if "AAA得3分" in c.text]
            self.assertTrue(validity_cands)
            self.assertTrue(credit_cands)
            self.assertNotIn("1.4.1", validity_cands[0].text)
            self.assertNotIn("投标人资质条件", validity_cands[0].text)
            self.assertNotIn("1.4.1", credit_cands[0].text)
            self.assertNotIn("投标人资质条件", credit_cands[0].text)

    def test_cross_page_table_continuation_preserves_merged_col0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "table_continuation.pdf"
            doc = pymupdf.open()

            p1 = doc.new_page()
            p1.insert_text((40, 40), "第二章 投标人须知前附表", fontsize=12, fontname="china-s")
            _draw_table(
                p1,
                40,
                60,
                [80, 120, 300],
                [
                    ["条款号", "条款名称", "编列内容"],
                    ["3.4.1", "投标保证金", "投标保证金金额为人民币50万元。"],
                ],
            )

            # Direct continuation on Page 2 (same chapter, same 3 columns, no header row, blank col0/col1)
            p2 = doc.new_page()
            _draw_table(
                p2,
                40,
                60,
                [80, 120, 300],
                [
                    ["", "", "现金保证金须从投标人基本存款账户转出。"],
                ],
            )

            doc.save(str(pdf_path))
            doc.close()

            cands = segment_tender_pdf(pdf_path)
            cont_cands = [c for c in cands if "基本存款账户转出" in c.text]
            self.assertTrue(cont_cands)
            self.assertIn("投标保证金", cont_cands[0].text)

    def test_main_tender_scanned_pages_and_mixed_pdf_use_ocr_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "mixed_tender.pdf"
            doc = pymupdf.open()

            # Page 1: Native text page
            p1 = doc.new_page()
            p1.insert_text(
                (50, 72),
                "第一章 招标公告\n2.1 计划工期为180日历天。\n",
                fontsize=11,
                fontname="china-s",
            )

            # Page 2: Image-only scanned page (no native text layer)
            p2 = doc.new_page()
            img_buf = io.BytesIO()
            Image.new("RGB", (200, 100), color=(255, 255, 255)).save(img_buf, format="PNG")
            p2.insert_image(pymupdf.Rect(50, 50, 250, 150), stream=img_buf.getvalue())

            doc.save(str(pdf_path))
            doc.close()

            called_pages = []

            class _MockOCR:
                def analyze_page(self, _doc, page_no, existing_text="", heading_hint=""):
                    called_pages.append(page_no)
                    return SimpleNamespace(
                        ocr_text="第二章 投标人须知\n3.1 投标人须具有水利水电工程施工总承包二级及以上资质。"
                    )

            cands = _segment_single_pdf_with_ocr_fallback(
                pdf_path, is_addendum=False, ocr_backend=_MockOCR()
            )
            self.assertEqual(called_pages, [2])
            self.assertTrue(any("180日历天" in c.text and c.page == 1 for c in cands))
            self.assertTrue(any("水利水电工程施工总承包二级" in c.text and c.page == 2 for c in cands))

    def test_decompose_enterprise_and_pm_compound_clauses_preserving_or_logic(self) -> None:
        # 1. Highway/General enterprise qualification compound (4 clauses)
        ent = (
            "投标人资质要求：具有独立法人资格，具备公路工程施工总承包三级及以上资质，"
            "具有有效的安全生产许可证，并在人员、设备、资金等方面具有相应的施工能力。"
        )
        ent_atoms = decompose_compound_requirement(ent)
        self.assertEqual(len(ent_atoms), 4)
        self.assertTrue(all(a.startswith("投标人") for a in ent_atoms))
        self.assertIn("独立法人资格", ent_atoms[0])
        self.assertIn("公路工程施工总承包三级及以上资质", ent_atoms[1])
        self.assertIn("安全生产许可证", ent_atoms[2])

        # 2. Project manager compound with inline '且在投标单位注册' AND an OR-alternative that MUST NOT split
        pm = (
            "项目经理（项目负责人）资格要求：须具备公路工程专业一级注册建造师资格且在投标单位注册，"
            "具备有效的安全生产考核合格证书，目前未在其他项目上任职，"
            "或虽在其他项目上任职但本项目中标后能够从原项目撤离。"
        )
        pm_atoms = decompose_compound_requirement(pm)
        self.assertEqual(len(pm_atoms), 4)
        self.assertTrue(all(a.startswith("项目经理") for a in pm_atoms))
        self.assertIn("一级注册建造师", pm_atoms[0])
        self.assertIn("在投标单位注册", pm_atoms[1])
        self.assertIn("安全生产考核合格证书", pm_atoms[2])
        # OR-alternative MUST stay together in one atomic clause!
        self.assertIn("目前未在其他项目上任职", pm_atoms[3])
        self.assertIn("或虽在其他项目上任职", pm_atoms[3])

        # 3. Water Conservancy compound clause
        water_pm = (
            "拟派项目经理须具有水利水电工程专业一级注册建造师证书，"
            "持水行政主管部门颁发的B类安全生产考核合格证书，且为投标人本单位人员。"
        )
        water_atoms = decompose_compound_requirement(water_pm)
        self.assertEqual(len(water_atoms), 3)
        self.assertTrue(all(a.startswith("拟派项目经理") for a in water_atoms))

    def test_decomposer_does_not_over_split_conditionals_scoring_or_performance(self) -> None:
        cases = [
            "如采用银行保函，须由投标人基本存款账户开户行出具不可撤销独立保函。",
            "水利建设市场信用等级AAA得3分、AA得2分、A得1分。",
            "拟派项目经理须于2019年1月1日以来完成1个合同价不低于7000万元、长度不少于15公里的水利工程施工业绩。",
            "投标人须满足下列条件之一：具备水利总承包一级，或具备市政总承包一级。",
        ]
        for clause in cases:
            with self.subTest(clause=clause):
                self.assertEqual(decompose_compound_requirement(clause), [clause])


if __name__ == "__main__":
    unittest.main()

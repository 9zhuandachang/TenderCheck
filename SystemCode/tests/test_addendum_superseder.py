"""
Unit tests for Phase 2.4: Deterministic Chinese Addendum & Clarification Superseding
(`src/extraction/addendum_superseder.py`).
"""

from __future__ import annotations

import unittest

from src.extraction.addendum_superseder import apply_addendum_superseding, parse_addendum_directive
from src.extraction.auto_annotator import AutoAnnotatedRequirement


class AddendumSupersederTests(unittest.TestCase):
    def test_modify_directive_supersedes_original_and_preserves_provenance(self) -> None:
        orig_price = AutoAnnotatedRequirement(
            ID="T0001",
            Page=12,
            Section="招标文件.pdf / 第二章 投标人须知前附表",
            Requirement_Type="Commercial",
            Requirement="3.2.4 最高投标限价为5000万元，投标报价不得超过最高投标限价。",
            doc_name="招标文件.pdf",
        )
        orig_bond = AutoAnnotatedRequirement(
            ID="T0002",
            Page=13,
            Section="招标文件.pdf / 第二章 投标人须知前附表",
            Requirement_Type="Commercial",
            Requirement="3.4.1 投标保证金金额为人民币50万元。",
            doc_name="招标文件.pdf",
        )
        add_price = AutoAnnotatedRequirement(
            ID="T0010",
            Page=1,
            Section="第1号补遗书.pdf / 答疑澄清",
            Requirement_Type="Commercial",
            Requirement="原招标文件第二章投标人须知前附表3.2.4项最高投标限价由5000万元修改为4800万元。",
            doc_name="第1号补遗书.pdf",
            is_addendum=True,
        )

        items, report = apply_addendum_superseding([orig_price, orig_bond, add_price])
        self.assertEqual(report.superseded_count, 1)
        self.assertEqual(report.active_count, 2)
        self.assertEqual(orig_price.lifecycle_status, "SUPERSEDED")
        self.assertEqual(orig_price.superseded_by, "T0010")
        self.assertEqual(orig_bond.lifecycle_status, "ACTIVE")
        self.assertEqual(add_price.lifecycle_status, "ACTIVE")
        self.assertEqual(add_price.supersedes_ids, ["T0001"])
        self.assertIn("4800万元", add_price.Requirement)
        self.assertIn("5000万元修改为4800万元", add_price.raw_text)

    def test_delete_directive_cancels_original_and_marks_directive_only(self) -> None:
        orig_audit = AutoAnnotatedRequirement(
            ID="T0003",
            Page=20,
            Section="招标文件.pdf / 第三章 评标办法",
            Requirement_Type="Document",
            Requirement="投标人须提供近3年经审计的财务审计报告。",
            doc_name="招标文件.pdf",
        )
        add_delete = AutoAnnotatedRequirement(
            ID="T0011",
            Page=1,
            Section="答疑澄清公告.pdf / 答疑澄清",
            Requirement_Type="Document",
            Requirement="删除原招标文件第三章关于近3年财务审计报告的要求，投标人无需提供财务审计报告。",
            doc_name="答疑澄清公告.pdf",
            is_addendum=True,
        )

        _, report = apply_addendum_superseding([orig_audit, add_delete])
        self.assertEqual(report.cancelled_count, 1)
        self.assertEqual(report.directive_only_count, 1)
        self.assertEqual(report.active_count, 0)
        self.assertEqual(orig_audit.lifecycle_status, "CANCELLED")
        self.assertEqual(orig_audit.superseded_by, "T0011")
        self.assertEqual(add_delete.lifecycle_status, "DIRECTIVE_ONLY")
        self.assertEqual(add_delete.supersedes_ids, ["T0003"])

    def test_chained_multi_round_addenda_resolve_in_order(self) -> None:
        orig_dur = AutoAnnotatedRequirement(
            ID="T0001",
            Page=5,
            Section="水利招标文件.pdf / 第二章 投标人须知前附表",
            Requirement_Type="Schedule",
            Requirement="1.3.2 计划工期：360日历天。",
            doc_name="水利招标文件.pdf",
        )
        # Intentionally pass Addendum #2 BEFORE Addendum #1 to verify round-order sorting
        add_2 = AutoAnnotatedRequirement(
            ID="T0020",
            Page=1,
            Section="第2号补遗书.pdf / 答疑澄清",
            Requirement_Type="Schedule",
            Requirement="将第1号补遗书中的计划工期由300日历天调整为270日历天。",
            doc_name="第2号补遗书.pdf",
            is_addendum=True,
        )
        add_1 = AutoAnnotatedRequirement(
            ID="T0010",
            Page=1,
            Section="第1号补遗书.pdf / 答疑澄清",
            Requirement_Type="Schedule",
            Requirement="原招标文件1.3.2项计划工期由360日历天变更为300日历天。",
            doc_name="第1号补遗书.pdf",
            is_addendum=True,
        )

        _, report = apply_addendum_superseding([orig_dur, add_2, add_1])
        self.assertEqual(report.superseded_count, 2)
        self.assertEqual(report.active_count, 1)
        self.assertEqual(orig_dur.lifecycle_status, "SUPERSEDED")
        self.assertEqual(orig_dur.superseded_by, "T0010")
        self.assertEqual(add_1.lifecycle_status, "SUPERSEDED")
        self.assertEqual(add_1.superseded_by, "T0020")
        self.assertEqual(add_2.lifecycle_status, "ACTIVE")
        self.assertIn("270日历天", add_2.Requirement)

    def test_supplement_and_ambiguous_or_cross_chapter_guards(self) -> None:
        pm_1 = AutoAnnotatedRequirement(
            ID="T0001",
            Page=10,
            Section="招标文件.pdf / 第三章 评标办法",
            Requirement_Type="Qualification",
            Requirement="项目经理须具备水利水电工程专业二级建造师注册证书。",
            doc_name="招标文件.pdf",
        )
        pm_2 = AutoAnnotatedRequirement(
            ID="T0002",
            Page=11,
            Section="招标文件.pdf / 第三章 评标办法",
            Requirement_Type="Qualification",
            Requirement="项目经理须持有水安B类安全生产考核合格证且无在建工程。",
            doc_name="招标文件.pdf",
        )
        # Ambiguous modify with no clause number and no old value matching two PM clauses -> must NOT silently delete
        ambiguous_add = AutoAnnotatedRequirement(
            ID="T0010",
            Page=1,
            Section="第1号补遗.pdf / 答疑澄清",
            Requirement_Type="Qualification",
            Requirement="关于项目经理的有关要求修改为以最新行业规定为准。",
            doc_name="第1号补遗.pdf",
            is_addendum=True,
        )
        # Cross-chapter guard: Chapter 2 directive must NOT touch Chapter 3 clause
        wrong_chapter_add = AutoAnnotatedRequirement(
            ID="T0011",
            Page=1,
            Section="第1号补遗.pdf / 答疑澄清",
            Requirement_Type="Qualification",
            Requirement="原招标文件第二章中关于项目经理的要求变更为一级建造师。",
            doc_name="第1号补遗.pdf",
            is_addendum=True,
        )
        # Supplement directive: adds new active clause without touching old ones
        supp_add = AutoAnnotatedRequirement(
            ID="T0012",
            Page=2,
            Section="第1号补遗.pdf / 答疑澄清",
            Requirement_Type="Document",
            Requirement="补充条款：投标人须额外提供无拖欠农民工工资承诺书。",
            doc_name="第1号补遗.pdf",
            is_addendum=True,
        )

        _, report = apply_addendum_superseding([pm_1, pm_2, ambiguous_add, wrong_chapter_add, supp_add])
        self.assertEqual(report.superseded_count, 0)
        self.assertEqual(report.unmatched_addenda_count, 2)
        self.assertEqual(pm_1.lifecycle_status, "ACTIVE")
        self.assertEqual(pm_2.lifecycle_status, "ACTIVE")
        self.assertEqual(supp_add.lifecycle_status, "ACTIVE")
        self.assertIn("人工复核", ambiguous_add.superseding_note)
        self.assertIn("补遗新增", supp_add.superseding_note)

    def test_dict_items_and_clause_number_filtering(self) -> None:
        # Ensure amounts like 5000.00万元 or dates like 2026.05 are not misparsed as clause numbers
        d = parse_addendum_directive("最高投标限价由5000.00万元修改为4600.50万元，开标时间为2026.05。")
        self.assertEqual(d.action, "MODIFY")
        self.assertEqual(d.target_clause_nos, [])
        self.assertIn("MAX_BID_PRICE", d.target_topics)

        dict_items = [
            {
                "ID": "D1",
                "Section": "招标文件 / 第二章",
                "Requirement": "投标保证金金额为80万元。",
            },
            {
                "ID": "D2",
                "Section": "第1号补遗 / 答疑澄清",
                "Requirement": "原招标文件第二章投标保证金由80万元调整为50万元。",
            },
        ]
        _, report = apply_addendum_superseding(dict_items)
        self.assertEqual(report.superseded_count, 1)
        self.assertEqual(dict_items[0]["lifecycle_status"], "SUPERSEDED")
        self.assertEqual(dict_items[0]["superseded_by"], "D2")
        self.assertEqual(dict_items[1]["lifecycle_status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()

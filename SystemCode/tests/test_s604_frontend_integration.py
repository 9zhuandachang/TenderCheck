"""Integration test verifying S604 full 359-clause results + real backend engine in the Streamlit frontend."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class S604FrontendIntegrationTest(unittest.TestCase):
    def setUp(self):
        from tcui import config, real_backend, service, store

        self.config = config
        self.real_backend = real_backend
        self.service = service
        self.store = store
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tendercheck_s604_ui_")
        self.addCleanup(self.temp_dir.cleanup)
        data_dir = Path(self.temp_dir.name)
        for name, value in (
            ("DATA_DIR", data_dir),
            ("DB_PATH", data_dir / "tendercheck.db"),
            ("PROJECTS_DIR", data_dir / "projects"),
        ):
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        st.cache_resource.clear()
        self.addCleanup(st.cache_resource.clear)
        store.init_db()

    def test_s604_full359_real_backend_in_frontend(self):
        pid = self.real_backend.create_s604_project()
        project = self.store.get_project(pid)
        self.assertIsNotNone(project)
        self.assertTrue(self.real_backend.is_s604_project(project))

        # Check readiness (all 6 readiness checks must pass, including >2000 pages)
        checks = self.service.review_readiness(pid)
        self.assertTrue(all(c["ok"] for c in checks), checks)

        # Check round summary matches v3 replay + live ComplianceRuleEngine numbers
        rnd = self.store.latest_round(pid)
        self.assertIsNotNone(rnd)
        summary = self.service.round_summary(rnd["id"])
        self.assertEqual(summary["total"], 359)
        self.assertEqual(summary["applicable"], 359)
        self.assertEqual(summary["compliant"], 289)
        self.assertEqual(summary["non_compliant"], 0)
        self.assertEqual(summary["insufficient"], 70)
        self.assertTrue(summary["is_real_backend"])

        sub_counts = summary["sub_counts"]
        self.assertEqual(sub_counts.get("COMPLIANT"), 286)
        self.assertEqual(sub_counts.get("SCORED"), 3)
        self.assertEqual(sub_counts.get("CONFLICTING_EVIDENCE"), 1)
        self.assertEqual(sub_counts.get("SEAL_SIGNATURE_CHECK"), 22)
        self.assertEqual(sub_counts.get("EXTERNAL_WEB_CHECK"), 18)
        self.assertEqual(sub_counts.get("SUBJECTIVE_SCORING"), 9)

        # Verify T0056 has both conflict evidence pages and real backend chain
        results_by_clause = {r["clause_no"]: r for r in self.store.list_results(rnd["id"])}
        t0056 = results_by_clause["T0056"]
        self.assertEqual(t0056["chain"]["sub_verdict"], "CONFLICTING_EVIDENCE")
        self.assertGreaterEqual(len(t0056["evidence"]), 2)

        # Verify live ComplianceRuleEngine re-evaluation on reviewer correction (e.g. T0001 numeric bound)
        t0001 = results_by_clause["T0001"]
        self.assertEqual(t0001["verdict"], self.config.COMPLIANT)
        self.service.correct_result(
            t0001["id"],
            {**t0001["constraint"], "type": "numeric", "operator": "<=", "value": 360.0, "unit": "日历天"},
            400.0,
            "日历天",
            "Test exceeding duration threshold",
            "Tester",
            "Reviewer",
        )
        updated_t0001 = self.store.get_result(t0001["id"])
        self.assertEqual(updated_t0001["verdict"], self.config.NON_COMPLIANT)
        self.assertEqual(updated_t0001["corrected"], 1)

        # Verify Streamlit UI pages render the S604 project without any exception
        app = AppTest.from_file(str(APP_DIR / "main.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        app.switch_page("views/workspace.py").run()
        self.assertFalse(app.exception)
        app.switch_page("views/report.py").run()
        self.assertFalse(app.exception)

    def test_unannotated_chinese_project_end_to_end(self):
        import pymupdf as fitz

        pid = self.store.create_project("New Unannotated Chinese Highway Project", is_demo=False)
        proj_dir = self.store.project_dir(pid)

        tender_pdf = proj_dir / "chinese_tender.pdf"
        tdoc = fitz.open()
        tpage = tdoc.new_page()
        tpage.insert_text(
            (50, 72),
            "第一章 招标公告\n"
            "2.8 计划工期：计划工期为360日历天。\n"
            "3.1 投标人须具有独立法人资格，持有有效的营业执照。\n"
            "3.2 投标人在信用中国网站不得被列为失信被执行人。\n"
            "第九章 投标文件格式\n"
            "一、投标函\n"
            "投标人：______（盖单位章）\n"
            "法定代表人或其委托代理人：______（签字）\n",
            fontsize=11,
            fontname="china-s",
        )
        tender_pdf.write_bytes(tdoc.tobytes())
        tdoc.close()

        bid_pdf = proj_dir / "chinese_bid.pdf"
        bdoc = fitz.open()
        bpage = bdoc.new_page()
        bpage.insert_text(
            (50, 72),
            "商务文件\n"
            "一、投标函\n"
            "我方已仔细研究招标文件的全部内容，愿意按合同约定实施和完成承包工程。\n"
            "工期：360日历天。\n"
            "工程质量：交工验收的质量评定：合格。\n"
            "我方承诺在招标文件规定的投标有效期内不撤销投标文件。\n"
            "投标诚信承诺书\n"
            "我单位在参加组织的工程投标活动中郑重承诺：我方申报的所有资料都是真实、准确、完整的；\n",
            fontsize=11,
            fontname="china-s",
        )
        bid_pdf.write_bytes(bdoc.tobytes())
        bdoc.close()

        self.real_backend._register_external_pdf(pid, "tender", "招标文件正文.pdf", tender_pdf)
        self.real_backend._register_external_pdf(pid, "bid", "投标文件.pdf", bid_pdf)

        counts = self.service._extract_job(lambda *a: None, pid)
        self.assertGreater(counts["requirements"], 0)
        self.assertTrue((proj_dir / "auto_annotations.json").exists())


if __name__ == "__main__":
    unittest.main()

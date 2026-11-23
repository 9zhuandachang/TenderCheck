"""End-to-end service test for the review workspace on the canonical Chinese backend trunk."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "app"
sys.path.insert(0, str(APP_DIR))

from tcui import config, engine, export, service, store  # noqa: E402
from tcui.config import CLOSED, COMPLIANT, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE  # noqa: E402


def _make_pdf_bytes(pages: list[str]) -> bytes:
    doc = pymupdf.open()
    for txt in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 80), txt, fontsize=11, fontname="china-ss")
    data = doc.tobytes()
    doc.close()
    return data


def _verdicts(round_id):
    return {r["clause_no"]: r["verdict"] for r in store.list_results(round_id)}


class ReviewFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tendercheck_flow_")
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
        env_patcher = patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        store.init_db()

        self.pid = store.create_project("某水利防洪治理工程（闭环测试）")
        tender_pdf = _make_pdf_bytes([
            "第二章 投标人须知前附表\n1.3.2 计划工期不超过360日历天。\n3.2.4 最高投标限价为5000万元，投标报价不得超过最高投标限价。\n3.5.1 投标有效期不少于90日历天。\n4.1.1 投标函须加盖投标人单位公章。"
        ])
        bid_v1_pdf = _make_pdf_bytes([
            "投标函\n我方承诺本工程计划工期为400日历天，投标有效期为120日历天，投标总报价为4850万元。\n投标人：（盖单位章）"
        ])
        self.bid_v2_bytes = _make_pdf_bytes([
            "投标函\n我方承诺本工程计划工期为350日历天，投标有效期为120日历天，投标总报价为4850万元。\n投标人：（盖单位章）"
        ])

        tdoc_id, _ = service.add_document(self.pid, "tender", "招标文件.pdf", tender_pdf, background=False)
        service.add_document(self.pid, "bid", "投标文件.pdf", bid_v1_pdf, background=False)

        self.req_dur = service.add_requirement(
            self.pid, "T0001", "计划工期不超过380日历天。", "Schedule", tdoc_id, 1, "导入", "Ann", "Reviewer"
        )
        # Attach a pending addendum amendment tightening duration to <= 360 days
        store.update(
            "requirements",
            self.req_dur,
            {
                "amendment_text": "计划工期不超过360日历天。",
                "amendment_status": "pending",
            },
        )
        service.add_requirement(
            self.pid, "T0002", "最高投标限价为5000万元，投标报价不得超过最高投标限价。", "Commercial", tdoc_id, 1, "导入", "Ann", "Reviewer"
        )
        service.add_requirement(
            self.pid, "T0003", "投标有效期不少于90日历天。", "Commercial", tdoc_id, 1, "导入", "Ann", "Reviewer"
        )
        service.add_requirement(
            self.pid, "T0004", "投标函须加盖投标人单位公章。", "Document", tdoc_id, 1, "导入", "Ann", "Reviewer"
        )

    def test_full_review_loop(self):
        pid = self.pid
        self.assertFalse(all(c["ok"] for c in service.review_readiness(pid)))
        with self.assertRaises(service.ServiceError):
            service.confirm_requirements(pid, "Ann", "Reviewer")  # pending addendum blocks confirm

        for req in store.pending_amendments(pid):
            service.decide_amendment(req["id"], True, "Ann", "Reviewer")
        service.confirm_requirements(pid, "Ann", "Reviewer")
        self.assertTrue(all(c["ok"] for c in service.review_readiness(pid)), service.review_readiness(pid))

        # Round 1 on Bid v1 (400 days > 360 -> Non-compliant; 60w >= 50w -> Compliant; 120d >= 90d -> Compliant; seal unverified -> Insufficient)
        r1 = service._review_job(service._no_progress, pid)["round_id"]
        v1 = _verdicts(r1)
        self.assertEqual(v1["T0001"], NON_COMPLIANT)
        self.assertEqual(v1["T0002"], COMPLIANT)
        self.assertEqual(v1["T0003"], COMPLIANT)
        self.assertEqual(v1["T0004"], INSUFFICIENT)

        results = {r["clause_no"]: r for r in store.list_results(r1)}
        service.send_to_remediation(results["T0001"]["id"], "工期超限需修改", "Ann", "Reviewer")
        service.send_to_remediation(results["T0004"]["id"], "公章待人工核验", "Ann", "Reviewer")

        # Under zero-false-pass governance (ADR-002), open defects & insufficient items block sign-off
        r1_blockers = service.signoff_blockers(r1)
        self.assertTrue(any("Non-compliant" in b for b in r1_blockers))
        self.assertTrue(any("Insufficient evidence" in b for b in r1_blockers))
        self.assertTrue(any("remediation" in b for b in r1_blockers))

        # Upload revised Bid v2 (duration fixed to 350 days) -> link_revised_bid moves remediations to TO_REINSPECT
        bid2, created = service.add_document(pid, "bid", "投标文件.pdf", self.bid_v2_bytes, background=False)
        self.assertTrue(created)
        self.assertEqual(service.link_revised_bid(pid, bid2, "Ann", "Reviewer"), 2)

        r2 = service._review_job(service._no_progress, pid)["round_id"]
        v2 = _verdicts(r2)
        self.assertEqual(v2["T0001"], COMPLIANT)
        self.assertEqual(v2["T0002"], COMPLIANT)
        self.assertEqual(v2["T0003"], COMPLIANT)
        self.assertEqual(v2["T0004"], INSUFFICIENT)

        changes = {r["clause_no"]: r["change"] for r in service.compare_rounds(r2)}
        self.assertEqual(changes["T0001"], "Resolved")

        rem = {i["clause_no"]: i["status"] for i in store.list_remediations(pid)}
        self.assertEqual(rem["T0001"], TO_REINSPECT)
        self.assertEqual(rem["T0004"], TO_REMEDIATE)

        res2 = {r["clause_no"]: r for r in store.list_results(r2)}
        self.assertEqual(service.confirm_result(res2["T0001"]["id"], "Ann", "Reviewer"), CLOSED)

        # Unresolved Insufficient T0004 still blocks sign-off until explicitly reviewed with written basis
        self.assertTrue(any("Insufficient evidence" in b for b in service.signoff_blockers(r2)))
        # Manual pass resolves T0004 and closes its open remediation item
        service.approve_manual_pass(
            res2["T0004"]["id"],
            "已目视核对投标函第1页单位红色公章清晰完整",
            "Ann",
            "Reviewer",
        )
        rem_after = {i["clause_no"]: i["status"] for i in store.list_remediations(pid)}
        self.assertEqual(rem_after["T0004"], CLOSED)
        self.assertEqual(service.signoff_blockers(r2), [])

        with self.assertRaises(service.ServiceError):
            service.sign_round(r2, "Ann", "", "Reviewer")
        service.sign_round(r2, "Ben", "全部条款复核通过", "Manager")
        with self.assertRaises(service.ServiceError):
            service.confirm_result(res2["T0004"]["id"], "Ann", "Reviewer")  # signed rounds are read-only

        self.assertTrue(export.excel_bytes(r2).startswith(b"PK"))
        self.assertTrue(export.pdf_bytes(r2).startswith(b"%PDF"))

    def test_correction_reruns_rule(self):
        constraint = {"type": "numeric", "field": "计划工期", "operator": "<=", "value": 330, "unit": "日历天"}
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 47, "unit": "周"})["verdict"], COMPLIANT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 48, "unit": "周"})["verdict"], NON_COMPLIANT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": None})["verdict"], INSUFFICIENT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 11, "unit": "个月"})["verdict"], INSUFFICIENT)

        money = {"type": "numeric", "field": "投标保证金", "operator": ">=", "value": 500000, "unit": "元"}
        self.assertEqual(engine.evaluate_rule(money, {"value": 50, "unit": "万元"})["verdict"], COMPLIANT)
        self.assertEqual(engine.evaluate_rule(money, {"value": 45, "unit": "万元"})["verdict"], NON_COMPLIANT)


if __name__ == "__main__":
    unittest.main()

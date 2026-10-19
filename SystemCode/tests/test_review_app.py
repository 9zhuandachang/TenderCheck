"""End-to-end service test for the review workspace on the synthetic demo package."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "src" / "app"
sys.path.insert(0, str(APP_DIR))
os.environ["TENDERCHECK_DATA_DIR"] = tempfile.mkdtemp(prefix="tendercheck_test_")

from tcui import config, demo, engine, export, service, store  # noqa: E402
from tcui.config import CLOSED, COMPLIANT, INSUFFICIENT, NON_COMPLIANT, TO_REINSPECT, TO_REMEDIATE  # noqa: E402

EXPECTED_ROUND_1 = {
    "1.2": COMPLIANT, "1.3": COMPLIANT, "2.1": COMPLIANT, "2.2": COMPLIANT,
    "3.1": NON_COMPLIANT,  # 345 days against the amended 330-day limit
    "3.2": COMPLIANT, "4.1": COMPLIANT,
    "4.2": NON_COMPLIANT, "5.1": NON_COMPLIANT, "5.2": COMPLIANT,
    "5.3": INSUFFICIENT,  # only a partial mention of site safety
    "5.4": NON_COMPLIANT,  # traffic management plan missing from a fully parsed bid
    "DWG C-102": NON_COMPLIANT, "DWG C-104": INSUFFICIENT, "DWG C-105": NON_COMPLIANT,
}


def _verdicts(round_id):
    return {r["clause_no"]: r["verdict"] for r in store.list_results(round_id)}


class ReviewFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_db()
        cls.pid = demo.create_demo_project()

    def test_full_review_loop(self):
        pid = self.pid
        self.assertFalse(all(c["ok"] for c in service.review_readiness(pid)))
        with self.assertRaises(service.ServiceError):
            service.confirm_requirements(pid, "Ann", "Reviewer")  # addendum changes still pending
        for req in store.pending_amendments(pid):
            service.decide_amendment(req["id"], True, "Ann", "Reviewer")
        service.confirm_requirements(pid, "Ann", "Reviewer")
        self.assertTrue(all(c["ok"] for c in service.review_readiness(pid)), service.review_readiness(pid))

        r1 = service._review_job(service._no_progress, pid)["round_id"]
        self.assertEqual(_verdicts(r1), EXPECTED_ROUND_1)

        results = {r["clause_no"]: r for r in store.list_results(r1)}
        self.assertEqual(results["5.4"]["evidence"], [])  # a missing document cites no evidence
        for clause, res in results.items():
            if res["verdict"] == COMPLIANT:
                service.confirm_result(res["id"], "Ann", "Reviewer")
        for clause in ("3.1", "4.2", "5.1", "5.3", "5.4", "DWG C-102", "DWG C-105"):
            service.send_to_remediation(results[clause]["id"], "", "Ann", "Reviewer")
        service.mark_not_applicable(results["DWG C-104"]["id"], "Clarification requested from the Employer",
                                    "Ann", "Reviewer")
        self.assertEqual(service.signoff_blockers(r1), [])

        # Revised bid moves remediation items to re-inspection.
        data = demo.revised_bid_path(pid).read_bytes()
        bid2, created = service.add_document(pid, "bid", "bid_v2.pdf", data, background=False)
        self.assertTrue(created)
        self.assertEqual(service.link_revised_bid(pid, bid2, "Ann", "Reviewer"), 7)
        r2 = service._review_job(service._no_progress, pid)["round_id"]
        v2 = _verdicts(r2)
        for clause in ("3.1", "4.2", "5.1", "5.3", "DWG C-102", "DWG C-105"):
            self.assertEqual(v2[clause], COMPLIANT, clause)
        self.assertEqual(v2["5.4"], NON_COMPLIANT)
        self.assertNotIn("DWG C-104", v2)  # marked not applicable

        changes = {r["clause_no"]: r["change"] for r in service.compare_rounds(r2)}
        self.assertEqual(changes["3.1"], "Resolved")
        self.assertEqual(changes["5.4"], "Persisting")

        rem = {i["clause_no"]: i["status"] for i in store.list_remediations(pid)}
        self.assertEqual(rem["3.1"], TO_REINSPECT)
        self.assertEqual(rem["5.4"], TO_REMEDIATE)
        res2 = {r["clause_no"]: r for r in store.list_results(r2)}
        self.assertEqual(res2["1.2"]["human_status"], config.CONFIRMED)  # carried forward, verdict unchanged
        for r in res2.values():
            if r["human_status"] == config.UNREVIEWED:
                service.confirm_result(r["id"], "Ann", "Reviewer")
        self.assertTrue(any("awaiting confirmation" in b for b in service.signoff_blockers(r2)))
        for clause in ("3.1", "4.2", "5.1", "5.3", "DWG C-102", "DWG C-105"):
            self.assertEqual(service.confirm_result(res2[clause]["id"], "Ann", "Reviewer"), CLOSED)
        self.assertEqual(service.signoff_blockers(r2), [])

        with self.assertRaises(service.ServiceError):
            service.sign_round(r2, "Ann", "", "Reviewer")
        service.sign_round(r2, "Ben", "5.4 disclosed as open risk", "Manager")
        with self.assertRaises(service.ServiceError):
            service.confirm_result(res2["5.4"]["id"], "Ann", "Reviewer")  # signed rounds are read-only

        self.assertTrue(export.excel_bytes(r2).startswith(b"PK"))
        self.assertTrue(export.pdf_bytes(r2).startswith(b"%PDF"))
        evaluation = service.evaluate_round(r1)
        self.assertEqual((evaluation["hits"], evaluation["known"]), (7, 8))

    def test_correction_reruns_rule(self):
        constraint = {"type": "numeric", "field": "Completion period", "operator": "<=", "value": 330, "unit": "days"}
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 47, "unit": "weeks"})["verdict"], COMPLIANT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 48, "unit": "weeks"})["verdict"], NON_COMPLIANT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": None})["verdict"], INSUFFICIENT)
        self.assertEqual(engine.evaluate_rule(constraint, {"value": 11, "unit": "months"})["verdict"], INSUFFICIENT)


if __name__ == "__main__":
    unittest.main()

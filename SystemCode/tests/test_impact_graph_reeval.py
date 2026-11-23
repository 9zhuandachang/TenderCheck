"""
Unit & integration tests for Phase 3.1:
Cross-Clause Impact Graph (`src/rules/impact_graph.py`) and Impact-Aware Cascade
Re-evaluation (`service.preview_page_impact` & `service.apply_remediation_edit`).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

SYSTEMCODE_DIR = Path(__file__).resolve().parents[1]
APP_DIR = SYSTEMCODE_DIR / "src" / "app"
for p in (str(SYSTEMCODE_DIR), str(APP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.rules.impact_graph import ClauseImpactGraph  # noqa: E402
from tcui import config, real_backend, service, store  # noqa: E402


def _create_two_page_bid_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 80), "投标函\n本工程计划工期为400日历天。", fontsize=12, fontname="china-ss")
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((50, 80), "投标保证金凭证\n投标保证金金额为60万元。", fontsize=12, fontname="china-ss")
    doc.save(str(path))
    doc.close()
    return path


class ImpactGraphAndCascadeReevalTests(unittest.TestCase):
    def test_impact_graph_indexes_evidence_and_backend_context_with_doc_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bid_path = str(Path(tmp) / "bid_main.pdf")
            doc_sources = {
                "bid_doc_1": {
                    "path": bid_path,
                    "filename": "bid_main.pdf",
                    "label": "商务标.pdf",
                }
            }
            results = [
                {
                    "id": 101,
                    "requirement_id": 1,
                    "clause_no": "T0001",
                    "category": "Schedule",
                    "verdict": config.NON_COMPLIANT,
                    "evidence": [{"page": 1, "doc_path": bid_path, "doc_label": "商务标.pdf"}],
                    "chain": {},
                },
                {
                    "id": 102,
                    "requirement_id": 2,
                    "clause_no": "T0002",
                    "category": "Schedule",
                    "verdict": config.COMPLIANT,
                    "evidence": [],
                    "chain": {
                        "backend_state": {
                            "evidence_context": [
                                {"evidence_id": "E1", "document_id": "bid_doc_1", "page_start": 1, "page_end": 2}
                            ]
                        }
                    },
                },
                {
                    "id": 103,
                    "requirement_id": 3,
                    "clause_no": "T0003",
                    "category": "Commercial",
                    "verdict": config.COMPLIANT,
                    "evidence": [{"page": 3, "doc_path": bid_path}],
                    "chain": {},
                },
            ]
            graph = ClauseImpactGraph(results, doc_sources=doc_sources)

            # Page 1 should link 101 and 102 (excluding 101 -> returns 102)
            imp_p1 = graph.impacted_results(bid_path, 1, exclude_result_id=101)
            self.assertEqual([r["clause_no"] for r in imp_p1], ["T0002"])

            # Page 2 should match 102 via backend_state page_start..page_end range
            imp_p2 = graph.impacted_results(bid_path, 2)
            self.assertEqual([r["clause_no"] for r in imp_p2], ["T0002"])

            # Page 3 should only match 103
            imp_p3 = graph.impacted_results(bid_path, 3)
            self.assertEqual([r["clause_no"] for r in imp_p3], ["T0003"])

    def test_apply_remediation_edit_cascades_fixes_and_catches_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "test_impact.db"
            bid_pdf = _create_two_page_bid_pdf(tmp_path / "bid.pdf")

            with patch.object(config, "DATA_DIR", tmp_path), \
                 patch.object(config, "DB_PATH", db_path), \
                 patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
                store.init_db()
                pid = store.insert(
                    "projects",
                    {
                        "name": "Impact Test Project",
                        "created_at": store.now(),
                    },
                )
                doc_id = store.insert(
                    "documents",
                    {
                        "project_id": pid,
                        "category": "bid",
                        "label": "商务标.pdf",
                        "filename": "bid.pdf",
                        "path": str(bid_pdf),
                        "sha256": "abc123",
                        "pages": 2,
                        "version": 1,
                        "selected": 1,
                        "parse_status": "done",
                        "uploaded_at": store.now(),
                    },
                )
                rnd_id = store.insert(
                    "rounds",
                    {
                        "project_id": pid,
                        "bid_doc_id": doc_id,
                        "no": 1,
                        "engine": config.ENGINE_NAME,
                        "rule_version": config.RULE_VERSION,
                        "status": "done",
                        "created_at": store.now(),
                    },
                )

                def _add_req(clause_no: str, category: str, text: str) -> int:
                    return store.insert(
                        "requirements",
                        {
                            "project_id": pid,
                            "clause_no": clause_no,
                            "category": category,
                            "text": text,
                            "applicable": 1,
                            "origin": "extracted",
                        },
                    )

                # Three clauses citing Page 1 (Bid Letter duration & validity) + one clause citing Page 2
                req1 = _add_req("T0001", "Schedule", "计划工期不超过360日历天。")
                req2 = _add_req("T0002", "Schedule", "计划工期不超过365日历天。")
                req3 = _add_req("T0003", "Commercial", "投标有效期不少于90日历天。")
                req4 = _add_req("T0004", "Commercial", "投标保证金不少于50万元。")

                def _make_ev(page: int, text: str) -> tuple[list[dict], dict]:
                    ev = [
                        {
                            "page": page,
                            "doc_path": str(bid_pdf),
                            "doc_label": "商务标.pdf",
                            "document_id": f"bid_doc_{doc_id}",
                            "snippet": text,
                            "rag_text": text,
                        }
                    ]
                    chain = {
                        "backend_state": {
                            "evidence_context": [
                                {
                                    "evidence_id": "E1",
                                    "document_id": f"bid_doc_{doc_id}",
                                    "source_sha256": "abc123",
                                    "page_start": page,
                                    "page_end": page,
                                    "text": text,
                                }
                            ]
                        }
                    }
                    return ev, chain

                ev_p1, ch_p1 = _make_ev(1, "投标函：本工程计划工期为400日历天，投标有效期为120日历天。")
                ev_p2, ch_p2 = _make_ev(2, "投标保证金金额为60万元。")

                res1 = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req1,
                        "verdict": config.NON_COMPLIANT,
                        "original_verdict": config.NON_COMPLIANT,
                        "rule_id": "R01_NUMERIC_BOUND",
                        "explanation": "400 > 360",
                        "evidence": ev_p1,
                        "chain": ch_p1,
                        "human_status": config.TO_REMEDIATE,
                        "updated_at": store.now(),
                    },
                )
                res2 = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req2,
                        "verdict": config.NON_COMPLIANT,
                        "original_verdict": config.NON_COMPLIANT,
                        "rule_id": "R01_NUMERIC_BOUND",
                        "explanation": "400 > 365",
                        "evidence": ev_p1,
                        "chain": ch_p1,
                        "human_status": config.TO_REMEDIATE,
                        "updated_at": store.now(),
                    },
                )
                res3 = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req3,
                        "verdict": config.COMPLIANT,
                        "original_verdict": config.COMPLIANT,
                        "rule_id": "R01_NUMERIC_BOUND",
                        "explanation": "400 >= 380",
                        "evidence": ev_p1,
                        "chain": ch_p1,
                        "human_status": config.CONFIRMED,
                        "updated_at": store.now(),
                    },
                )
                res4 = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req4,
                        "verdict": config.COMPLIANT,
                        "original_verdict": config.COMPLIANT,
                        "rule_id": "R01_NUMERIC_BOUND",
                        "explanation": "60 >= 50",
                        "evidence": ev_p2,
                        "chain": ch_p2,
                        "human_status": config.CONFIRMED,
                        "updated_at": store.now(),
                    },
                )

                rem1 = store.insert(
                    "remediations",
                    {
                        "project_id": pid,
                        "requirement_id": req1,
                        "issue": "工期超标",
                        "target": "商务标.pdf p.1",
                        "status": config.TO_REMEDIATE,
                        "draft": "",
                        "opened_round_id": rnd_id,
                        "updated_at": store.now(),
                    },
                )
                rem2 = store.insert(
                    "remediations",
                    {
                        "project_id": pid,
                        "requirement_id": req2,
                        "issue": "工期超标",
                        "target": "商务标.pdf p.1",
                        "status": config.TO_REMEDIATE,
                        "draft": "",
                        "opened_round_id": rnd_id,
                        "updated_at": store.now(),
                    },
                )

                # 1. Preview impact of editing Page 1 while focusing on T0001
                preview = service.preview_page_impact(rnd_id, str(bid_pdf), 1, exclude_result_id=res1)
                self.assertEqual(preview["count"], 2)
                self.assertEqual([c["clause_no"] for c in preview["clauses"]], ["T0002", "T0003"])

                # 2. Apply edit on Page 1: fix duration (400 -> 350) while accidentally shortening validity (120 -> 60)
                outcome = service.apply_remediation_edit(
                    rem1,
                    res1,
                    0,
                    "投标函：本工程计划工期为350日历天，投标有效期为60日历天。",
                    "调整工期至350天",
                    "Alice",
                    "Reviewer",
                    resolve=True,
                    doc_path=str(bid_pdf),
                    page_no=1,
                )

                # Primary clause T0001 is now Compliant & closed
                self.assertTrue(outcome["passed"])
                self.assertEqual(outcome["verdict"], config.COMPLIANT)
                self.assertEqual(outcome["impacted_count"], 2)
                self.assertEqual([c["clause_no"] for c in outcome["regressed_clauses"]], ["T0003"])

                # Sibling T0002 (<= 365 days) cascaded to Compliant & auto-closed its remediation
                r2_after = store.get_result(res2)
                self.assertEqual(r2_after["verdict"], config.COMPLIANT)
                self.assertEqual(r2_after["human_status"], config.CONFIRMED)
                rem2_after = store.query_one("SELECT * FROM remediations WHERE id=?", (rem2,))
                self.assertEqual(rem2_after["status"], config.CLOSED)

                # Sibling T0003 (>= 380 days) REGRESSED from Compliant -> Non-compliant & auto-opened remediation!
                r3_after = store.get_result(res3)
                self.assertEqual(r3_after["verdict"], config.NON_COMPLIANT)
                self.assertEqual(r3_after["human_status"], config.TO_REMEDIATE)
                rem3_after = store.query_one(
                    "SELECT * FROM remediations WHERE project_id=? AND requirement_id=?",
                    (pid, req3),
                )
                self.assertIsNotNone(rem3_after)
                self.assertEqual(rem3_after["status"], config.TO_REMEDIATE)
                self.assertIn("联动重审发现回归", rem3_after["issue"])

                # Unrelated Page 2 clause T0004 was NOT touched
                r4_after = store.get_result(res4)
                self.assertEqual(r4_after["verdict"], config.COMPLIANT)
                self.assertEqual(r4_after["corrected"], 0)

                # Audit trail recorded both cascade re-evaluations
                audit_rows = [
                    a for a in store.list_actions(pid)
                    if a.get("action") == "impact_reevaluate_clause"
                ]
                self.assertEqual(len(audit_rows), 2)


if __name__ == "__main__":
    unittest.main()

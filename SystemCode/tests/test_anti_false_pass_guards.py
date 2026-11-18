"""Regression tests enforcing strict zero-false-pass (严控假放行) guarantees across extraction, hydration, remediation, and UI workflow."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SYSTEMCODE_DIR = Path(__file__).resolve().parents[1]
APP_DIR = SYSTEMCODE_DIR / "src" / "app"
for p in (str(SYSTEMCODE_DIR), str(APP_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.extraction.evidence import ground_extraction  # noqa: E402
from src.extraction.global_context import (  # noqa: E402
    build_project_global_context,
    hydrate_with_global_context,
)
from src.extraction.llm_extractor import DeepSeekSlotExtractor  # noqa: E402
from src.extraction.schema import (  # noqa: E402
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules import ComplianceRuleEngine  # noqa: E402
from tcui import config, real_backend, service, store  # noqa: E402


def _make_passage(eid: str, text: str, page: int = 1, doc_id: str = "bid_doc_1") -> dict:
    return {
        "evidence_id": eid,
        "document_id": doc_id,
        "source_sha256": "deadbeef" * 8,
        "page_start": page,
        "page_end": page,
        "text": text,
    }


class AntiFalsePassGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ComplianceRuleEngine()

    def test_strict_fallback_never_fabricates_safety_fee_or_staffing_pass(self) -> None:
        passages = [
            _make_passage("E1", "安全生产费 150,000.00 元；项目管理机构：项目经理 王五 工程师；本工程合同履行承诺。"),
        ]
        ev_str = json.dumps(passages, ensure_ascii=False)

        # 1. R09 Safety fee formula clause without Chapter 100-700 base must NOT back-calculate denominator
        ext_r09 = real_backend._strict_conservative_fallback(
            "T0901",
            "安全生产费不低于第100章至第700章合计净基数的1.5%（元）。",
            "Commercial",
            ev_str,
            "2026-11-16",
        )
        rep_r09 = self.engine.evaluate(ext_r09)
        self.assertNotEqual(rep_r09.verdict, "COMPLIANT")
        self.assertEqual(rep_r09.verdict, "INSUFFICIENT_EVIDENCE")

        # 2. R07 Staffing clause must NOT fabricate dummy personnel records
        ext_r07 = real_backend._strict_conservative_fallback(
            "T0701",
            "项目经理1人（须具备公路工程专业一级注册建造师执业资格及有效B证）。",
            "Qualification",
            ev_str,
            "2026-11-16",
        )
        rep_r07 = self.engine.evaluate(ext_r07)
        self.assertNotEqual(rep_r07.verdict, "COMPLIANT")
        self.assertEqual(rep_r07.verdict, "INSUFFICIENT_EVIDENCE")

    def test_remediation_reevaluate_offline_blocks_arbitrary_text_fake_pass(self) -> None:
        p1 = _make_passage("E1", "拟投入项目管理机构人员表：项目经理 张三 二级建造师", page=10)
        ext = StructuredExtractionResult(
            clause_id="T0088",
            requirement_text="项目经理1人，须具备公路工程一级注册建造师资格。",
            category="Qualification",
            tender_slots=TenderConstraintSlots(
                constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
                required_role="project_manager",
                required_count=1,
                expected_text="一级注册建造师",
                required_domain="公路工程",
                review_reference_date="2026-11-16",
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                personnel_records=[
                    {
                        "role": "project_manager",
                        "name": "张三",
                        "qualification_text": "二级注册建造师",
                        "domain": "公路工程",
                        "valid_to": "2028-01-01",
                    }
                ],
            ),
        )
        existing_result = {
            "chain": {
                "is_real_backend": True,
                "backend_state": {
                    "extraction": ext.to_dict(),
                    "evidence_context": [p1],
                },
            }
        }
        req = {
            "clause_no": "T0088",
            "text": ext.requirement_text,
            "category": "Qualification",
            "project_id": None,
        }
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False), patch.object(
            real_backend, "_ensure_env_loaded", lambda: None
        ):
            out = real_backend.reevaluate_clause_with_modified_bid(
                req=req,
                existing_result=existing_result,
                pdf_path="",
                page_no=10,
                modified_text="随便修改一行文字测试是否会被假放行通过。",
            )
        self.assertNotEqual(out["verdict"], config.COMPLIANT)
        self.assertEqual(out["verdict"], config.INSUFFICIENT)

    def test_remediation_reevaluate_grounded_numeric_pass_and_fail(self) -> None:
        p1 = _make_passage("E1", "投标函：我方承诺工期为400日历天。", page=5)
        ext = StructuredExtractionResult(
            clause_id="T0020",
            requirement_text="计划工期不超过360日历天。",
            category="Schedule",
            tender_slots=TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="duration_days",
                operator="<=",
                expected_numeric=360.0,
                expected_unit="日历天",
                source_quotes={
                    "expected_numeric": "计划工期不超过360日历天",
                    "expected_unit": "计划工期不超过360日历天",
                },
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                actual_numeric=400.0,
                actual_unit="日历天",
            ),
        )
        existing_result = {
            "chain": {
                "is_real_backend": True,
                "backend_state": {
                    "extraction": ext.to_dict(),
                    "evidence_context": [p1],
                },
            }
        }
        req = {
            "clause_no": "T0020",
            "text": ext.requirement_text,
            "category": "Schedule",
            "project_id": None,
        }
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False), patch.object(
            real_backend, "_ensure_env_loaded", lambda: None
        ):
            # Still exceeding 360 days -> must fail
            out_fail = real_backend.reevaluate_clause_with_modified_bid(
                req=req,
                existing_result=existing_result,
                pdf_path="",
                page_no=5,
                modified_text="投标函：我方承诺工期为380日历天。",
            )
            self.assertEqual(out_fail["verdict"], config.NON_COMPLIANT)

            # Grounded compliant duration 350 <= 360 -> passes legitimately
            out_ok = real_backend.reevaluate_clause_with_modified_bid(
                req=req,
                existing_result=existing_result,
                pdf_path="",
                page_no=5,
                modified_text="投标函：我方承诺工期为350日历天。",
            )
            self.assertEqual(out_ok["verdict"], config.COMPLIANT)

    def test_global_context_never_copies_expected_numeric_without_explicit_bid_number(self) -> None:
        # Case A: Generic bid letter without explicit validity days -> must abstain, NEVER copy 90
        generic_letter = _make_passage(
            "E1",
            "投标函：我方已仔细研究招标文件全部内容，按合同约定实施和完成承包工程，工期：360日历天。我方承诺在招标文件规定的投标有效期内不撤销投标文件。",
            page=2,
        )
        cases_a = [
            {
                "clause_id": "T0037",
                "requirement": "投标有效期为90日历天。",
                "category": "Commercial",
                "evidence_context": json.dumps([generic_letter], ensure_ascii=False),
            }
        ]
        gctx_a = build_project_global_context(cases_a)
        res_a = StructuredExtractionResult(
            clause_id="T0037",
            requirement_text="投标有效期为90日历天。",
            category="Commercial",
            tender_slots=TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="validity_days",
                operator="==",
                expected_numeric=90.0,
                expected_unit="日历天",
                source_quotes={
                    "expected_numeric": "投标有效期为90日历天",
                    "expected_unit": "投标有效期为90日历天",
                },
            ),
            bid_slots=BidEvidenceSlots(evidence_found=False),
        )
        res_a = ground_extraction(res_a, cases_a[0]["evidence_context"])
        res_a, _ = hydrate_with_global_context(res_a, cases_a[0]["evidence_context"], gctx_a)
        self.assertIsNone(res_a.bid_slots.actual_numeric)
        self.assertEqual(self.engine.evaluate(res_a).verdict, "INSUFFICIENT_EVIDENCE")

        # Case B: Bid letter explicitly states 60 days (< 90 days) -> must extract 60 and fail R01!
        short_letter = _make_passage(
            "E1",
            "投标函：我方已仔细研究招标文件全部内容，按合同约定实施和完成承包工程，工期：360日历天。本投标自开标日起有效期为60个日历天。",
            page=2,
        )
        cases_b = [
            {
                "clause_id": "T0037",
                "requirement": "投标有效期不少于90日历天。",
                "category": "Commercial",
                "evidence_context": json.dumps([short_letter], ensure_ascii=False),
            }
        ]
        gctx_b = build_project_global_context(cases_b)
        res_b = StructuredExtractionResult(
            clause_id="T0037",
            requirement_text="投标有效期不少于90日历天。",
            category="Commercial",
            tender_slots=TenderConstraintSlots(
                constraint_type="NUMERIC_BOUND",
                target_field="validity_days",
                operator=">=",
                expected_numeric=90.0,
                expected_unit="日历天",
                source_quotes={
                    "expected_numeric": "投标有效期不少于90日历天",
                    "expected_unit": "投标有效期不少于90日历天",
                },
            ),
            bid_slots=BidEvidenceSlots(evidence_found=False),
        )
        res_b = ground_extraction(res_b, cases_b[0]["evidence_context"])
        res_b, _ = hydrate_with_global_context(res_b, cases_b[0]["evidence_context"], gctx_b)
        self.assertEqual(res_b.bid_slots.actual_numeric, 60)
        self.assertEqual(self.engine.evaluate(res_b).verdict, "NON_COMPLIANT")

    def test_global_context_constraint_rewrite_always_regrounds_bid_slots(self) -> None:
        p1 = _make_passage("E1", "目录：第一章 总体施工组织布置", page=1)
        ev_str = json.dumps([p1], ensure_ascii=False)
        gctx = build_project_global_context([])
        # Uncited `document_present=True` paired with `requires_human_review=True` on an objective "须编制..." clause
        res = StructuredExtractionResult(
            clause_id="T0300",
            requirement_text="须编制季节性施工保障方案。",
            category="Technical",
            tender_slots=TenderConstraintSlots(
                constraint_type="UNSUPPORTED",
                requires_human_review=True,
            ),
            bid_slots=BidEvidenceSlots(
                evidence_found=True,
                document_present=True,
                evidence_references=[],
            ),
        )
        res, _ = hydrate_with_global_context(res, ev_str, gctx)
        self.assertTrue(bool(res.extraction_issues))
        self.assertEqual(self.engine.evaluate(res).verdict, "INSUFFICIENT_EVIDENCE")

    def test_conflicting_evidence_maps_to_non_compliant_not_insufficient(self) -> None:
        self.assertEqual(real_backend.map_ui_verdict("CONFLICTING_EVIDENCE"), config.NON_COMPLIANT)
        self.assertEqual(real_backend.map_ui_verdict("NON_COMPLIANT"), config.NON_COMPLIANT)
        self.assertEqual(real_backend.map_ui_verdict("INSUFFICIENT_EVIDENCE"), config.INSUFFICIENT)

    def test_expiring_license_without_expiry_date_never_marked_valid_by_approval_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            extractor = DeepSeekSlotExtractor(
                cache_path=Path(tmp) / "cache.json",
                cache_only=True,
                review_reference_date="2026-11-16",
            )
            passages = [
                _make_passage(
                    "E1",
                    "一级建造师注册证书 姓名：李四 批准日期：2020-05-01 聘用企业：某建设有限公司",
                    page=8,
                )
            ]
            res = StructuredExtractionResult(
                clause_id="T0099",
                requirement_text="项目经理1人，须具备一级注册建造师有效注册证书。",
                category="Qualification",
                tender_slots=TenderConstraintSlots(
                    constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
                    required_role="project_manager",
                    required_count=1,
                    expected_text="一级注册建造师",
                    review_reference_date="2026-11-16",
                ),
                bid_slots=BidEvidenceSlots(
                    evidence_found=True,
                    personnel_records=[
                        {
                            "role": "project_manager",
                            "name": "李四",
                            "qualification_text": "一级注册建造师",
                        }
                    ],
                    evidence_references=[
                        {"field": "personnel_records", "evidence_id": "E1", "quote": "姓名：李四"}
                    ],
                ),
            )
            normed = extractor._normalize_llm_extraction(res, passages)
            self.assertIsNone(normed.bid_slots.personnel_records[0].get("certificate_valid"))
            self.assertEqual(self.engine.evaluate(normed).verdict, "INSUFFICIENT_EVIDENCE")

    def test_confirm_result_preserves_non_compliant_and_signoff_blocks_defects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with patch.object(config, "DATA_DIR", tmp_dir), patch.object(
                config, "DB_PATH", tmp_dir / "test.db"
            ), patch.object(config, "PROJECTS_DIR", tmp_dir / "projects"):
                store.init_db()
                pid = store.create_project("Anti-False-Pass Test Project")
                req1 = store.insert(
                    "requirements",
                    {
                        "project_id": pid,
                        "clause_no": "T0001",
                        "text": "投标报价不得超过最高限价1000万元。",
                        "category": "Commercial",
                        "origin": "extracted",
                        "applicable": 1,
                        "created_at": store.now(),
                    },
                )
                req2 = store.insert(
                    "requirements",
                    {
                        "project_id": pid,
                        "clause_no": "T0002",
                        "text": "授权委托书须加盖单位公章。",
                        "category": "Document",
                        "origin": "extracted",
                        "applicable": 1,
                        "created_at": store.now(),
                    },
                )
                rnd_id = store.insert(
                    "rounds",
                    {
                        "project_id": pid,
                        "no": 1,
                        "engine": config.ENGINE_NAME,
                        "rule_version": config.RULE_VERSION,
                        "status": "done",
                        "created_at": store.now(),
                    },
                )
                res_nc_id = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req1,
                        "verdict": config.NON_COMPLIANT,
                        "original_verdict": config.NON_COMPLIANT,
                        "rule_id": "R01_NUMERIC_BOUND",
                        "explanation": "报价1050万元 > 1000万元",
                        "fields": [{"field": "报价", "required": "<= 1000", "actual": "1050", "result": config.NON_COMPLIANT}],
                        "chain": {"sub_verdict": "NON_COMPLIANT", "sub_verdict_label": "✗ Non-compliant"},
                        "human_status": config.UNREVIEWED,
                        "updated_at": store.now(),
                    },
                )
                res_ins_id = store.insert(
                    "results",
                    {
                        "round_id": rnd_id,
                        "requirement_id": req2,
                        "verdict": config.INSUFFICIENT,
                        "original_verdict": config.INSUFFICIENT,
                        "rule_id": "R04_DOCUMENT_PRESENCE_AND_SEAL",
                        "explanation": "印章需目视核验",
                        "fields": [{"field": "公章", "required": "加盖公章", "actual": "待查", "result": config.INSUFFICIENT}],
                        "chain": {"sub_verdict": "SEAL_SIGNATURE_CHECK", "sub_verdict_label": "🔍 Seal & Signature Check"},
                        "human_status": config.UNREVIEWED,
                        "updated_at": store.now(),
                    },
                )

                # 1. Confirming a Non-compliant result MUST preserve Non-compliant (no false pass!)
                service.confirm_result(res_nc_id, "Alice", "Reviewer", note="确认超限价违规")
                after_confirm = store.get_result(res_nc_id)
                self.assertEqual(after_confirm["verdict"], config.NON_COMPLIANT)
                self.assertEqual(after_confirm["human_status"], config.CONFIRMED)

                # 2. Confirming Insufficient evidence MUST also preserve Insufficient evidence
                service.confirm_result(res_ins_id, "Alice", "Reviewer")
                self.assertEqual(store.get_result(res_ins_id)["verdict"], config.INSUFFICIENT)

                # 3. Sign-off MUST be blocked while Non-compliant or Insufficient clauses remain
                blockers = service.signoff_blockers(rnd_id)
                self.assertTrue(any("Non-compliant" in b for b in blockers))
                self.assertTrue(any("Insufficient evidence" in b for b in blockers))

                # 4. Manual pass without reason MUST raise ServiceError
                with self.assertRaises(service.ServiceError):
                    service.approve_manual_pass(res_ins_id, "  ", "Alice", "Reviewer")

                # 5. Reviewer CANNOT manually override deterministic Non-compliant
                with self.assertRaises(service.ServiceError):
                    service.approve_manual_pass(res_nc_id, "我想直接放行这条超限价条款", "Alice", "Reviewer")

                # 6. Reviewer CAN pass Insufficient evidence WITH explicit verification basis
                service.approve_manual_pass(res_ins_id, "已目视核对第15页公章清晰完整", "Alice", "Reviewer")
                self.assertEqual(store.get_result(res_ins_id)["verdict"], config.COMPLIANT)
                self.assertEqual(store.get_result(res_ins_id)["corrected"], 1)

                # 7. Manager CAN override Non-compliant ONLY with explicit written basis, unblocking sign-off
                service.approve_manual_pass(res_nc_id, "补遗第2号已将控制价上调至1100万元，经经理复核合规", "Bob", "Manager")
                self.assertEqual(store.get_result(res_nc_id)["verdict"], config.COMPLIANT)
                self.assertEqual(service.signoff_blockers(rnd_id), [])


class RepositoryHygieneTests(unittest.TestCase):
    """Enforce AGENTS.md file-hygiene, markdown whitelist, and anti-toy-fallback rules."""

    REPO_ROOT = SYSTEMCODE_DIR.parent

    ALLOWED_MARKDOWN_FILES = {
        "README.md",
        "AGENTS.md",
        "docs/DECISIONS.md",
        "docs/ROADMAP.md",
        "docs/EXPERIMENTS.md",
        "Miscellaneous/TenderCheck_Annotation/TenderCheck_Annotation_Guideline_v1_3.md",
    }

    IGNORED_DIR_PARTS = {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "env",
        "ENV",
        "data_extracted",
        "samples",
        "scratch",
        ".system_generated",
    }

    def _is_ignored(self, rel_path: Path) -> bool:
        parts = rel_path.parts
        if any(p in self.IGNORED_DIR_PARTS for p in parts):
            return True
        rel_posix = rel_path.as_posix()
        for prefix in (
            "SystemCode/reports/rag_experiments/",
            "SystemCode/reports/runs/",
            "SystemCode/data/app/",
            "SystemCode/models/",
        ):
            if rel_posix.startswith(prefix):
                return True
        return False

    def test_markdown_files_strictly_match_whitelist(self) -> None:
        found_md = set()
        for md_path in self.REPO_ROOT.rglob("*.md"):
            rel = md_path.relative_to(self.REPO_ROOT)
            if self._is_ignored(rel):
                continue
            found_md.add(rel.as_posix())

        unexpected = sorted(found_md - self.ALLOWED_MARKDOWN_FILES)
        missing_core = sorted(
            {"README.md", "AGENTS.md", "docs/DECISIONS.md", "docs/ROADMAP.md", "docs/EXPERIMENTS.md"} - found_md
        )
        self.assertEqual(
            unexpected,
            [],
            f"Unauthorized .md file(s) found outside AGENTS.md whitelist: {unexpected}. "
            "Update docs/{{DECISIONS,ROADMAP,EXPERIMENTS}}.md in place instead of creating new .md files.",
        )
        self.assertEqual(missing_core, [], f"Core governance/documentation file(s) missing: {missing_core}")

    def test_no_scratch_or_versioned_sprawl_scripts_in_repo(self) -> None:
        import re

        root_py = [p.name for p in self.REPO_ROOT.glob("*.py")]
        self.assertEqual(
            root_py,
            [],
            f"Python scripts must not be placed at repository root (use scratch/ for one-off probes): {root_py}",
        )

        sprawl_re = re.compile(r"^(?:temp_|tmp_|scratch_|debug_|quick_test)|_(?:copy|backup|old|new|v\d+)\.py$", re.I)
        legacy_allowed = {"SystemCode/tests/test_extraction_rules_v6.py"}
        offenders = []
        for py_path in SYSTEMCODE_DIR.rglob("*.py"):
            rel = py_path.relative_to(self.REPO_ROOT)
            if self._is_ignored(rel) or rel.as_posix() in legacy_allowed:
                continue
            if sprawl_re.search(py_path.name):
                offenders.append(rel.as_posix())

        self.assertEqual(
            offenders,
            [],
            f"Sprawl/temporary script filename(s) detected in SystemCode/: {offenders}. Use scratch/ instead.",
        )

    def test_no_fabricated_fallback_markers_in_src(self) -> None:
        forbidden_tokens = ("_offline_chinese_slot_fallback", "已核验人员_")
        src_dir = SYSTEMCODE_DIR / "src"
        violations = []
        for py_path in src_dir.rglob("*.py"):
            content = py_path.read_text(encoding="utf-8")
            for tok in forbidden_tokens:
                if tok in content:
                    violations.append(f"{py_path.relative_to(self.REPO_ROOT).as_posix()}: {tok}")
        self.assertEqual(violations, [], f"Forbidden fake-pass fallback code detected in src/: {violations}")


if __name__ == "__main__":
    unittest.main()

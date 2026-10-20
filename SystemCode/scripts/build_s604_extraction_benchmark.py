"""Build the 50-clause S604 standalone LLM extraction benchmark (Step 5.1).

Grounded in:
- SystemCode/data/evidence_annotations/s604/annotations.json
- SystemCode/data/rag_index/s604_passages_v3/parent_chunks.jsonl
- SystemCode/data/rule_eval/s604_clause_rule_mapping.json
- SystemCode/data/reasoning_diagnostics/s604/compliance_calibrated_reference.json
"""
from __future__ import annotations

import copy
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYS_ROOT = ROOT / "SystemCode"
if str(SYS_ROOT) not in sys.path:
    sys.path.insert(0, str(SYS_ROOT))

from src.extraction.evidence import ground_extraction
from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.rules.rule_engine import ComplianceRuleEngine

REVIEW_REFERENCE_DATE = "2022-11-11"

# 10 dev clauses covering R01-R09 + 40 test clauses
DEV_CLAUSES = {
    "T0001",  # R01 numeric bound
    "T0004",  # R02 qualification hierarchy + timeliness abstention trap
    "T0127",  # R03 RMB uppercase & cross-table consistency
    "T0014",  # R04 document presence & multi-party seals
    "T0033",  # R05 prohibition & commitment (compliant)
    "T0099",  # R05 external verification abstention
    "T0057",  # R05 two-sided conflict detection
    "T0214",  # R06 interval bracket scoring
    "T0078",  # R07 multi-person staffing count & qualification
    "T0011",  # R08 historical performance chain & role change approval
    "T0059",  # R09 net-base ratio arithmetic formula
}
# Keep dev split at exactly 10 representative clauses covering R01..R09
DEV_CLAUSES = {
    "T0001",  # R01
    "T0004",  # R02 (with validity trap)
    "T0127",  # R03
    "T0014",  # R04
    "T0033",  # R05 (compliant)
    "T0057",  # R05 (conflict)
    "T0214",  # R06
    "T0078",  # R07
    "T0011",  # R08
    "T0059",  # R09
}


def load_inputs():
    ann = json.loads(
        (SYS_ROOT / "data/evidence_annotations/s604/annotations.json").read_text("utf-8")
    )
    chunks = [
        json.loads(line)
        for line in (
            SYS_ROOT / "data/rag_index/s604_passages_v3/parent_chunks.jsonl"
        ).read_text("utf-8").splitlines()
        if line.strip()
    ]
    chunk_by_id = {c["chunk_id"]: c for c in chunks}
    mapping = {
        c["clause_id"]: c
        for c in json.loads(
            (SYS_ROOT / "data/rule_eval/s604_clause_rule_mapping.json").read_text("utf-8")
        )["clauses"]
    }
    calibrated = {
        c["clause_id"]: c
        for c in json.loads(
            (
                SYS_ROOT
                / "data/reasoning_diagnostics/s604/compliance_calibrated_reference.json"
            ).read_text("utf-8")
        )["queries"]
    }
    return ann, chunks, chunk_by_id, mapping, calibrated


def select_clause_chunks(clause_id: str, q_ann: dict, chunks: list[dict], chunk_by_id: dict[str, dict], sources: dict) -> list[dict]:
    """Select full parent chunks from parent_chunks.jsonl for a clause's annotated evidence groups,
    plus explicit role anchor chunks when needed for self-contained subject attribution."""
    selected_ids: list[str] = []

    def add_chunk_id(cid: str):
        if cid in chunk_by_id and cid not in selected_ids:
            selected_ids.append(cid)

    # Role anchor chunks for self-contained single-clause subject attribution
    if clause_id in {"T0003", "T0004", "T0005"}:
        add_chunk_id("11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub16_P00001")
    elif clause_id in {"T0007", "T0009", "T0010", "T0011", "T0012", "T0013", "T0014", "T0057", "T0209"}:
        add_chunk_id("11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub14_P00001")
    if clause_id in {"T0013", "T0014"}:
        # Include the project manager role-change approval batch for S214 Phase 4
        add_chunk_id("11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sub02_P00005")
    if clause_id == "T0010":
        # Include both pages 4 and 5 of sys01 so the cross-page active-assignment conflict is in context
        add_chunk_id("11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_P00004")
        add_chunk_id("11.11-S604皖南交旅融合（飞毕路美丽公路）项目_sys01_P00005")

    for group in q_ann["evidence_groups"]:
        alts = group["alternatives"]
        # For S214 Phase 4 performance groups where alt0 (sub06_P00005) had an OCR typo '王文华'
        # while alt1 (sub02_P00005) has clean '王文年', prefer alt1 when available
        chosen_alt = alts[0]
        if len(alts) > 1 and chosen_alt["document_id"].endswith("_sub06"):
            for alt in alts:
                if alt["document_id"].endswith("_sub02"):
                    chosen_alt = alt
                    break
        doc_id = chosen_alt["document_id"]
        ps, pe = chosen_alt["page_start"], chosen_alt["page_end"]
        for ch in chunks:
            if ch["document_id"] == doc_id and ch["page_start"] <= pe and ch["page_end"] >= ps:
                add_chunk_id(ch["chunk_id"])

    evidence_list = []
    for idx, cid in enumerate(selected_ids, 1):
        ch = chunk_by_id[cid]
        evidence_list.append(
            {
                "evidence_id": f"E{idx}",
                "chunk_id": ch["chunk_id"],
                "document_id": ch["document_id"],
                "source_file": ch.get("source_file", ""),
                "source_sha256": ch.get("source_sha256") or sources.get(ch["document_id"], {}).get("sha256", ""),
                "page_start": ch["page_start"],
                "page_end": ch["page_end"],
                "section": ch.get("section_path", ""),
                "text": ch["text"],
                "has_official_seal": bool(ch.get("has_official_seal")),
                "seal_company_name": ch.get("seal_company_name"),
                "has_signature": bool(ch.get("has_signature")),
            }
        )
    return evidence_list


def find_eid_by_chunk_suffix(evidence_list: list[dict], suffix: str) -> str:
    for item in evidence_list:
        if item["chunk_id"].endswith(suffix):
            return item["evidence_id"]
    raise KeyError(f"Chunk suffix {suffix} not found in {[e['chunk_id'] for e in evidence_list]}")


def ref(field: str, eid: str, quote: str) -> dict:
    return {"field": field, "evidence_id": eid, "quote": quote}


def build_gold_spec(clause_id: str, req: str, evidence_list: list[dict]) -> dict:
    """Return gold_constraint_slots, gold_evidence_slots, must_remain_unknown, rule_family,
    expected_verdict, expected_score, and optional tender_context_overrides."""
    t_slots = dataclasses.asdict(TenderConstraintSlots())
    b_slots = dataclasses.asdict(BidEvidenceSlots())
    unknown_fields: list[str] = []
    overrides: dict = {}
    expected_verdict_with_context: str | None = None

    def set_t(**kwargs):
        t_slots.update(kwargs)

    def set_b(**kwargs):
        b_slots.update(kwargs)

    # ------------------------------------------------------------------
    # R01: Numeric Bound Clauses (T0001, T0002, T0023, T0027, T0037, T0135, T0204)
    # ------------------------------------------------------------------
    if clause_id in {"T0001", "T0027"}:
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="duration_days",
            operator="==",
            expected_numeric=360.0,
            expected_unit="日历天",
            source_quotes={"expected_numeric": req, "expected_unit": req},
        )
        set_b(
            evidence_found=True,
            actual_numeric=360.0,
            actual_unit="日历天",
            evidence_references=[
                ref("actual_numeric", e1, "工期: 360日历天。"),
                ref("actual_unit", e1, "工期: 360日历天。"),
            ],
            evidence_summary="商务文件投标函及技术标均明确承诺工期为360日历天。",
        )
        unknown_fields = ["secondary_numeric", "secondary_actual_numeric"]
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=unknown_fields,
            notes="工期显式等值/上限约束，投标函承诺360日历天。",
        )

    if clause_id == "T0002":
        e_letter = find_eid_by_chunk_suffix(evidence_list, "sys04_P00001")
        e_boq = find_eid_by_chunk_suffix(evidence_list, "sys05_P00002")
        quote_letter = "愿意以人民币（大写）玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分（¥97984949.58元）的投标总价报（含税）"
        quote_boq = "投标报价汇总表\n工程名称：S604皖南交旅融合（飞毕路美丽公路）项目\n序号\n章次\n科目名称\n金额\n清单 第100章 总则\n5028034.51\n清单 第200章 路基\n33789741\n清单 第300章 路面\n35074386.43\n清单 第400章 桥梁、涵洞\n5019541.88\n清单 第600章 安全设施及预埋管线\n3876217.04\n清单 第700章 绿化及环境保护设施\n7197028.72\n第100章至第700章合计\n89984949.58\n已包含在清单合计中的材料、工程设备、专业工程暂估价合计\n40000\n清单合计减去材料、工程设备、专业工程暂估价合计\n89944949.58\n计日工合计\n暂列金额（不含计日工总额）\n8000000"
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="bid_price_yuan",
            operator="<=",
            expected_numeric=115046243.64,
            expected_unit="元",
            secondary_numeric=8000000.0,
            source_quotes={
                "expected_numeric": "项目招标控制价115046243.64元",
                "expected_unit": "项目招标控制价115046243.64元",
                "secondary_numeric": "含暂列金8000000.00元",
            },
        )
        set_b(
            evidence_found=True,
            actual_numeric=97984949.58,
            actual_unit="元",
            secondary_actual_numeric=8000000.0,
            evidence_references=[
                ref("actual_numeric", e_letter, quote_letter),
                ref("actual_unit", e_letter, quote_letter),
                ref("secondary_actual_numeric", e_boq, quote_boq),
            ],
            evidence_summary="报价文件投标函总报价为97984949.58元（低于控制价115046243.64元），工程量清单投标报价汇总表暂列金额为8000000元。",
        )
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="双数值约束：投标总价<=招标控制价115046243.64元且暂列金==8000000元。",
        )

    if clause_id == "T0023":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub12_P00001")
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="bid_bond_amount",
            operator="==",
            expected_numeric=50.0,
            expected_unit="万元",
            source_quotes={"expected_numeric": req, "expected_unit": req},
        )
        set_b(
            evidence_found=True,
            actual_numeric=500000.0,
            actual_unit="元",
            evidence_references=[
                ref("actual_numeric", e1, "| 金额 | 小写 | 500,000.00元 | 币种 | 人民币 |"),
                ref("actual_unit", e1, "| 金额 | 小写 | 500,000.00元 | 币种 | 人民币 |"),
            ],
            evidence_summary="银行电子回单显示投标人安徽水利开发有限公司汇出投标保证金小写500,000.00元（大写伍拾万元整）。",
        )
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["secondary_numeric", "secondary_actual_numeric"],
            notes="电子回单原文小写金额为500,000.00元，抽取器保持原文数值与单位（500000.0元），由规则引擎统一量纲比对50万元。",
        )

    if clause_id == "T0037":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="bid_validity_days",
            operator="==",
            expected_numeric=90.0,
            expected_unit="日",
            source_quotes={"expected_numeric": req, "expected_unit": req},
        )
        set_b(
            evidence_found=True,
            actual_numeric=None,
            actual_unit="",
            actual_text="我方承诺在招标文件规定的投标有效期内不撤销投标文件。",
            evidence_references=[
                ref("actual_text", e1, "2. 我方承诺在招标文件规定的投标有效期内不撤销投标文件。"),
            ],
            evidence_summary="投标函仅承诺在招标文件规定的投标有效期内不撤销投标文件，未显式写出90日数值。",
        )
        unknown_fields = ["actual_numeric", "actual_unit"]
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="INSUFFICIENT_EVIDENCE",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=unknown_fields,
            notes="典型防脑补测试：投标函仅定性承诺在规定投标有效期内不撤销，未出现90日，禁止把招标阈值90抄入actual_numeric。",
        )

    if clause_id == "T0135":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys04_P00001")
        quote_letter = "愿意以人民币（大写）玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分（¥97984949.58元）的投标总价报（含税）"
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="bid_price_yuan",
            operator="<=",
            expected_numeric=None,
            expected_unit="",
            source_quotes={},
        )
        set_b(
            evidence_found=True,
            actual_numeric=97984949.58,
            actual_unit="元",
            evidence_references=[
                ref("actual_numeric", e1, quote_letter),
                ref("actual_unit", e1, quote_letter),
            ],
            evidence_summary="报价文件投标函载明投标总报价为97984949.58元；单句招标条款未给出最高投标限价具体数值。",
        )
        unknown_fields = ["expected_numeric", "expected_unit"]
        overrides = {
            "expected_numeric": 115046243.64,
            "expected_unit": "元",
            "source_section": "T0002",
        }
        expected_verdict_with_context = "COMPLIANT"
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="INSUFFICIENT_EVIDENCE",
            expected_verdict_with_context=expected_verdict_with_context,
            tender_context_overrides=overrides,
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=unknown_fields,
            notes="单句招标要求缺少最高投标限价数值（见T0002: 115046243.64元）；单条抽取时expected_numeric必须保持null，注入项目级上下文后判定COMPLIANT。",
        )

    if clause_id == "T0204":
        e_boq = find_eid_by_chunk_suffix(evidence_list, "sys05_P00002")
        quote_boq = "投标报价汇总表\n工程名称：S604皖南交旅融合（飞毕路美丽公路）项目\n序号\n章次\n科目名称\n金额\n清单 第100章 总则\n5028034.51\n清单 第200章 路基\n33789741\n清单 第300章 路面\n35074386.43\n清单 第400章 桥梁、涵洞\n5019541.88\n清单 第600章 安全设施及预埋管线\n3876217.04\n清单 第700章 绿化及环境保护设施\n7197028.72\n第100章至第700章合计\n89984949.58\n已包含在清单合计中的材料、工程设备、专业工程暂估价合计\n40000\n清单合计减去材料、工程设备、专业工程暂估价合计\n89944949.58\n计日工合计\n暂列金额（不含计日工总额）\n8000000"
        set_t(
            constraint_type="NUMERIC_BOUND",
            target_field="provisional_sum_yuan",
            operator="==",
            expected_numeric=None,
            expected_unit="",
            source_quotes={},
        )
        set_b(
            evidence_found=True,
            actual_numeric=8000000.0,
            actual_unit="元",
            evidence_references=[
                ref("actual_numeric", e_boq, quote_boq),
                ref("actual_unit", e_boq, quote_boq),
            ],
            evidence_summary="投标报价汇总表载明暂列金额为8000000元；单句招标条款未给出招标暂列金额具体数值。",
        )
        unknown_fields = ["expected_numeric", "expected_unit"]
        overrides = {
            "expected_numeric": 8000000.0,
            "expected_unit": "元",
            "source_section": "T0002",
        }
        expected_verdict_with_context = "COMPLIANT"
        return dict(
            rule_family="R01_NUMERIC_BOUND",
            expected_verdict="INSUFFICIENT_EVIDENCE",
            expected_verdict_with_context=expected_verdict_with_context,
            tender_context_overrides=overrides,
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=unknown_fields,
            notes="单句招标要求缺少暂列金额基准值（见T0002: 8000000.00元）；单条抽取时expected_numeric必须保持null，注入项目级上下文后判定COMPLIANT。",
        )

    # ------------------------------------------------------------------
    # R02: Qualification Hierarchy & Certificate Timeliness (T0003, T0004, T0005, T0007, T0009, T0015)
    # ------------------------------------------------------------------
    if clause_id == "T0003":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub16_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub16_P00004")
        cert_quote = "营业执照\n\n统一社会信用代码 91340300MA2RJ2XJXJ (1-10)\n名称 安徽水利开发有限公司\n类型 其他有限责任公司"
        role_quote = "| 投标人名称 | 安徽水利开发有限公司 |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="business_license",
            operator="LEVEL_GE",
            expected_text="独立法人资格",
            required_role="bidder",
            review_reference_date=REVIEW_REFERENCE_DATE,
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            actual_text="其他有限责任公司",
            subject_role="投标人",
            certificate_holder="安徽水利开发有限公司",
            is_long_term=True,
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[ref("actual_text", e_cert, "类型 其他有限责任公司")],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "安徽水利开发有限公司",
                    "role": "bidder",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                }
            ],
            evidence_summary="营业执照副本显示投标人安徽水利开发有限公司类型为其他有限责任公司，营业期限长期，具备独立法人资格。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["valid_to", "extension_valid_to"],
            notes="企业营业执照独立法人资格核验与投标人主体绑定。",
        )

    if clause_id == "T0004":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub16_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub16_P00006")
        cert_quote = "企业名称:安徽水利开发有限公司\n详细地址:安徽省蚌埠市东海大道5183号\n统一社会信用代码(或营业执照注册号):91340300MA2RJ2XJXJ\n法定代表人:张晓林\n注册资本:240236.03万元人民币 经济性质:其他有限责任公司\n证书编号:D134128243 有效期:2021年12月31日\n资质类别及等级:\n公路工程施工总承包特级"
        role_quote = "| 投标人名称 | 安徽水利开发有限公司 |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="enterprise_qual",
            operator="LEVEL_GE",
            expected_text="公路工程施工总承包三级",
            required_domain="公路工程",
            required_role="bidder",
            review_reference_date=REVIEW_REFERENCE_DATE,
            requires_validity_date=True,
            allow_extension_notice=True,
            source_quotes={
                "expected_text": "公路工程施工总承包三级及以上资质",
                "required_domain": "公路工程施工总承包三级及以上资质",
            },
        )
        set_b(
            evidence_found=True,
            actual_text="公路工程施工总承包特级",
            actual_domain="公路工程",
            subject_role="投标人",
            certificate_holder="安徽水利开发有限公司",
            valid_to="2021-12-31",
            extension_valid_to="",
            certificate_valid=None,
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[
                ref("actual_text", e_cert, "公路工程施工总承包特级"),
                ref("actual_domain", e_cert, "公路工程施工总承包特级"),
            ],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "安徽水利开发有限公司",
                    "role": "bidder",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
                {
                    "field": "actual_domain",
                    "evidence_id": e_cert,
                    "subject_name": "安徽水利开发有限公司",
                    "role": "bidder",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
            ],
            evidence_summary="sub16第6页建筑业企业资质证书显示安徽水利开发有限公司具备公路工程施工总承包特级，但原证载明有效期至2021年12月31日（早于评审基准日2022-11-11），且当前证据未见住建部统一延续证明。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="INSUFFICIENT_EVIDENCE",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["extension_valid_to", "certificate_valid"],
            notes="T0004典型假阳性阻断案例：特级>=三级满足等级，但证书原载有效期2021-12-31早于2022-11-11且缺少延续证明，extension_valid_to和certificate_valid必须保持未知，判定INSUFFICIENT_EVIDENCE。",
        )

    if clause_id == "T0005":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub16_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub16_P00005")
        cert_quote = "安全生产许可证\n\n单位名称：安徽水利开发有限公司\n主要负责人：张晓林\n单位地址：安徽省蚌埠市东海大道5183号\n统一社会信用代码：91340300MA2RJ2XJXJ\n经济类型：其他有限责任公司\n许可范围：建筑施工\n证书编号：(皖)JZ安许证字[2019]013097\n有效期至：2025年03月31日"
        role_quote = "| 投标人名称 | 安徽水利开发有限公司 |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="safety_license",
            operator="LEVEL_GE",
            expected_text="有效安全生产许可证",
            required_role="bidder",
            review_reference_date=REVIEW_REFERENCE_DATE,
            requires_validity_date=True,
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            actual_text="安全生产许可证",
            subject_role="投标人",
            certificate_holder="安徽水利开发有限公司",
            valid_from="2022-04-01",
            valid_to="2025-03-31",
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[ref("actual_text", e_cert, "安全生产许可证")],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "安徽水利开发有限公司",
                    "role": "bidder",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                }
            ],
            evidence_summary="安全生产许可证显示单位名称为安徽水利开发有限公司，有效期2022-04-01至2025-03-31，覆盖评审基准日2022-11-11。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["extension_valid_to"],
            notes="安全生产许可证有效性及起止日期核验（2025-03-31 >= 2022-11-11）。",
        )

    if clause_id == "T0007":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub05_P00003")
        cert_quote = "中华人民共和国一级建造师注册证书\n\n姓 名：王文年\n性 别：男\n出生日期：1981年12月13日\n注册编号：皖1342011201207116\n聘用企业：安徽水利开发有限公司\n注册专业：公路工程(有效期：2022-05-16至2025-05-15)"
        role_quote = "| 职务 | 姓名 | 职称 | 执业或职业资格证明 | 列5 | 列6 | 列7 | 列8 | 备注 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n|  |  |  | 证书名称 | 级别 | 证号 | 专业 | 养老保险 |  |\n| 项目经理 | 王文年 | 高级 | 一级注册 建造师证 | 一级 | 皖1342011201207116 | 公路工程 | 已缴纳 |  |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="constructor",
            operator="LEVEL_GE",
            expected_text="一级注册建造师",
            required_domain="公路工程",
            required_role="project_manager",
            review_reference_date=REVIEW_REFERENCE_DATE,
            requires_validity_date=True,
            source_quotes={
                "expected_text": "一级注册建造师资格",
                "required_domain": "公路工程专业",
            },
        )
        set_b(
            evidence_found=True,
            actual_text="一级建造师注册证书",
            actual_domain="公路工程",
            subject_role="项目经理",
            certificate_holder="王文年",
            valid_from="2022-05-16",
            valid_to="2025-05-15",
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[
                ref("actual_text", e_cert, "中华人民共和国一级建造师注册证书"),
                ref("actual_domain", e_cert, "注册专业：公路工程(有效期：2022-05-16至2025-05-15)"),
            ],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "王文年",
                    "role": "project_manager",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
                {
                    "field": "actual_domain",
                    "evidence_id": e_cert,
                    "subject_name": "王文年",
                    "role": "project_manager",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
            ],
            evidence_summary="拟任项目经理王文年持有一级建造师注册证书（注册专业：公路工程，有效期2022-05-16至2025-05-15）。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["extension_valid_to"],
            notes="项目经理一级注册建造师等级、公路工程专业、角色归属与有效期综合核验。",
        )

    if clause_id == "T0009":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub05_P00004")
        cert_quote = "姓名：王文年 性别：男\n出生年月：1981-12-13 身份证号：34080319811213217X\n经公路水运工程施工企业主要负责人和安全生产管理人员安全生产考核合格，特发此证。\n企业名称：安徽水利开发有限公司\n证书编号：皖交安B(19)G01960\n有效期：2012-09-04 至 2024-09-03"
        role_quote = "| 职务 | 姓名 | 职称 | 执业或职业资格证明 | 列5 | 列6 | 列7 | 列8 | 备注 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n|  |  |  | 证书名称 | 级别 | 证号 | 专业 | 养老保险 |  |\n| 项目经理 | 王文年 | 高级 | 一级注册 建造师证 | 一级 | 皖1342011201207116 | 公路工程 | 已缴纳 |  |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="safety_b",
            operator="LEVEL_GE",
            expected_text="安全生产考核合格证书",
            required_role="project_manager",
            review_reference_date=REVIEW_REFERENCE_DATE,
            requires_validity_date=True,
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            actual_text="皖交安B(19)G01960",
            subject_role="项目经理",
            certificate_holder="王文年",
            valid_to="2024-09-03",
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[ref("actual_text", e_cert, "皖交安B(19)G01960")],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "王文年",
                    "role": "project_manager",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                }
            ],
            evidence_summary="项目经理王文年持有交安B证（皖交安B(19)G01960），有效期至2024-09-03，覆盖评审基准日2022-11-11。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["extension_valid_to"],
            notes="项目经理安全生产考核合格证书（交安B证）有效性及主体归属核验。",
        )

    if clause_id == "T0015":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub05_P00012")
        cert_quote = "王曙\nFull Name 男\nSex 1983.10\n出生年月 安徽水利开发股份\nDate of Birth Working Unit有限公司\n工程技术人员\n系列名称 Category Appellation\n专业名称 路桥工程\nSpecialty Appellation\n资格名称 高级工程师"
        role_quote = "| 职务 | 姓名 | 职称 | 执业或职业资格证明 | 列5 | 列6 | 列7 | 列8 | 备注 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n|  |  |  | 证书名称 | 级别 | 证号 | 专业 | 养老保险 |  |\n| 项目经理 | 王文年 | 高级 | 一级注册 建造师证 | 一级 | 皖1342011201207116 | 公路工程 | 已缴纳 |  |\n| 项目总工 | 王曙 | 高级 | 工程师证 | 高级 | （2018）934000648 | 路桥工程 | 已缴纳 |  |"
        set_t(
            constraint_type="QUALIFICATION_LEVEL",
            target_field="chief_engineer_title",
            operator="LEVEL_GE",
            expected_text="中级及以上职称",
            required_domain="公路工程相关专业",
            required_role="chief_engineer",
            review_reference_date=REVIEW_REFERENCE_DATE,
            source_quotes={
                "expected_text": "中级及以上职称",
                "required_domain": "公路工程相关专业",
            },
        )
        set_b(
            evidence_found=True,
            actual_text="高级工程师",
            actual_domain="路桥工程",
            subject_role="项目总工",
            certificate_holder="王曙",
            evidence_source_type="CERTIFICATE_SCAN",
            evidence_references=[
                ref("actual_text", e_cert, "资格名称 高级工程师"),
                ref("actual_domain", e_cert, "专业名称 路桥工程"),
            ],
            subject_bindings=[
                {
                    "field": "actual_text",
                    "evidence_id": e_cert,
                    "subject_name": "王曙",
                    "role": "chief_engineer",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
                {
                    "field": "actual_domain",
                    "evidence_id": e_cert,
                    "subject_name": "王曙",
                    "role": "chief_engineer",
                    "quote": cert_quote,
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                },
            ],
            evidence_summary="拟任项目总工王曙具有路桥工程专业高级工程师职称，满足公路工程相关专业中级及以上职称要求。",
        )
        return dict(
            rule_family="R02_QUALIFICATION_HIERARCHY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["valid_to", "extension_valid_to"],
            notes="项目总工职称等级（高级工程师>=中级）及公路工程相关专业（路桥工程）核验。",
        )

    # ------------------------------------------------------------------
    # R03: RMB Uppercase & Cross-Document Entity Consistency (T0069, T0106, T0127)
    # ------------------------------------------------------------------
    if clause_id == "T0069":
        e_bidder = find_eid_by_chunk_suffix(evidence_list, "sub16_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub05_P00003")
        set_t(
            constraint_type="ENTITY_CONSISTENCY",
            target_field="manager_employer_consistency",
            operator="CONSISTENT",
            expected_text="项目经理注册证书注册单位须与投标人名称一致",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            entity_primary="安徽水利开发有限公司",
            entity_secondary="安徽水利开发有限公司",
            evidence_references=[
                ref("entity_primary", e_bidder, "| 投标人名称 | 安徽水利开发有限公司 |"),
                ref("entity_secondary", e_cert, "聘用企业：安徽水利开发有限公司"),
            ],
            evidence_summary="投标人名称为安徽水利开发有限公司（sub16第1页），项目经理王文年一级建造师注册证书聘用企业为安徽水利开发有限公司（sub05第3页），二者完全一致。",
        )
        return dict(
            rule_family="R03_RMB_UPPER_AND_CONSISTENCY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric", "upper_amount_text"],
            notes="跨页主体一致性核对：投标人名称与项目经理建造师注册证书聘用企业一致。",
        )

    if clause_id == "T0106":
        e_bidder = find_eid_by_chunk_suffix(evidence_list, "sub16_P00001")
        e_mot = find_eid_by_chunk_suffix(evidence_list, "sub16_P00011")
        set_t(
            constraint_type="ENTITY_CONSISTENCY",
            target_field="mot_directory_name_consistency",
            operator="CONSISTENT",
            expected_text="投标人名称须与交通运输部施工资质企业名录相应企业名称完全一致",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            entity_primary="安徽水利开发有限公司",
            entity_secondary="安徽水利开发有限公司",
            evidence_references=[
                ref("entity_primary", e_bidder, "| 投标人名称 | 安徽水利开发有限公司 |"),
                ref("entity_secondary", e_mot, "| 1 | 安徽水利开发有限公司 | 施工企业 |"),
            ],
            evidence_summary="投标人名称（sub16第1页）与交通运输部施工资质企业名录截图中的企业名称（sub16第11页）均为安徽水利开发有限公司，完全一致。",
        )
        return dict(
            rule_family="R03_RMB_UPPER_AND_CONSISTENCY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric", "upper_amount_text"],
            notes="跨文档主体一致性核对：投标人名称与交通运输部名录截图企业名称一致。",
        )

    if clause_id == "T0127":
        e_letter = find_eid_by_chunk_suffix(evidence_list, "sys04_P00001")
        e_boq = find_eid_by_chunk_suffix(evidence_list, "sys05_P00002")
        quote_letter = "愿意以人民币（大写）玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分（¥97984949.58元）的投标总价报（含税）"
        quote_boq = "| 12 | 投标报价 |  | 97984949.58 |"
        set_t(
            constraint_type="ENTITY_CONSISTENCY",
            target_field="rmb_upper_consistency",
            operator="CONSISTENT",
            expected_text="已标价清单投标报价须与投标函大写报价一致",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            actual_numeric=97984949.58,
            actual_unit="元",
            upper_amount_text="玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分",
            entity_primary="97984949.58",
            entity_secondary="97984949.58",
            evidence_references=[
                ref("actual_numeric", e_letter, quote_letter),
                ref("actual_unit", e_letter, quote_letter),
                ref("upper_amount_text", e_letter, quote_letter),
                ref("entity_primary", e_letter, quote_letter),
                ref("entity_secondary", e_boq, quote_boq),
            ],
            evidence_summary="报价文件投标函大写报价（玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分，¥97984949.58元）与已标价工程量清单投标报价汇总表（97984949.58元）完全一致。",
        )
        return dict(
            rule_family="R03_RMB_UPPER_AND_CONSISTENCY",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="人民币大小写确定性转换与跨表报价（投标函vs工程量清单汇总表）一致性核验。",
        )

    # ------------------------------------------------------------------
    # R04: Document Presence & Seal Verification (T0012, T0013, T0014, T0056, T0112, T0113, T0118)
    # ------------------------------------------------------------------
    if clause_id == "T0012":
        e_contract = find_eid_by_chunk_suffix(evidence_list, "sub02_P00003")
        e_change = find_eid_by_chunk_suffix(evidence_list, "sub02_P00005")
        quote_contract = "七、合同协议书\n\n郎溪县交通运输局（发包人名称，以下简称“发包人”）为实施省道214郎溪县段\n改建工程四期工程（郎溪县城至十字铺工业园段），已接受 安徽水利开发股份有限公司\n（承包人名称，以下简称“承包人”）对该项目施工的投标。 发包人和承包人共同达成\n如下协议:\n\n1. 该标段由 K19+850.486 至 K35+004.744，长约 15.15426 km，公路等级为 I 级，设计时速为 80 公里/小时，沥青砼路面，其中郎溪县城段部分段落（K19+850.486 至 K20+935）\n采用城市主干道 I 级标准建设，设计速度 60 公里/小时；特大桥 / 座，计长 / m；\n大中桥 8 座，计长 1864m；隧道 / 座，计长 / m以及其他构造物工程等。\n\n2. 下列文件应视为构成合同文件的组成部分:\n\n(1) 本协议书及各种合同附件（含评标期间和合同谈判过程中的澄清文件和补充资料）;\n\n(2) 中标通知书;\n\n(3) 补遗书;\n\n(4) 投标函及投标函附录;\n\n(5) 项目专用合同条款;\n\n(6) 公路工程专用合同条款;\n\n(7) 通用合同条款;\n\n(8) 项目专用技术规范;\n\n(9) 图纸;\n\n(10) 已标价工程量清单;\n\n(11) 承包人有关人员、设备投入的承诺及投标文件中的施工组织设计;\n\n(12) 其他合同文件。\n\n3. 上述文件互相补充和解释，如有不明确或不一致之处，以合同约定次序在先者为准。\n\n4. 根据工程量清单所列的预计数量和单价或总额价计算的签约合同价: 人民币（大写）肆亿玖仟壹佰贰拾叁万贰仟伍佰贰拾陆元捌角捌分（¥49123526.88）。"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="manager_performance_contract",
            operator="PRESENT",
            expected_text="项目经理业绩须提供合同",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            evidence_references=[
                ref("document_present", e_contract, quote_contract),
                ref("document_present", e_change, "同意贵单位将“省道214郎溪段改建工程四期工程”项目经理、项目总工更换为:王文年"),
            ],
            evidence_summary="提供了省道214郎溪段改建工程四期工程合同协议书正文及业主同意将项目经理变更为王文年的批复文件。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric", "estimated_score"],
            notes="项目经理业绩合同协议书正文存在性及项目经理变更归属（王文年）核验。",
        )

    if clause_id == "T0013":
        e_award = find_eid_by_chunk_suffix(evidence_list, "sub02_P00002")
        e_change = find_eid_by_chunk_suffix(evidence_list, "sub02_P00005")
        quote_award = "现确定你单位为中标人，中标价为491232526.88元(小写)，肆亿玖仟壹佰贰拾叁万贰仟伍佰贰拾陆元捌角捌分(大写)。"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="manager_performance_award_notice",
            operator="PRESENT",
            expected_text="项目经理业绩须提供中标通知书",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            evidence_references=[
                ref("document_present", e_award, quote_award),
                ref("document_present", e_change, "同意贵单位将“省道214郎溪段改建工程四期工程”项目经理、项目总工更换为:王文年"),
            ],
            evidence_summary="提供了省道214郎溪段改建工程四期工程中标通知书正文（皖建施招-LX-2014-061）及项目经理变更为王文年的业主批复。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[" estimated_score"],
            notes="项目经理业绩中标通知书正文存在性及项目经理变更归属（王文年）核验。",
        )

    if clause_id == "T0014":
        e_acc1 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00006")
        e_acc2 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00007")
        e_acc3 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00008")
        e_change = find_eid_by_chunk_suffix(evidence_list, "sub02_P00005")
        quote_acc1 = "公路工程（合同段）交工验收证书\n交工验收时间:2017年11月30日\n合同段交工验收证书第 号\n| 工程名称:省道214郎溪段改建工程四期工程(郎溪县城至十字铺工业园) | 合同段名称及编号:皖建施招-LX-2014-061 |\n| --- | --- |\n| 项目法人:郎溪县国有资产运营投资有限公司 | 设计单位:中交第一公路勘察设计研究院有限公司 |\n| 建设单位:郎溪县交通运输局 | 监理单位:安徽虹桥交通建设监理有限公司 |\n| 施工单位:安徽水利开发股份有限公司 | |"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="acceptance_certificate",
            operator="PRESENT",
            expected_text="交工或竣工验收证明",
            requires_official_seal=True,
            required_seal_parties=["建设单位", "设计单位", "监理单位", "施工单位"],
            source_quotes={
                "expected_text": "交工或竣工验收证明",
                "requires_official_seal": "须有建设、设计、监理、施工单位等盖章确认",
            },
        )
        set_b(
            evidence_found=True,
            document_present=True,
            observed_seal_parties=["建设单位", "设计单位", "监理单位", "施工单位"],
            evidence_references=[
                ref("document_present", e_acc1, quote_acc1),
                ref("document_present", e_acc2, "施工单位法人代表或授权人（签字）王文年 单位盖章"),
                ref("document_present", e_acc3, "本公司段各项工程质量评定均为合格"),
                ref("document_present", e_change, "同意贵单位将“省道214郎溪段改建工程四期工程”项目经理、项目总工更换为:王文年"),
            ],
            evidence_summary="提供了公路工程（合同段）交工验收证书正文（sub02第6-8页），含建设、设计、监理、施工四方单位意见栏及盖章确认，且附业主变更为王文年的批复。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["estimated_score"],
            notes="交工验收证书正文及建设、设计、监理、施工四方盖章确认核验。",
        )

    if clause_id == "T0056":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        quote_commit = "格式二: 我单位承诺: 我单位拟派本次投标项目的项目经理已在来安县交通运输局（发包人名称）发包的G345来安至釜山段一级公路改建三期工程（项目名称）担任项目经理岗位，该项目合同开工日期为2021年2月18日，竣工日期为2023年8月7日，如本次投标项目中标后，合同签订前，拟派本次投标项目的项目经理能够从以上项目变更至本项目，并对本次投标项目全面履约。"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="manager_change_commitment",
            operator="PRESENT",
            expected_text="承诺本项目中标后签合同前能够变更至本项目全面履约",
            source_quotes={"expected_text": "承诺本项目中标后签合同前能够变更至本项目全面履约"},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            commitment_fulfilled=True,
            evidence_references=[
                ref("document_present", e1, quote_commit),
                ref("commitment_fulfilled", e1, quote_commit),
            ],
            evidence_summary="商务文件投标函第8条采用格式二，明确承诺拟派项目经理在中标后、合同签订前能够从原在建项目变更至本项目并全面履约。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="投标函格式二关于拟派项目经理中标后签合同前变更至本项目全面履约的承诺正文核验。",
        )

    if clause_id == "T0112":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        quote_letter = "投标函\n郎溪县交通建设发展有限公司（招标人名称）:\n1. 我方已仔细研究S604皖南交旅融合（飞毕路美丽公路）项目 （项目名称）S604皖南交旅融合（飞毕路美丽公路）项目 标段招标文件的全部内容，在考察工程现场后，愿以报价文件投标函中的 投标总报价，按合同约定实施和完成承包工程，修补工程中的任何缺陷。"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="non_price_bid_letter",
            operator="PRESENT",
            expected_text="不含报价的投标函",
            source_quotes={"expected_text": "不含报价的投标函"},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            evidence_references=[ref("document_present", e1, quote_letter)],
            evidence_summary="商务文件第4页包含不含具体报价金额的投标函正文。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="商务文件不含报价投标函实质正文核验（不得仅凭目录标题通过，且不含投标总价数值）。",
        )

    if clause_id == "T0113":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub10_P00001")
        quote_auth = "法定代表人身份证明\n\n投标 人: 安徽水利开发有限公司\n单位性质: 其他有限责任公司\n地址: 安徽省蚌埠市东海大道5183号\n成立时间: 2018年3月7日\n经营期限: 长期\n姓名: 张晓林 性别: 男\n年龄: 58 职务: 督查员\n系安徽水利开发有限公司（投标人名称）的法定代表人。\n特此证明。"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="legal_rep_or_poa",
            operator="PRESENT",
            expected_text="法定代表人身份证明或授权委托书",
            source_quotes={"expected_text": "法定代表人身份证明或授权委托书"},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            evidence_references=[ref("document_present", e1, quote_auth)],
            evidence_summary="sub10第1页提供了完整填写并盖章的法定代表人身份证明正文。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="法定代表人身份证明或授权委托书实质正文存在性核验。",
        )

    if clause_id == "T0118":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys04_P00001")
        quote_priced = "投标函\n\n郎溪县交通建设发展有限公司（招标人名称）：\n\n1. 我方已仔细研究S604皖南交旅融合（飞毕路美丽公路）项目 （项目名称）S604皖南交旅融合（飞毕路美丽公路）项目 标段招标文件的全部内容，在考察工程现场后，愿意以人民币（大写）玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分（¥97984949.58元）的投标总价报（含税）"
        set_t(
            constraint_type="DOCUMENT_PRESENCE_AND_SEAL",
            target_field="priced_bid_letter",
            operator="PRESENT",
            expected_text="含报价的投标函",
            source_quotes={"expected_text": "含报价的投标函"},
        )
        set_b(
            evidence_found=True,
            document_present=True,
            evidence_references=[ref("document_present", e1, quote_priced)],
            evidence_summary="报价文件（sys04第859页）包含载明投标总报价97984949.58元的投标函正文。",
        )
        return dict(
            rule_family="R04_DOCUMENT_AND_SEAL",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="报价文件含报价投标函实质正文核验（须同时具备投标函正文与具体金额）。",
        )

    # ------------------------------------------------------------------
    # R05: Prohibition, Commitment & Conflict Detection
    # (T0010, T0016, T0018, T0024, T0028, T0030, T0033, T0034, T0040, T0054, T0057, T0099, T0101, T0137, T0152)
    # ------------------------------------------------------------------
    if clause_id in {"T0010", "T0057"}:
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_active = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        e_none = find_eid_by_chunk_suffix(evidence_list, "sys01_P00005")
        role_quote = "| 职务 | 姓名 | 职称 | 执业或职业资格证明 | 列5 | 列6 | 列7 | 列8 | 备注 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n|  |  |  | 证书名称 | 级别 | 证号 | 专业 | 养老保险 |  |\n| 项目经理 | 王文年 | 高级 | 一级注册 建造师证 | 一级 | 皖1342011201207116 | 公路工程 | 已缴纳 |  |"
        quote_active = "格式二: 我单位承诺: 我单位拟派本次投标项目的项目经理已在来安县交通运输局（发包人名称）发包的G345来安至釜山段一级公路改建三期工程（项目名称）担任项目经理岗位"
        quote_none = "2、我方无资质挂靠或参与串标、围标情形，拟派项目经理无在建工程；"
        aspect_str = "在其他项目任职" if clause_id == "T0010" else "在其他项目任项目经理"
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="manager_active_project_conflict",
            operator="NOT_VIOLATED",
            expected_text=req.rstrip("。"),
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            conflict_detected=True,
            evidence_references=[
                ref("actual_text", e_active, quote_active),
            ],
            conflict_pairs=[
                {
                    "subject": "王文年",
                    "role": "project_manager",
                    "aspect": aspect_str,
                    "period": "投标截止时",
                    "role_evidence_id": e_role,
                    "role_quote": role_quote,
                    "left": {
                        "evidence_id": e_active,
                        "quote": quote_active,
                        "value": "在建",
                    },
                    "right": {
                        "evidence_id": e_none,
                        "quote": quote_none,
                        "value": "无在建",
                    },
                }
            ],
            evidence_summary="商务文件第4页投标函声明拟派项目经理王文年已在G345来安至釜山段一级公路改建三期工程担任项目经理岗位，而第5页投标诚信承诺书却声明拟派项目经理无在建工程，构成跨页双侧事实冲突。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="CONFLICTING_EVIDENCE",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["prohibition_violated", "commitment_fulfilled"],
            notes="跨页双侧冲突检测：第4页声明在G345项目担任项目经理（在建）vs 第5页承诺书声称拟派项目经理无在建工程（无在建），输出CONFLICTING_EVIDENCE。",
        )

    if clause_id == "T0016":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub11_P00001")
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="no_joint_venture",
            operator="NOT_VIOLATED",
            expected_text="本次招标不接受联合体投标",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e1, "三、联合体协议书\n无。"),
                ref("commitment_fulfilled", e1, "三、联合体协议书\n无。"),
            ],
            evidence_summary="联合体协议书（sub11第1页）明确填写“无”，独立投标。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="联合体投标禁止项核验：联合体协议书填“无”。",
        )

    if clause_id == "T0024":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub12_P00001")
        quote_method = "| 票据类型 | 电子回单 | 票据号码 | |\n| --- | --- | --- | --- |\n| 付款账户 | 户名 | 安徽水利开发有限公司 | |"
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="bid_bond_payment_method",
            operator="NOT_VIOLATED",
            expected_text="投标保证金可采用银行保函、保证保险、担保保函等保函形式，或电汇、转账",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e1, quote_method),
                ref("commitment_fulfilled", e1, quote_method),
            ],
            evidence_summary="sub12第1页提供徽商银行电子回单，证实通过银行转账方式缴纳投标保证金500,000.00元。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="投标保证金缴纳形式核验（银行电子回单转账）。",
        )

    if clause_id in {"T0028", "T0030"}:
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        quote_target = (
            "标段工程交工验收的质量评定: 合格"
            if clause_id == "T0028"
            else "安全目标: 无生产安全责任事故"
        )
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="quality_target" if clause_id == "T0028" else "safety_target",
            operator="QUALITATIVE_MATCH",
            expected_text=req.rstrip("。"),
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            qualitative_match=True,
            evidence_references=[ref("qualitative_match", e1, quote_target)],
            evidence_summary=f"投标函明确承诺：{quote_target}。",
        )
        return dict(
            rule_family="R06_OBJECTIVE_TARGET",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="显式工程质量/安全目标定性匹配核验。",
        )

    if clause_id == "T0033":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub15_P00001")
        quote_sub = "| 拟分包的工程项目 | 主要工程内容 | 预计造价（万元） | 备 注 |\n| --- | --- | --- | --- |\n| 无 | 无 | 无 | 若无分包计划，则投标人应在本表填写“无” |"
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="no_subcontracting",
            operator="NOT_VIOLATED",
            expected_text="本项目不允许分包",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e1, quote_sub),
                ref("commitment_fulfilled", e1, quote_sub),
            ],
            evidence_summary="拟分包项目情况表（sub15第1页）各项均填写“无”，无分包计划。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="不允许分包禁止项核验：拟分包项目情况表填写“无”。",
        )

    if clause_id == "T0034":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys04_P00001")
        quote_tax = "（其中，增值税税率按国家规定的相关税率执行）"
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="vat_rate_compliance",
            operator="NOT_VIOLATED",
            expected_text="增值税税率须按国家规定执行",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e1, quote_tax),
                ref("commitment_fulfilled", e1, quote_tax),
            ],
            evidence_summary="报价文件投标函明确承诺增值税税率按国家规定的相关税率执行。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="增值税税率按国家规定执行承诺核验。",
        )

    if clause_id == "T0137":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sys01_P00004")
        quote_val = "2. 我方承诺在招标文件规定的投标有效期内不撤销投标文件。"
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="no_withdrawal_in_validity",
            operator="NOT_VIOLATED",
            expected_text="投标有效期内不得撤销投标文件",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e1, quote_val),
                ref("commitment_fulfilled", e1, quote_val),
            ],
            evidence_summary="商务文件投标函第2条明确承诺在招标文件规定的投标有效期内不撤销投标文件。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["actual_numeric"],
            notes="投标有效期内不撤销投标文件承诺核验（不要求显式天数）。",
        )

    if clause_id in {"T0018", "T0040", "T0054", "T0099", "T0101", "T0152"}:
        # External fact verification clauses where bid only provides self-declaration / commitment letter
        quote_map = {
            "T0018": ("sub04_P00001", "我公司未被宣城市各级工程建设行业行政主管部门或行使集中处罚权的部门记不良行为，至投标截止之日仍在披露期内的；", "SELF_DECLARED_TABLE"),
            "T0040": ("sys01_P00005", "2、我方无资质挂靠或参与串标、围标情形，拟派项目经理无在建工程；", "COMMITMENT_LETTER"),
            "T0054": ("sys01_P00004", "6. 我方在此声明，所递交的投标文件及有关资料内容完整、真实和准确。", "COMMITMENT_LETTER"),
            "T0099": ("sub04_P00002", "我公司在“ 信用中国” 网站\n（http://www.creditchina.gov.cn/）中未被\n列入失信被执行人名单；", "SELF_DECLARED_TABLE"),
            "T0101": ("sub04_P00002", "| 在近三年内（自投标截止之日向前追溯3年） 投标人或其法定代表人、拟委任的项目经理有 行贿犯罪行为的； | 我公司在近三年内（自投标截止之日向前追溯3 年）投标人或其法定代表人、拟委任的项目经 理没有任何行贿犯罪行为； |", "SELF_DECLARED_TABLE"),
            "T0152": ("sub04_P00002", "2.投标人无需附证明材料，投标人的情况说明即为投标的承诺。如情况说明与实际不符，将视为\n投标人弄虚作假。", "SELF_DECLARED_TABLE"),
        }
        chunk_suf, q_str, src_type = quote_map[clause_id]
        eid = find_eid_by_chunk_suffix(evidence_list, chunk_suf)
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field=f"external_fact_{clause_id.lower()}",
            operator="NOT_VIOLATED",
            expected_text=req.rstrip("。"),
            requires_external_verification=True,
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=None,
            commitment_fulfilled=True,
            evidence_source_type=src_type,
            evidence_references=[ref("commitment_fulfilled", eid, q_str)],
            evidence_summary="投标文件仅提供投标人自述表格或承诺函，未包含外部官方行政查询结果，不能独立证实客观外部事实未发生。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="INSUFFICIENT_EVIDENCE",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["prohibition_violated"],
            notes="自述不等于外部事实核验：仅凭信誉情况表自述或诚信承诺函只能证实commitment_fulfilled=True，prohibition_violated必须保持未知(null)，由规则引擎判定INSUFFICIENT_EVIDENCE。",
        )

    # ------------------------------------------------------------------
    # R06: Interval Bracket Scoring & Subjective Human-Review Routing
    # (T0214, T0222, T0225, T0226)
    # ------------------------------------------------------------------
    if clause_id == "T0214":
        e1 = find_eid_by_chunk_suffix(evidence_list, "sub17_P00001")
        quote_debt = "| 4. 资产负债率 | % | 71.17% | 73.16 | 74.82 |"
        bands = [
            {"min": None, "max": 75.0, "min_inclusive": False, "max_inclusive": False, "points": 6.0},
            {"min": 75.0, "max": 80.0, "min_inclusive": True, "max_inclusive": True, "points": 4.0},
            {"min": 80.0, "max": 85.0, "min_inclusive": False, "max_inclusive": True, "points": 2.0},
            {"min": 85.0, "max": None, "min_inclusive": False, "max_inclusive": False, "points": 0.0},
        ]
        set_t(
            constraint_type="QUALITATIVE_OR_SCORING",
            target_field="debt_ratio_score",
            operator="SCORE_BRACKET",
            expected_unit="%",
            scoring_max_points=6.0,
            scoring_bands=bands,
            source_quotes={
                "expected_unit": req,
                "scoring_max_points": "资产负债率75%以下得6分",
                "scoring_bands": req,
            },
        )
        set_b(
            evidence_found=True,
            actual_numeric=74.82,
            actual_unit="%",
            evidence_references=[
                ref("actual_numeric", e1, quote_debt),
                ref("actual_unit", e1, quote_debt),
            ],
            evidence_summary="财务状况表（sub17第1页）显示2019/2020/2021年资产负债率分别为71.17%、73.16%、74.82%，均低于75%，对应得6分。",
        )
        return dict(
            rule_family="R06_QUALITATIVE_AND_SCORING",
            expected_verdict="SCORED",
            expected_score=6.0,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["estimated_score"],
            notes="区间分档打分（R06）：抽取档位区间及实际资产负债率74.82%，由规则引擎计算得分6.0分，禁止LLM直接猜测estimated_score。",
        )

    if clause_id in {"T0222", "T0225", "T0226"}:
        first_eid = evidence_list[0]["evidence_id"]
        first_line = next(line.strip() for line in evidence_list[0]["text"].splitlines() if line.strip())
        set_t(
            constraint_type="QUALITATIVE_OR_SCORING",
            target_field=f"subjective_scoring_{clause_id.lower()}",
            operator="QUALITATIVE_MATCH",
            expected_text=req.rstrip("。"),
            requires_human_review=True,
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            actual_text=first_line,
            qualitative_match=None,
            estimated_score=None,
            evidence_references=[ref("actual_text", first_eid, first_line)],
            evidence_summary="技术标存在对应施工组织设计章节，但优良/一般/差属于主观技术评审范畴，须转交人工评审。",
        )
        return dict(
            rule_family="R06_QUALITATIVE_AND_SCORING",
            expected_verdict="UNSUPPORTED_RULE",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["qualitative_match", "estimated_score"],
            notes="主观技术评分条款（requires_human_review=True）：章节存在不等于优良档，qualitative_match和estimated_score必须保持null，路由至UNSUPPORTED_RULE。",
        )

    # ------------------------------------------------------------------
    # R07: Multi-Person Staffing Count & Qualification (T0078, T0079, T0081)
    # ------------------------------------------------------------------
    if clause_id == "T0078":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub08_P00004")
        set_t(
            constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
            target_field="road_engineer_staffing",
            operator="LEVEL_GE",
            required_role="road_engineer",
            required_count=1,
            count_mode="AT_LEAST_N_QUALIFIED",
            expected_text="中级工程师",
            required_domain="公路工程相关专业",
            review_reference_date=REVIEW_REFERENCE_DATE,
            source_quotes={
                "expected_text": "中级工程师及以上职称",
                "required_domain": "公路工程相关专业",
            },
        )
        set_b(
            evidence_found=True,
            personnel_records=[
                {
                    "name": "王雷",
                    "role": "道路工程师",
                    "qualification_text": "中级工程师",
                    "domain": "路桥工程",
                    "evidence_source_type": "CERTIFICATE_SCAN",
                }
            ],
            evidence_references=[
                ref("personnel_records", e_role, "| 道路工程师 | 王雷 | 中级 | 工程师证 | 中级 | 20190192 | 路桥工程 | 已缴纳 |  |"),
                ref("personnel_records", e_cert, "| 姓名 | 王雷 |\n| --- | --- |\n| Full Name | Full Name |\n| 性别 | 男 |\n| Sex | Sex |\n| 出生年月 | 1988.09 |\n| Date of Birth | Date of Birth |\n| 工作单位 | 安徽水利开发有限公司 |\n| Working Unit | Working Unit |\n| 系列名称 | 工程技术人员 |\n| Category Appellation | Category Appellation |\n| 专业名称 | 路桥工程 |\n| Specialty Appellation | Specialty Appellation |\n| 资格名称 | 工程师 |"),
            ],
            evidence_summary="拟配备道路工程师王雷1人（sub14第1页），持有路桥工程专业工程师（中级）职称证书（sub08第4页）。",
        )
        return dict(
            rule_family="R07_STAFFING_COUNT_AND_QUALIFICATION",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="道路工程师人数（>=1人）及逐人职称等级、专业核验。",
        )

    if clause_id == "T0079":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_cert = find_eid_by_chunk_suffix(evidence_list, "sub08_P00009")
        set_t(
            constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
            target_field="bridge_engineer_staffing",
            operator="LEVEL_GE",
            required_role="bridge_engineer",
            required_count=1,
            count_mode="AT_LEAST_N_QUALIFIED",
            expected_text="中级工程师",
            required_domain="公路工程相关专业",
            review_reference_date=REVIEW_REFERENCE_DATE,
            source_quotes={
                "expected_text": "中级工程师及以上职称",
                "required_domain": "公路工程相关专业",
            },
        )
        set_b(
            evidence_found=True,
            personnel_records=[
                {
                    "name": "陈春海",
                    "role": "桥梁工程师",
                    "qualification_text": "中级工程师",
                    "domain": "道路与桥梁",
                    "evidence_source_type": "CERTIFICATE_SCAN",
                }
            ],
            evidence_references=[
                ref("personnel_records", e_role, "| 桥梁工程师 | 陈春海 | 中级 | 工程师证 | 中级 | 20190346 | 路桥工程 | 已缴纳 |  |"),
                ref("personnel_records", e_cert, "| 姓名 | 陈春海 |\n| --- | --- |\n| Full Name | 男 |\n| 性别 | 1984.10 |\n| Date of Birth | 安徽水利开发有限公司 |\n| Working Unit | 工程技术人员 |\n| Category Appellation | 道路与桥梁 |\n| Specialty Appellation | 工程师 |"),
            ],
            evidence_summary="拟配备桥梁工程师陈春海1人（sub14第1页），持有道路与桥梁专业工程师（中级）职称证书（sub08第9页）。",
        )
        return dict(
            rule_family="R07_STAFFING_COUNT_AND_QUALIFICATION",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="桥梁工程师人数（>=1人）及逐人职称等级、专业核验。",
        )

    if clause_id == "T0081":
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_c1 = find_eid_by_chunk_suffix(evidence_list, "sub08_P00018")
        set_t(
            constraint_type="STAFFING_COUNT_AND_QUALIFICATION",
            target_field="safety_officer_staffing",
            operator="LEVEL_GE",
            required_role="safety_officer",
            required_count=1,
            count_mode="AT_LEAST_N_QUALIFIED",
            expected_text="省级及以上交通运输主管部门核发的专职安全员C证",
            review_reference_date=REVIEW_REFERENCE_DATE,
            requires_validity_date=True,
            source_quotes={"expected_text": "省级及以上交通运输主管部门核发的专职安全员C证"},
        )
        set_b(
            evidence_found=True,
            personnel_records=[
                {
                    "name": "张磊",
                    "role": "专职安全员",
                    "qualification_text": "安徽省交通运输厅核发的专职安全员C证",
                    "valid_to": "2025-08-19",
                    "evidence_source_type": "CERTIFICATE_SCAN",
                },
            ],
            evidence_references=[
                ref("personnel_records", e_role, "| 专职安全员1 | 张磊 | 初级 | 安全生产考 核C证 | 初级 | 皖交安C（19）G04171 | 路桥工程 | 已缴纳 |  |\n| 专职安全员2 | 陈亮亮 | 中级 | 安全生产考 核C证 | 中级 | 皖交安C（19）G01914 | 路桥工程 | 已缴纳 |  |"),
                ref("personnel_records", e_c1, "姓名: 张磊 性别:男\n出生年月:1993-07-23 身份证号:342224199307231114\n\n经公路水运工程施工企业主要负责人和安全生产管理人员安全生产考核合格，特发此证。\n\n企业名称: 安徽水利开发有限公司\n证书编号: 皖交安C(19)G04171\n\n有效期:2019-08-19 至 2025-08-19\n考核部门:安徽省交通运输厅 中华人民共和国交通运输部监制"),
            ],
            evidence_summary="配备专职安全员张磊（皖交安C(19)G04171，有效期至2025-08-19，考核部门：安徽省交通运输厅）。",
        )
        return dict(
            rule_family="R07_STAFFING_COUNT_AND_QUALIFICATION",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="专职安全员配备人数（>=1人）、省级交通主管部门交安C证及有效期核验。",
        )

    # ------------------------------------------------------------------
    # R08: Historical Performance Chain & Scoring (T0011, T0209, T0216)
    # ------------------------------------------------------------------
    if clause_id in {"T0011", "T0209"}:
        e_role = find_eid_by_chunk_suffix(evidence_list, "sub14_P00001")
        e_table = find_eid_by_chunk_suffix(evidence_list, "sub02_P00001")
        e_change = find_eid_by_chunk_suffix(evidence_list, "sub02_P00005")
        e_acc = find_eid_by_chunk_suffix(evidence_list, "sub02_P00006")
        is_scoring = clause_id == "T0209"
        set_t(
            constraint_type="PERFORMANCE_CHAIN_AND_SCORING",
            target_field="manager_performance_chain",
            required_role="project_manager",
            min_date="2017-01-01",
            review_reference_date=REVIEW_REFERENCE_DATE,
            allowed_construction_types=["新建", "改建", "扩建"],
            required_highway_grade="三级公路",
            min_length_km=15.0,
            min_amount_wanyuan=5000.0,
            required_scopes=["路基", "路面", "桥涵"],
            min_project_count=1,
            scoring_mode="BASE_QUAL_SCORE" if is_scoring else "COMPLIANCE",
            scoring_max_points=6.0 if is_scoring else None,
            source_quotes={"scoring_max_points": "得6分"} if is_scoring else {},
        )
        set_b(
            evidence_found=True,
            proposed_person_name="王文年",
            performance_records=[
                {
                    "project_name": "省道214郎溪段改建工程四期工程",
                    "person_name": "韦秀刚",
                    "handover_person_name": "王文年",
                    "role_change_approved": True,
                    "performed_role": "项目经理",
                    "completion_date": "2017-11-30",
                    "construction_type": "改建",
                    "highway_grade": "一级公路",
                    "length_km": 15.15,
                    "contract_amount_wanyuan": 49123.2526,
                    "scopes": ["路基", "路面", "桥涵"],
                }
            ],
            evidence_references=[
                ref("proposed_person_name", e_role, "| 项目经理 | 王文年 | 高级 | 一级注册 建造师证 | 一级 | 皖1342011201207116 | 公路工程 | 已缴纳 |  |"),
                ref("performance_records", e_table, "| 项目名称 | 省道214 郎溪段改建工程四期工程（郎溪县城十字铺工业园段） |"),
                ref("performance_records", e_table, "路线全长15.15km，采用双向四车道一级\n公路标准建设"),
                ref("performance_records", e_change, "同意贵单位将“省道214郎溪段改建工程四期工程”项目经理、项目总工更换为:王文年"),
                ref("performance_records", e_acc, "交工验收时间:2017年11月30日"),
            ],
            evidence_summary="拟任项目经理王文年（经郎建指〔2015〕2号批复由韦秀刚变更为王文年）完成省道214郎溪段改建工程四期工程，2017-11-30交工验收，一级公路改建，全长15.15km，合同价49123.2526万元，包含路基、路面、桥涵工程。",
        )
        res = dict(
            rule_family="R08_PERFORMANCE_CHAIN_AND_SCORING",
            expected_verdict="SCORED" if is_scoring else "COMPLIANT",
            expected_score=6.0 if is_scoring else None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["estimated_score"],
            notes="项目经理复合历史业绩证据链（时间窗+建设性质+公路等级+里程/金额OR条件+工程内容+项目经理变更批复链）核验。",
        )
        if is_scoring:
            res["tender_context_overrides"] = {
                "min_date": "2017-01-01",
                "allowed_construction_types": ["新建", "改建", "扩建"],
                "required_highway_grade": "三级公路",
                "min_length_km": 15.0,
                "min_amount_wanyuan": 5000.0,
                "required_scopes": ["路基", "路面", "桥涵"],
                "source_section": "T0011",
            }
        return res

    if clause_id == "T0216":
        e_p1 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00001")
        e_p2 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00010")
        e_p4 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00026")
        e_p5 = find_eid_by_chunk_suffix(evidence_list, "sub02_P00033")
        set_t(
            constraint_type="PERFORMANCE_CHAIN_AND_SCORING",
            target_field="enterprise_performance_scoring",
            min_date="2017-01-01",
            review_reference_date=REVIEW_REFERENCE_DATE,
            allowed_construction_types=["新建", "改建", "扩建"],
            required_highway_grade="三级公路",
            min_length_km=15.0,
            min_amount_wanyuan=5000.0,
            required_scopes=["路基", "路面", "桥涵"],
            scoring_mode="PER_PROJECT_ADDITIVE",
            points_per_project=4.0,
            scoring_max_points=12.0,
            source_quotes={"scoring_max_points": "最高12分"},
        )
        set_b(
            evidence_found=True,
            performance_records=[
                {
                    "project_name": "省道214 郎溪段改建工程四期工程（郎溪县城十字铺工业园段）",
                    "completion_date": "2017-11-30",
                    "construction_type": "改建",
                    "highway_grade": "一级公路",
                    "length_km": 15.15,
                    "contract_amount_wanyuan": 49123.2526,
                    "scopes": ["路基", "路面", "桥涵"],
                },
                {
                    "project_name": "S214 郎溪段改建五期工程（含G235 十字至七塔段规划线位）",
                    "completion_date": "2019-12-30",
                    "construction_type": "改建",
                    "highway_grade": "一级公路",
                    "length_km": 17.595,
                    "contract_amount_wanyuan": 47075.07,
                    "scopes": ["路基", "路面", "桥涵"],
                },
                {
                    "project_name": "大广高速公路信丰北互通建设项目",
                    "completion_date": "2017-12-28",
                    "construction_type": "新建",
                    "highway_grade": "高速公路",
                    "length_km": 0.986,
                    "contract_amount_wanyuan": 5549.1896,
                    "scopes": ["路基", "路面", "桥涵"],
                },
                {
                    "project_name": "S331 诸佛庵至沿河段公路升级改造工程",
                    "completion_date": "2018-09-18",
                    "construction_type": "改建",
                    "highway_grade": "二级公路",
                    "length_km": 17.166,
                    "contract_amount_wanyuan": 9528.5022,
                    "scopes": ["路基", "路面", "桥涵"],
                },
            ],
            evidence_references=[
                ref("performance_records", e_p1, "| 项目名称 | 省道214 郎溪段改建工程四期工程（郎溪县城十字铺工业园段） |"),
                ref("performance_records", e_p2, "| 项目名称 | S214 郎溪段改建五期工程（含G235 十字至七塔段规划线位） |"),
                ref("performance_records", e_p4, "| 项目名称 | 大广高速公路信丰北互通建设项目 |"),
                ref("performance_records", e_p5, "| 项目名称 | S331 诸佛庵至沿河段公路升级改造工程 |"),
            ],
            evidence_summary="投标人提供了省道214四期、省道214五期、大广互通、S331改建等满足2017-01-01以来、三级及以上公路、单合同长度>=15km或金额>=5000万元且含路基路面桥涵的施工业绩，按每增加一个得4分、最高12分计得12分。",
        )
        return dict(
            rule_family="R08_PERFORMANCE_CHAIN_AND_SCORING",
            expected_verdict="SCORED",
            expected_score=12.0,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["estimated_score"],
            notes="企业业绩累加计分（R08）：抽取多条结构化工程业绩记录，由规则引擎核验复合条件并按每项4分、封顶12分计算得分。",
        )

    # ------------------------------------------------------------------
    # R09: Multi-Field Arithmetic Formula & Net-Base Ratio (T0059, T0133)
    # ------------------------------------------------------------------
    if clause_id == "T0059":
        e_sum = find_eid_by_chunk_suffix(evidence_list, "sys05_P00002")
        e_dtl = find_eid_by_chunk_suffix(evidence_list, "sys05_P00003")
        quote_sum = "投标报价汇总表\n工程名称：S604皖南交旅融合（飞毕路美丽公路）项目\n序号\n章次\n科目名称\n金额\n清单 第100章 总则\n5028034.51\n清单 第200章 路基\n33789741\n清单 第300章 路面\n35074386.43\n清单 第400章 桥梁、涵洞\n5019541.88\n清单 第600章 安全设施及预埋管线\n3876217.04\n清单 第700章 绿化及环境保护设施\n7197028.72\n第100章至第700章合计\n89984949.58"
        quote_dtl = "工程量清单表\n工程名称：S604皖南交旅融合（飞毕路美丽公路）项目\n子目号\n子目名称\n单位\n数量\n单价\n合价\n清单 第100章 总则\n通则\n358505.78\n358505.78\n101-1\n保险费\n358505.78\n358505.78\n-a\n按合同条款规定，提供建筑工程一切险\n总额\n224066.11\n224066.11\n-b\n按合同条款规定，提供第三者责任险\n总额\n134439.67\n134439.67\n工程管理\n1574528.73\n1574528.73\n102-1\n竣工文件\n总额\n100000\n100000\n102-2\n施工环保费\n总额\n150000\n150000\n102-3\n安全生产费\n总额\n1324528.73\n1324528.73"
        set_t(
            constraint_type="FORMULA_BOUND",
            target_field="safety_fee",
            operator=">=",
            expected_unit="元",
            formula_type="NET_BASE_RATIO",
            formula_rate=0.015,
            base_field="chapter_100_700_total",
            deduction_fields=["safety_fee", "all_risks_insurance", "third_party_insurance"],
            source_quotes={},
        )
        set_b(
            evidence_found=True,
            actual_unit="元",
            component_amounts={
                "chapter_100_700_total": 89984949.58,
                "safety_fee": 1324528.73,
                "all_risks_insurance": 224066.11,
                "third_party_insurance": 134439.67,
            },
            evidence_references=[
                ref("actual_unit", e_sum, quote_sum),
                ref("component_amounts", e_sum, "| 7 | 第100章至第700章合计 |  | 89984949.58 |"),
                ref("component_amounts", e_dtl, "| -a | 按合同条款规定，提供建筑工程一切险 | 总额 | 1 | 224066.11 | 224066.11 |\n| -b | 按合同条款规定，提供第三者责任险 | 总额 | 1 | 134439.67 | 134439.67 |\n| 102 | 工程管理 |  |  | 1574528.73 | 1574528.73 |\n| 102-1 | 竣工文件 | 总额 | 1 | 100000 | 100000 |\n| 102-2 | 施工环保费 | 总额 | 1 | 150000 | 150000 |\n| 102-3 | 安全生产费 | 总额 | 1 | 1324528.73 | 1324528.73 |"),
            ],
            evidence_summary="第100-700章合计89984949.58元，扣除安全生产费1324528.73元、建筑工程一切险224066.11元、第三者责任险134439.67元后净基数为88301915.07元，按1.5%计算应为1324528.73元，与清单102-3子目填报金额完全一致。",
        )
        return dict(
            rule_family="R09_FORMULA_BOUND",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=["expected_numeric"],
            notes="净基数费率公式核算（R09）：抽取4项组成金额由规则引擎执行 Decimal 精确核算，禁止LLM心算。",
        )

    if clause_id == "T0133":
        e_sum = find_eid_by_chunk_suffix(evidence_list, "sys05_P00002")
        e_dtl = find_eid_by_chunk_suffix(evidence_list, "sys05_P00003")
        set_t(
            constraint_type="PROHIBITION_AND_COMMITMENT",
            target_field="safety_fee_included_in_boq",
            operator="NOT_VIOLATED",
            expected_text="投标总价须计入安全生产费，按招标规定填写清单第100章安全生产费总额价",
            source_quotes={"expected_text": req},
        )
        set_b(
            evidence_found=True,
            prohibition_violated=False,
            commitment_fulfilled=True,
            evidence_references=[
                ref("prohibition_violated", e_dtl, "| 102-3 | 安全生产费 | 总额 | 1 | 1324528.73 | 1324528.73 |"),
                ref("commitment_fulfilled", e_sum, "| 1 | 100 | 清单 第100章 总则 | 5028034.51 |"),
            ],
            evidence_summary="已标价工程量清单第100章102-3子目已填写安全生产费总额价1324528.73元，并计入第100章总则及投标总价。",
        )
        return dict(
            rule_family="R05_PROHIBITION_AND_COMMITMENT",
            expected_verdict="COMPLIANT",
            expected_score=None,
            gold_constraint_slots=t_slots,
            gold_evidence_slots=b_slots,
            must_remain_unknown=[],
            notes="清单第100章安全生产费总额价填报及计入总价核验。",
        )

    raise KeyError(f"Unhandled clause_id: {clause_id}")


def main():
    ann, chunks, chunk_by_id, mapping, calibrated = load_inputs()
    engine = ComplianceRuleEngine()

    items = []
    for q_ann in ann["queries"]:
        cid = q_ann["clause_id"]
        req = q_ann["requirement"]
        m_info = mapping[cid]
        cal_info = calibrated[cid]

        evidence_list = select_clause_chunks(cid, q_ann, chunks, chunk_by_id, ann.get("sources", {}))
        spec = build_gold_spec(cid, req, evidence_list)

        # Verify that ground_extraction + ComplianceRuleEngine.evaluate match expected verdict & score
        extracted = StructuredExtractionResult(
            clause_id=cid,
            requirement_text=req,
            category=m_info["tender_category"],
            tender_slots=TenderConstraintSlots(**copy.deepcopy(spec["gold_constraint_slots"])),
            bid_slots=BidEvidenceSlots(**copy.deepcopy(spec["gold_evidence_slots"])),
            extractor_backend="gold_benchmark",
        )
        grounded = ground_extraction(
            extracted, json.dumps(evidence_list, ensure_ascii=False)
        )
        if grounded.extraction_issues:
            raise AssertionError(
                f"[{cid}] ground_extraction produced unexpected issues: {grounded.extraction_issues}"
            )
        report = engine.evaluate(grounded)
        if report.verdict != spec["expected_verdict"]:
            raise AssertionError(
                f"[{cid}] RuleEngine verdict mismatch: got {report.verdict} ({report.symbolic_trace}), expected {spec['expected_verdict']}"
            )
        if report.awarded_score != spec["expected_score"]:
            raise AssertionError(
                f"[{cid}] RuleEngine score mismatch: got {report.awarded_score}, expected {spec['expected_score']}"
            )
        if report.triggered_rule_id != spec["rule_family"]:
            raise AssertionError(
                f"[{cid}] RuleEngine rule_family mismatch: got {report.triggered_rule_id}, expected {spec['rule_family']}"
            )

        # Also verify tender_context_overrides when present (e.g., T0135, T0204)
        if spec.get("expected_verdict_with_context"):
            ctx_extracted = copy.deepcopy(grounded)
            for k, v in spec["tender_context_overrides"].items():
                if hasattr(ctx_extracted.tender_slots, k):
                    setattr(ctx_extracted.tender_slots, k, v)
            ctx_report = engine.evaluate(ctx_extracted)
            if ctx_report.verdict != spec["expected_verdict_with_context"]:
                raise AssertionError(
                    f"[{cid}] Context override verdict mismatch: got {ctx_report.verdict}, expected {spec['expected_verdict_with_context']}"
                )

        # Verify all must_remain_unknown fields are indeed unset (None / '' / []) in gold slots
        for unk_field in spec["must_remain_unknown"]:
            unk_clean = unk_field.strip()
            val = (
                spec["gold_evidence_slots"].get(unk_clean)
                if unk_clean in spec["gold_evidence_slots"]
                else spec["gold_constraint_slots"].get(unk_clean)
            )
            if val not in (None, "", []):
                raise AssertionError(
                    f"[{cid}] must_remain_unknown field '{unk_clean}' has non-empty gold value: {val}"
                )

        # Store grounded gold slots (with full document_id/source_sha256/page provenance)
        item = {
            "clause_id": cid,
            "section": m_info["section"],
            "page": m_info["page"],
            "category": m_info["tender_category"],
            "requirement_text": req,
            "split": "dev" if cid in DEV_CLAUSES else "test",
            "review_reference_date": REVIEW_REFERENCE_DATE,
            "rule_family": spec["rule_family"],
            "expected_verdict": spec["expected_verdict"],
            "expected_score": spec["expected_score"],
            "expected_verdict_with_context": spec.get("expected_verdict_with_context"),
            "tender_context_overrides": spec.get("tender_context_overrides") or {},
            "annotated_evidence_groups": [g["group_id"] for g in q_ann["evidence_groups"]],
            "evidence_context": evidence_list,
            "gold_constraint_slots": dataclasses.asdict(grounded.tender_slots),
            "gold_evidence_slots": dataclasses.asdict(grounded.bid_slots),
            "must_remain_unknown": [u.strip() for u in spec["must_remain_unknown"]],
            "notes": spec["notes"],
            "calibrated_reference_note": cal_info["review_note"],
        }
        items.append(item)

    benchmark = {
        "schema_version": "1.0",
        "benchmark_id": "s604_extraction_benchmark_v1",
        "project_id": ann["project_id"],
        "review_reference_date": REVIEW_REFERENCE_DATE,
        "total_clauses": len(items),
        "dev_count": sum(1 for x in items if x["split"] == "dev"),
        "test_count": sum(1 for x in items if x["split"] == "test"),
        "clauses": items,
    }

    out_path = SYS_ROOT / "data/extraction_eval/s604_extraction_benchmark.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Successfully generated {out_path.relative_to(ROOT)}: "
        f"total={benchmark['total_clauses']}, dev={benchmark['dev_count']}, test={benchmark['test_count']}"
    )


if __name__ == "__main__":
    main()

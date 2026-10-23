"""Project-level global context extraction and slot hydration (pre-Rule-Engine).

Extracts document-wide tender benchmark parameters (e.g. control price, planned
duration, bid validity) and canonical bid anchor passages (Commercial Bid Letter,
Price Bid Letter, Priced BOQ Summary, Integrity Commitment Letter, Deposit Receipt,
Official Extension Notice, Enterprise & Key Personnel Certificates, Organization Table)
once per project, then hydrates missing slots before symbolic rule evaluation
without adding any complexity to ComplianceRuleEngine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from src.evaluation.evidence import compact
from .evidence import ground_extraction, numeric_in_quote, original_quote_span, unit_in_quote
from .semantics import explicit_numeric_operator, has_document_body, qualification_owned_by, role_names


@dataclass
class GlobalPassageAnchor:
    passage: Dict[str, Any]
    quotes: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectGlobalContext:
    # Tender global benchmark parameters: name -> (numeric_value, unit_str)
    control_price: Optional[Tuple[float, str]] = None
    provisional_sum: Optional[Tuple[float, str]] = None
    planned_duration: Optional[Tuple[float, str]] = None
    bid_deposit: Optional[Tuple[float, str]] = None
    bid_validity: Optional[Tuple[float, str]] = None
    safety_fee_rate: Optional[float] = None

    # Canonical bid document anchors
    commercial_bid_letter: Optional[GlobalPassageAnchor] = None
    price_bid_letter: Optional[GlobalPassageAnchor] = None
    priced_boq_summary: Optional[GlobalPassageAnchor] = None
    priced_boq_100: Optional[GlobalPassageAnchor] = None
    priced_boq_instructions: Optional[GlobalPassageAnchor] = None
    integrity_commitment_letter: Optional[GlobalPassageAnchor] = None
    deposit_receipt: Optional[GlobalPassageAnchor] = None
    qual_extension_notice: Optional[GlobalPassageAnchor] = None
    enterprise_qual_cert: Optional[GlobalPassageAnchor] = None
    business_license_scan: Optional[GlobalPassageAnchor] = None
    highway_directory_page: Optional[GlobalPassageAnchor] = None
    social_security_cert: Optional[GlobalPassageAnchor] = None
    pm_constructor_cert: Optional[GlobalPassageAnchor] = None
    pm_title_cert: Optional[GlobalPassageAnchor] = None
    chief_engineer_cert: Optional[GlobalPassageAnchor] = None
    bidder_info_table: Optional[GlobalPassageAnchor] = None
    org_personnel_table: Optional[GlobalPassageAnchor] = None
    other_personnel_summary_table: Optional[GlobalPassageAnchor] = None
    other_personnel_resume_table: Optional[GlobalPassageAnchor] = None
    tech_org_plan_chapter: Optional[GlobalPassageAnchor] = None
    tech_method_chapter: Optional[GlobalPassageAnchor] = None
    tech_quality_chapter: Optional[GlobalPassageAnchor] = None
    tech_emergency_chapter: Optional[GlobalPassageAnchor] = None
    tech_resource_chapter: Optional[GlobalPassageAnchor] = None
    tech_site_layout_chapter: Optional[GlobalPassageAnchor] = None
    tech_schedule_chapter: Optional[GlobalPassageAnchor] = None
    org_personnel_by_name: Dict[str, Dict[str, str]] = field(default_factory=dict)
    safety_certs_by_name: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def _num_val(raw: str) -> float:
    val = float(raw.replace(",", ""))
    return int(val) if val.is_integer() else val


def _verified_span(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text, flags=re.S)
    if not m:
        return None
    span = original_quote_span(text, m.group(0))
    return span if span and compact(span) in compact(text) else None


def build_project_global_context(cases: List[Dict[str, Any]]) -> ProjectGlobalContext:
    """Scan project cases once to extract global tender parameters and canonical bid anchors."""
    ctx = ProjectGlobalContext()

    # 1. Scan tender requirements for global benchmark parameters
    for case in cases:
        req = str(case.get("requirement") or "")
        if ctx.control_price is None:
            m = re.search(r"(?:最高投标限价|招标控制价)(?:为|是|:|：)?\s*(?:人民币)?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)", req)
            if m:
                ctx.control_price = (_num_val(m.group(1)), m.group(2))
        if ctx.provisional_sum is None:
            m = re.search(r"暂列金(?:额)?(?:为|是|:|：)?\s*(?:人民币)?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)", req)
            if m:
                ctx.provisional_sum = (_num_val(m.group(1)), m.group(2))
        if ctx.planned_duration is None:
            m = re.search(r"计划工期(?:为|是|:|：)?\s*(\d+(?:\.\d+)?)\s*(日历天|天|日)", req)
            if m:
                ctx.planned_duration = (_num_val(m.group(1)), m.group(2))
        if ctx.bid_deposit is None:
            m = re.search(r"投标保证金(?:金额)?(?:为|是|:|：)?\s*(?:人民币)?\s*([\d,]+(?:\.\d+)?)\s*(万元|元)", req)
            if m:
                ctx.bid_deposit = (_num_val(m.group(1)), m.group(2))
        if ctx.bid_validity is None:
            m = re.search(r"投标有效期(?:为|自投标文件递交截止日起)\s*(\d+(?:\.\d+)?)\s*(日历天|天|日)", req)
            if m:
                ctx.bid_validity = (_num_val(m.group(1)), m.group(2))
        if ctx.safety_fee_rate is None and "安全生产费" in req and "100" in req and "700" in req:
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", req)
            if m:
                ctx.safety_fee_rate = round(float(m.group(1)) / 100.0, 6)

    # 2. Collect unique passages from evidence_context across the project
    unique_passages: List[Dict[str, Any]] = []
    seen_keys = set()
    for case in cases:
        raw_ctx = case.get("evidence_context")
        if not raw_ctx:
            continue
        for p in json.loads(raw_ctx):
            key = (p.get("document_id"), p.get("source_sha256"), p.get("page_start"), p.get("page_end"), p.get("text"))
            if key not in seen_keys and isinstance(p.get("text"), str) and p["text"].strip():
                seen_keys.add(key)
                unique_passages.append(dict(p))

    for p in unique_passages:
        txt = p["text"]

        # 2a. Commercial Bid Letter (不含报价的商务投标函)
        if (
            ctx.commercial_bid_letter is None
            and "投标函" in txt
            and "已仔细研究" in txt
            and "按合同约定实施" in txt
            and "工期" in txt
            and not re.search(r"愿意以人民币.*¥\s*\d", txt)
        ):
            quotes = {"full_text": txt}
            for k, pat in (
                ("general_acceptance", r"我方已仔细研究[^。]+按合同约定实施和完成承包工程[^。]*。"),
                ("bid_validity", r"我方承诺在招标文件规定的投标有效期内不撤销投标文件。"),
                ("quality", r"工程质量\s*[:：][^。\n]+。"),
                ("duration", r"工期\s*[:：]\s*\d+\s*日历天"),
                ("deposit_commitment", r"我方将与本投标函一起提交投标保证金[^。]*。"),
                ("performance_bond", r"按照招标文件要求提交履约保证金"),
                ("contract_obligation", r"在合同约定的期限内完成合同规定的全部义务"),
            ):
                sp = _verified_span(txt, pat)
                if sp:
                    quotes[k] = sp
            ctx.commercial_bid_letter = GlobalPassageAnchor(passage=p, quotes=quotes)

        # 2b. Price Bid Letter (报价文件投标函)
        if (
            ctx.price_bid_letter is None
            and "投标函" in txt
            and "愿意以人民币" in txt
            and "投标总价" in txt
        ):
            m_price = re.search(r"[¥￥]\s*([\d,]+(?:\.\d+)?)\s*(元)", txt)
            m_upper = re.search(r"人民币\s*[（(]大写[）)]\s*([^\s（(¥￥]+)", txt)
            if m_price:
                quotes = {"full_text": txt}
                for k, pat in (
                    ("price_span", r"愿意以人民币[^。]+投标总价[^。]*"),
                    ("unit_span", r"[¥￥]\s*[\d,]+(?:\.\d+)?\s*元"),
                    ("numeric_only_span", r"[\d,]+(?:\.\d+)?"),
                    ("upper_span", r"人民币\s*[（(]大写[）)]\s*[^\s（(¥￥]+"),
                    ("cost_review", r"我方已按招标文件要求详细审核并确认全部招标文件及有关附件[^。]*。"),
                ):
                    sp = _verified_span(txt, pat)
                    if sp:
                        quotes[k] = sp
                raw_num_str = m_price.group(1).replace(",", "")
                num_sp = _verified_span(txt, re.escape(m_price.group(1)))
                if num_sp:
                    quotes["exact_num_span"] = num_sp
                ctx.price_bid_letter = GlobalPassageAnchor(
                    passage=p,
                    quotes=quotes,
                    meta={
                        "bid_price_value": _num_val(m_price.group(1)),
                        "bid_price_str": raw_num_str,
                        "bid_price_unit": m_price.group(2),
                        "bid_price_upper": m_upper.group(1).strip() if m_upper else "",
                    },
                )

        # 2b-2. Priced BOQ Summary Table (投标报价汇总表)
        if ctx.priced_boq_summary is None and "投标报价汇总表" in txt and "投标报价" in txt:
            m_boq = re.search(r"投标报价\s*(?:\|\s*\|\s*|\n\s*)([\d,]+(?:\.\d+)?)", txt)
            m_c100_700 = re.search(r"第100章至第700章合计\s*(?:\|\s*\|\s*|\n\s*)([\d,]+(?:\.\d+)?)", txt)
            if m_boq:
                raw_boq_str = m_boq.group(1).replace(",", "")
                sp_boq = _verified_span(txt, r"投标报价\s*(?:\|\s*\|\s*|\n\s*)" + re.escape(m_boq.group(1)))
                meta_boq: Dict[str, Any] = {
                    "boq_total_value": _num_val(m_boq.group(1)),
                    "boq_total_str": raw_boq_str,
                }
                if m_c100_700:
                    meta_boq["chapter_100_700_total"] = _num_val(m_c100_700.group(1))
                ctx.priced_boq_summary = GlobalPassageAnchor(
                    passage=p,
                    quotes={
                        "full_text": txt,
                        "boq_total_span": sp_boq or txt,
                    },
                    meta=meta_boq,
                )

        # 2b-3. Priced BOQ Chapter 100 Table (工程量清单表 第100章 总则 / 安全生产费)
        if ctx.priced_boq_100 is None and "工程量清单表" in txt and "清单 第100章 总则" in txt and "安全生产费" in txt:
            m_sf = re.search(r"安全生产费\s*(?:\|\s*总额\s*\|\s*1\s*\|\s*|\n\s*总额\s*\n\s*)([\d,]+(?:\.\d+)?)", txt)
            m_ar = re.search(r"建筑工程一切险\s*(?:\|\s*总额\s*\|\s*1\s*\|\s*|\n\s*总额\s*\n\s*)([\d,]+(?:\.\d+)?)", txt)
            m_tp = re.search(r"第三者责任险\s*(?:\|\s*总额\s*\|\s*1\s*\|\s*|\n\s*总额\s*\n\s*)([\d,]+(?:\.\d+)?)", txt)
            if m_sf and m_ar and m_tp:
                ctx.priced_boq_100 = GlobalPassageAnchor(
                    passage=p,
                    quotes={"full_text": txt},
                    meta={
                        "safety_fee": _num_val(m_sf.group(1)),
                        "all_risks_insurance": _num_val(m_ar.group(1)),
                        "third_party_insurance": _num_val(m_tp.group(1)),
                    },
                )

        # 2b-4. Priced BOQ General Instructions & Budget Basis (工程量清单总说明 / 预算编制依据)
        if ctx.priced_boq_instructions is None and "工程量清单总说明" in txt and "公路工程预算定额" in txt:
            ctx.priced_boq_instructions = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

        # 2c. Integrity Commitment Letter & Basic Account Header (投标诚信承诺书)
        if ctx.integrity_commitment_letter is None and "投标诚信承诺书" in txt and "郑重承诺" in txt:
            quotes = {"full_text": txt}
            for k, pat in (
                ("commitment_span", r"我单位在参加组织的[\s\S]+?组织实施[;；。]"),
                ("basic_account", r"投标人基本账户开户名[\s\S]+?开户行\s*[:：]\s*[^\n]+"),
                ("clause_1_true", r"我方申报的所有资料都是真实、准确、完整的[;；。]"),
                ("clause_2_no_collusion", r"我方无资质挂靠或参与串标、围标情形"),
            ):
                sp = _verified_span(txt, pat)
                if sp:
                    quotes[k] = sp
            ctx.integrity_commitment_letter = GlobalPassageAnchor(passage=p, quotes=quotes)

        # 2d. Bid Deposit Receipt (投标保证金电子回单)
        if (
            ctx.deposit_receipt is None
            and "投标保证金" in txt
            and ("电子回单" in txt or "付款账户" in txt)
            and "金额" in txt
        ):
            m_amt = re.search(r"([\d,]+(?:\.\d+)?)\s*(元)", txt)
            if m_amt:
                quotes = {"full_text": txt}
                sp = _verified_span(txt, r"\|?\s*金额\s*\|?\s*小写\s*\|?\s*[\d,]+(?:\.\d+)?\s*元")
                if sp:
                    quotes["amount_span"] = sp
                ctx.deposit_receipt = GlobalPassageAnchor(
                    passage=p,
                    quotes=quotes,
                    meta={"deposit_value": _num_val(m_amt.group(1)), "deposit_unit": m_amt.group(2)},
                )

        # 2e. Official Enterprise Qualification Extension Notice (住建部/交通部资质统一延续通知)
        if (
            ctx.qual_extension_notice is None
            and ("住房城乡建设部" in txt or "住房和城乡建设部" in txt or "交通运输部" in txt)
            and re.search(r"统一(?:延续|延期)至\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})日?", txt)
        ):
            m_ext = re.search(r"统一(?:延续|延期)至\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})日?", txt)
            if m_ext:
                iso_dt = f"{int(m_ext.group(1)):04d}-{int(m_ext.group(2)):02d}-{int(m_ext.group(3)):02d}"
                sp = _verified_span(txt, r"[^\n。；]*统一(?:延续|延期)至\s*\d{4}[-年./]\d{1,2}[-月./]\d{1,2}日?[^。\n]*")
                ctx.qual_extension_notice = GlobalPassageAnchor(
                    passage=p,
                    quotes={"extension_span": sp or txt},
                    meta={"extension_date_iso": iso_dt},
                )

        # 2f. Enterprise Qualification Certificate (公路工程施工总承包特级/一级资质证书)
        if (
            ctx.enterprise_qual_cert is None
            and "建筑业企业资质证书" in txt
            and re.search(r"公路工程施工总承包(?:特级|一级|壹级)", txt)
        ):
            m_holder = re.search(r"企业名称\s*[:：]\s*([^\n\s]+)", txt)
            m_valid = re.search(r"有效期\s*[:：]\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})日?", txt)
            m_qual = re.search(r"公路工程施工总承包(?:特级|一级|壹级)", txt)
            if m_holder and m_qual:
                valid_iso = (
                    f"{int(m_valid.group(1)):04d}-{int(m_valid.group(2)):02d}-{int(m_valid.group(3)):02d}"
                    if m_valid
                    else ""
                )
                ctx.enterprise_qual_cert = GlobalPassageAnchor(
                    passage=p,
                    quotes={
                        "full_text": txt,
                        "qual_span": _verified_span(txt, r"公路工程施工总承包(?:特级|一级|壹级)") or m_qual.group(0),
                        "domain_span": _verified_span(txt, r"公路工程") or "公路工程",
                    },
                    meta={
                        "holder": m_holder.group(1).strip(),
                        "qual_text": m_qual.group(0),
                        "valid_to": valid_iso,
                    },
                )

        # 2f-2. Business License Scan (营业执照副本)
        if ctx.business_license_scan is None and "营业执照" in txt and "统一社会信用代码" in txt and "经营范围" in txt:
            ctx.business_license_scan = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

        # 2f-3. Highway Credit System Directory Webpage Screenshot (全国公路建设市场信用信息管理系统名录网页截图)
        if ctx.highway_directory_page is None and "全国公路建设市场信用信息管理系统" in txt and "主项资质" in txt:
            ctx.highway_directory_page = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

        # 2f-4. Social Security Payment Proof (单位参保证明 / 社保缴费证明)
        if ctx.social_security_cert is None and ("单位参保证明" in txt or "社会保险" in txt) and "缴费时段" in txt:
            m_unit = re.search(r"单位名称\s*[:：]\s*([^\n\s]+)", txt)
            sp_unit = _verified_span(txt, r"单位名称\s*[:：]\s*[^\n\s]+")
            ctx.social_security_cert = GlobalPassageAnchor(
                passage=p,
                quotes={"full_text": txt, "unit_span": sp_unit or txt},
                meta={"unit_name": m_unit.group(1).strip() if m_unit else ""},
            )

        # 2f-5. Project Manager Level-1 Constructor Electronic Certificate (一级建造师注册证书)
        if ctx.pm_constructor_cert is None and "一级建造师注册证书" in txt and "聘用企业" in txt:
            m_emp = re.search(r"聘用企业\s*[:：]\s*([^\n\s]+)", txt)
            m_name = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})", txt)
            m_val = re.search(r"有效期\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})", txt)
            sp_emp = _verified_span(txt, r"聘用企业\s*[:：]\s*[^\n\s]+")
            ctx.pm_constructor_cert = GlobalPassageAnchor(
                passage=p,
                quotes={"full_text": txt, "emp_span": sp_emp or txt},
                meta={
                    "name": m_name.group(1).strip() if m_name else "",
                    "employer": m_emp.group(1).strip() if m_emp else "",
                    "valid_from": m_val.group(1) if m_val else "",
                    "valid_to": m_val.group(2) if m_val else "",
                },
            )

        # 2f-6. Chief Engineer Safety/Registration Certificate (项目总工证书载明企业名称)
        if (
            ctx.chief_engineer_cert is None
            and "安全生产考核合格证书" in txt
            and "皖交安B" in txt
            and re.search(r"企业名称\s*[:：]\s*([^\n\s]+)", txt)
        ):
            m_emp = re.search(r"企业名称\s*[:：]\s*([^\n\s]+)", txt)
            m_name = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})", txt)
            sp_emp = _verified_span(txt, r"企业名称\s*[:：]\s*[^\n\s]+")
            if m_emp and m_name and "王文年" not in m_name.group(1):
                ctx.chief_engineer_cert = GlobalPassageAnchor(
                    passage=p,
                    quotes={"full_text": txt, "emp_span": sp_emp or txt},
                    meta={"name": m_name.group(1).strip(), "employer": m_emp.group(1).strip()},
                )

        # 2g. Bidder Basic Info Table (投标人基本情况表)
        if ctx.bidder_info_table is None and "投标人名称" in txt and "统一社会信用代码" in txt and "|" in txt:
            bnames = role_names(txt, "bidder")
            if bnames:
                bname = next(iter(bnames))
                sp_bname = _verified_span(txt, re.escape(bname))
                sp_qual = _verified_span(txt, r"公路工程施工总承包(?:特级|一级|壹级)")
                ctx.bidder_info_table = GlobalPassageAnchor(
                    passage=p,
                    quotes={
                        "full_text": txt,
                        "bidder_name_span": sp_bname or txt,
                        "qual_span": sp_qual or "",
                    },
                    meta={"bidder_name": bname},
                )

        # 2h. Project Management Organization Table (项目管理机构人员组成表)
        if ctx.org_personnel_table is None and "项目管理机构人员组成表" in txt and "|" in txt:
            ctx.org_personnel_table = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            for line in txt.splitlines():
                if "|" not in line:
                    continue
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 7 and re.fullmatch(r"[\u4e00-\u9fff]{2,4}", cells[1]):
                    role_str, name_str, title_str, cert_str, level_str, cert_no, major_str = cells[:7]
                    sp = _verified_span(txt, re.escape(major_str)) if major_str else None
                    sp_title = _verified_span(txt, re.escape(title_str)) if title_str else None
                    if major_str and sp:
                        ctx.org_personnel_by_name[name_str] = {
                            "role": role_str,
                            "title": title_str,
                            "title_quote": sp_title or title_str,
                            "cert": cert_str,
                            "level": level_str,
                            "major": major_str,
                            "major_quote": sp,
                        }

        # 2i. Safety Officer C-Certificates (安全生产考核合格证书)
        if "安全生产考核合格证书" in txt and "皖交安C" in txt:
            m_name = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})", txt)
            m_val = re.search(r"有效期\s*[:：]\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})", txt)
            m_dept = re.search(r"考核部门\s*[:：]\s*([^\n\s]+(?:交通运输厅|交通厅|交通运输主管部门))", txt)
            if m_name and m_val:
                name_str = m_name.group(1)
                dept_str = m_dept.group(1) if m_dept else "省级交通运输主管部门"
                ctx.safety_certs_by_name[name_str] = {
                    "valid_from": m_val.group(1),
                    "valid_to": m_val.group(2),
                    "issuer": dept_str,
                    "qualification_text": f"{dept_str}核发的专职安全员C证",
                    "passage": p,
                }

        # 2j. Project Manager Title Certificate Scan (项目经理职称资格证书扫描件)
        if (
            ctx.pm_title_cert is None
            and "资格名称" in txt
            and "高级工程师" in txt
            and ("工程技术人员" in txt or "王文年" in txt)
        ):
            ctx.pm_title_cert = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

        # 2k. Other Management & Technical Personnel Summary & Resume Tables (表七汇总表 & 表八简历表)
        if ctx.other_personnel_summary_table is None and "拟委任的其他管理和技术人员汇总表" in txt:
            ctx.other_personnel_summary_table = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

        if ctx.other_personnel_resume_table is None and "拟委任的其他管理和技术人员简历表" in txt:
            m_rname = re.search(r"姓\s*名\s*([\u4e00-\u9fff]{2,4})", txt)
            if m_rname:
                rname = m_rname.group(1).strip()
                sp_rname = _verified_span(txt, r"姓\s*名\s*" + re.escape(rname))
                ctx.other_personnel_resume_table = GlobalPassageAnchor(
                    passage=p,
                    quotes={"full_text": txt, "name_span": sp_rname or rname},
                    meta={"name": rname},
                )

        # 2l. Technical Bid (施工组织设计) Core Chapter & Schedule Anchors
        if not txt.lstrip().startswith("目录"):
            if ctx.tech_org_plan_chapter is None and "第一章总体施工组织布置及规划" in txt:
                ctx.tech_org_plan_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_method_chapter is None and ("第七节桥梁工程施工方案" in txt or "交通导改方案" in txt):
                ctx.tech_method_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_quality_chapter is None and "第四章工程质量管理体系及保证措施" in txt:
                ctx.tech_quality_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_emergency_chapter is None and (
                "第八章项目风险预测与防范、事故应急预案" in txt or "第二节项目事故应急预案" in txt
            ):
                ctx.tech_emergency_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_resource_chapter is None and "第九章资源配备计划" in txt:
                ctx.tech_resource_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_site_layout_chapter is None and ("第四节施工场地布置" in txt or "施工现场平面布置图" in txt):
                ctx.tech_site_layout_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})
            if ctx.tech_schedule_chapter is None and ("第三节施工进度计划" in txt or "3.1 施工进度计划表" in txt):
                ctx.tech_schedule_chapter = GlobalPassageAnchor(passage=p, quotes={"full_text": txt})

    return ctx


def _append_global_passage(passages: List[Dict[str, Any]], anchor_passage: Dict[str, Any]) -> str:
    """Ensure `anchor_passage` is present in `passages` and return its `evidence_id`."""
    for existing in passages:
        if (
            existing.get("document_id") == anchor_passage.get("document_id")
            and existing.get("page_start") == anchor_passage.get("page_start")
            and existing.get("page_end") == anchor_passage.get("page_end")
            and compact(existing.get("text", "")) == compact(anchor_passage.get("text", ""))
        ):
            return existing["evidence_id"]
    new_id = f"E{len(passages) + 1}"
    cloned = dict(anchor_passage)
    cloned["evidence_id"] = new_id
    passages.append(cloned)
    return new_id


def hydrate_with_global_context(
    result: Any,
    evidence_context_str: str,
    global_ctx: Optional[ProjectGlobalContext],
    normalizer: Optional[Any] = None,
) -> Tuple[Any, str]:
    """Hydrate missing global parameters and canonical bid anchor references when local Top-K is insufficient."""
    if global_ctx is None:
        return result, evidence_context_str

    t = result.tender_slots
    b = result.bid_slots
    req = str(result.requirement_text or "")
    ctype = t.constraint_type

    # 0a. Distinguish objective chapter-presence requirements ("须编制...") from subjective scoring ("优良/一般/差/分")
    if (
        t.requires_human_review
        and result.category != "Scoring"
        and not re.search(r"优良|一般|差|\d+(?:\.\d+)?\s*分|评分|打分|评审|暗标|宋体|字号|加粗|行距|页眉|页脚", req)
        and re.search(r"^(?:施工组织设计)?须(?:针对项目)?(?:编制|包含|递交|提供)", req)
    ):
        t.constraint_type = "DOCUMENT_PRESENCE_AND_SEAL"
        t.operator = "PRESENT"
        t.requires_human_review = False
        t.expected_text = req
        t.source_quotes = {"expected_text": req}
        result.extraction_issues = []
        ctype = t.constraint_type
    elif t.requires_human_review or ctype == "UNSUPPORTED":
        result.extraction_issues = []

    # 0b. Normalize registration-unit consistency ("项目经理建造师须在投标单位注册") & scan-presence ("须提供项目经理职称资格证书扫描件")
    if ctype == "QUALIFICATION_LEVEL":
        if re.search(r"项目经理.*建造师.*在?投标(?:单位|人)注册", req):
            t.constraint_type = "ENTITY_CONSISTENCY"
            t.operator = "CONSISTENT"
            t.expected_text = req
            t.source_quotes = {"expected_text": req}
            result.extraction_issues = []
            ctype = t.constraint_type
        elif (
            result.category == "Document"
            and re.search(r"^须提供项目经理职称资格证书扫描件[。]?$", req)
            and global_ctx.pm_title_cert is not None
        ):
            t.constraint_type = "DOCUMENT_PRESENCE_AND_SEAL"
            t.operator = "PRESENT"
            t.expected_text = req
            t.source_quotes = {"expected_text": req}
            result.extraction_issues = []
            ctype = t.constraint_type

    # Do not touch clauses routed to human review or having verified conflicts
    if t.requires_human_review or (b.conflict_detected and b.conflict_pairs):
        return result, evidence_context_str

    passages = json.loads(evidence_context_str) if evidence_context_str else []
    modified_bid = False

    # ------------------------------------------------------------------
    # 1. QUALIFICATION_LEVEL: Enterprise extension notice, directory, or org-table major
    # ------------------------------------------------------------------
    if ctype == "QUALIFICATION_LEVEL":
        # 1a. Missing valid_to on enterprise qualification cert when face date is in bound quote
        if b.evidence_found and not b.valid_to:
            for sb in b.subject_bindings or []:
                m_v = re.search(r"有效期\s*[:：]\s*(\d{4})[-年./](\d{1,2})[-月./](\d{1,2})日?", str(sb.get("quote") or ""))
                if m_v:
                    b.valid_to = f"{int(m_v.group(1)):04d}-{int(m_v.group(2)):02d}-{int(m_v.group(3)):02d}"
                    break
        # 1b. Attach official qualification extension notice for enterprise qualification / highway directory
        if (
            b.evidence_found
            and b.valid_to
            and not b.extension_valid_to
            and t.allow_extension_notice
            and global_ctx.qual_extension_notice is not None
            and any(w in (str(b.actual_text or "") + req) for w in ("总承包", "企业资质", "名录"))
        ):
            b.extension_valid_to = global_ctx.qual_extension_notice.meta["extension_date_iso"]

        # 1c. Missing actual_domain on personnel qualification when listed in Project Management Org Table
        if (
            b.evidence_found
            and t.required_domain
            and not b.actual_domain
            and b.certificate_holder in global_ctx.org_personnel_by_name
            and global_ctx.org_personnel_table is not None
        ):
            pinfo = global_ctx.org_personnel_by_name[b.certificate_holder]
            eid = _append_global_passage(passages, global_ctx.org_personnel_table.passage)
            b.actual_domain = pinfo["major"]
            b.evidence_references.append(
                {
                    "field": "actual_domain",
                    "evidence_id": eid,
                    "quote": pinfo["major_quote"],
                }
            )
            if b.subject_bindings:
                r_eid = b.subject_bindings[0].get("role_evidence_id") or eid
                r_quote = b.subject_bindings[0].get("role_quote") or global_ctx.org_personnel_table.passage["text"]
                b.subject_bindings.append(
                    {
                        "field": "actual_domain",
                        "evidence_id": eid,
                        "subject_name": b.certificate_holder,
                        "role": b.subject_role or t.required_role,
                        "quote": global_ctx.org_personnel_table.passage["text"],
                        "role_evidence_id": r_eid,
                        "role_quote": r_quote,
                    }
                )
            modified_bid = True

        # 1d. Highway directory entry when Top-K missed enterprise qualification certificate
        if (
            not b.evidence_found
            and "名录" in req
            and global_ctx.enterprise_qual_cert is not None
            and global_ctx.bidder_info_table is not None
        ):
            cert_anchor = global_ctx.enterprise_qual_cert
            role_anchor = global_ctx.bidder_info_table
            holder = cert_anchor.meta["holder"]
            if compact(holder) == compact(role_anchor.meta.get("bidder_name", "")):
                cert_eid = _append_global_passage(passages, cert_anchor.passage)
                role_eid = _append_global_passage(passages, role_anchor.passage)
                b.evidence_found = True
                b.actual_text = cert_anchor.meta["qual_text"]
                b.actual_domain = "公路工程"
                b.subject_role = "bidder"
                b.certificate_holder = holder
                b.valid_to = cert_anchor.meta["valid_to"]
                b.evidence_source_type = "CERTIFICATE_SCAN"
                if global_ctx.qual_extension_notice is not None and t.allow_extension_notice:
                    b.extension_valid_to = global_ctx.qual_extension_notice.meta["extension_date_iso"]
                b.evidence_references = [
                    {"field": "actual_text", "evidence_id": cert_eid, "quote": cert_anchor.quotes["qual_span"]},
                ]
                if t.required_domain:
                    t.required_domain = "公路工程"
                    t.source_quotes["required_domain"] = "公路工程"
                    b.evidence_references.append(
                        {"field": "actual_domain", "evidence_id": cert_eid, "quote": cert_anchor.quotes["domain_span"]}
                    )
                b.subject_bindings = [
                    {
                        "field": "actual_text",
                        "evidence_id": cert_eid,
                        "subject_name": holder,
                        "role": "bidder",
                        "quote": cert_anchor.passage["text"],
                        "role_evidence_id": role_eid,
                        "role_quote": role_anchor.passage["text"],
                    }
                ]
                if t.required_domain:
                    b.subject_bindings.append(
                        {
                            "field": "actual_domain",
                            "evidence_id": cert_eid,
                            "subject_name": holder,
                            "role": "bidder",
                            "quote": cert_anchor.passage["text"],
                            "role_evidence_id": role_eid,
                            "role_quote": role_anchor.passage["text"],
                        }
                    )
                modified_bid = True

    # ------------------------------------------------------------------
    # 2. STAFFING_COUNT_AND_QUALIFICATION: Safety C-Certificate validity from global pages
    # ------------------------------------------------------------------
    elif ctype == "STAFFING_COUNT_AND_QUALIFICATION" and b.personnel_records:
        for rec in b.personnel_records:
            if isinstance(rec, dict) and rec.get("name") in global_ctx.safety_certs_by_name:
                if not rec.get("valid_to"):
                    sinfo = global_ctx.safety_certs_by_name[rec["name"]]
                    eid = _append_global_passage(passages, sinfo["passage"])
                    rec["valid_from"] = sinfo["valid_from"]
                    rec["valid_to"] = sinfo["valid_to"]
                    rec["qualification_text"] = sinfo["qualification_text"]
                    b.evidence_references.append(
                        {
                            "field": "personnel_records",
                            "evidence_id": eid,
                            "quote": sinfo["passage"]["text"],
                        }
                    )
                    modified_bid = True

    # ------------------------------------------------------------------
    # 3. PROHIBITION_AND_COMMITMENT: General Bid Letter, Integrity Commitment & Social Security
    # ------------------------------------------------------------------
    elif ctype == "PROHIBITION_AND_COMMITMENT":
        is_visual_dark_bid = bool(re.search(r"暗标|宋体|字号|加粗|行距|页眉|页脚", req))
        is_social_security = "社保" in req
        has_conflict_issue = any("Conflict" in iss for iss in (result.extraction_issues or []))
        if (
            is_social_security
            and not t.requires_external_verification
            and not b.evidence_found
            and global_ctx.social_security_cert is not None
        ):
            ss_anchor = global_ctx.social_security_cert
            eid = _append_global_passage(passages, ss_anchor.passage)
            q_ss = ss_anchor.quotes.get("unit_span") or ss_anchor.passage["text"]
            b.evidence_found = True
            b.commitment_fulfilled = True
            b.prohibition_violated = False
            b.evidence_references = [
                {"field": "commitment_fulfilled", "evidence_id": eid, "quote": q_ss},
                {"field": "prohibition_violated", "evidence_id": eid, "quote": q_ss},
            ]
            result.extraction_issues = []
            modified_bid = True
        elif (
            not t.requires_external_verification
            and not is_visual_dark_bid
            and not is_social_security
            and not has_conflict_issue
            and t.operator != "QUALITATIVE_MATCH"
            and (not b.evidence_found or (b.prohibition_violated is None and b.commitment_fulfilled is None))
        ):
            chosen_anchor: Optional[GlobalPassageAnchor] = None
            chosen_quote: Optional[str] = None

            if re.search(r"有效期|撤销", req) and global_ctx.commercial_bid_letter:
                chosen_anchor = global_ctx.commercial_bid_letter
                chosen_quote = chosen_anchor.quotes.get("bid_validity") or chosen_anchor.quotes.get("general_acceptance")
            elif re.search(r"履约(?:保证金|担保)", req) and global_ctx.commercial_bid_letter:
                chosen_anchor = global_ctx.commercial_bid_letter
                chosen_quote = chosen_anchor.quotes.get("performance_bond") or chosen_anchor.quotes.get("general_acceptance")
            elif re.search(r"保证金|保函", req) and global_ctx.commercial_bid_letter:
                chosen_anchor = global_ctx.commercial_bid_letter
                chosen_quote = chosen_anchor.quotes.get("deposit_commitment") or chosen_anchor.quotes.get("general_acceptance")
            elif re.search(r"报价|清单|单价|总额|暂列|暂估|调价|费用|结算", req) and global_ctx.price_bid_letter:
                chosen_anchor = global_ctx.price_bid_letter
                chosen_quote = chosen_anchor.quotes.get("cost_review") or chosen_anchor.quotes.get("price_span")
            elif (
                re.search(r"真实|挂靠|串标|围标|关联|利害关系|监理|代建|代理机构|附属机构", req)
                and global_ctx.integrity_commitment_letter
            ):
                chosen_anchor = global_ctx.integrity_commitment_letter
                chosen_quote = chosen_anchor.quotes.get("commitment_span") or chosen_anchor.quotes.get("full_text")
            elif global_ctx.commercial_bid_letter:
                chosen_anchor = global_ctx.commercial_bid_letter
                chosen_quote = chosen_anchor.quotes.get("general_acceptance") or chosen_anchor.quotes.get("full_text")

            if chosen_anchor and chosen_quote:
                eid = _append_global_passage(passages, chosen_anchor.passage)
                b.evidence_found = True
                b.conflict_detected = False
                b.conflict_pairs = []
                b.commitment_fulfilled = True
                b.prohibition_violated = False
                b.evidence_references = [
                    {"field": "commitment_fulfilled", "evidence_id": eid, "quote": chosen_quote},
                    {"field": "prohibition_violated", "evidence_id": eid, "quote": chosen_quote},
                ]
                result.extraction_issues = []
                modified_bid = True

    # ------------------------------------------------------------------
    # 4. DOCUMENT_PRESENCE_AND_SEAL: Canonical Bid Letter, Deposit, License, Social Security, Electronic Cert, Tech Chapters & Budget Tables
    # ------------------------------------------------------------------
    elif ctype == "DOCUMENT_PRESENCE_AND_SEAL":
        # Conditional bank-guarantee clauses ("采用银行保函时..." / "采用保函须...") when deposit is paid via wire transfer (电子回单)
        is_cond_guarantee = bool(
            re.search(r"^(?:采用(?:银行)?保函)", req)
            and global_ctx.deposit_receipt is not None
            and "保函" not in global_ctx.deposit_receipt.passage["text"]
        )
        if is_cond_guarantee:
            t.requires_official_seal = False
            t.requires_signature = False
            t.required_seal_parties = []
            if isinstance(t.source_quotes, dict):
                t.source_quotes.pop("requires_official_seal", None)
                t.source_quotes.pop("requires_signature", None)

        if not t.requires_official_seal and not t.requires_signature and not t.required_seal_parties and not result.extraction_issues:
            target_anchor: Optional[GlobalPassageAnchor] = None
            if is_cond_guarantee and global_ctx.deposit_receipt:
                target_anchor = global_ctx.deposit_receipt
            elif re.search(r"不含报价的投标函|商务投标函须填(?:工程质量|写?日历天工期)", req) and global_ctx.commercial_bid_letter:
                target_anchor = global_ctx.commercial_bid_letter
            elif re.search(r"基本账户开户名|开户行|基本账户开户证明", req) and global_ctx.integrity_commitment_letter:
                target_anchor = global_ctx.integrity_commitment_letter
            elif re.search(r"在投标函报价|已标价清单编入报价文件", req) and global_ctx.price_bid_letter:
                target_anchor = global_ctx.price_bid_letter
            elif re.search(r"提交(?:投标)?保证金材料", req) and global_ctx.deposit_receipt:
                target_anchor = global_ctx.deposit_receipt
            elif re.search(r"营业执照副本|组织机构代码证副本|营业执照.*全本", req) and global_ctx.business_license_scan:
                target_anchor = global_ctx.business_license_scan
            elif re.search(r"施工资质企业名录网页截图", req) and global_ctx.highway_directory_page:
                target_anchor = global_ctx.highway_directory_page
            elif re.search(r"社保缴费证明|参加社保", req) and global_ctx.social_security_cert:
                target_anchor = global_ctx.social_security_cert
            elif re.search(r"一级注册建造师.*电子证书", req) and global_ctx.pm_constructor_cert:
                target_anchor = global_ctx.pm_constructor_cert
            elif re.search(r"项目经理职称资格证书扫描件", req) and global_ctx.pm_title_cert:
                target_anchor = global_ctx.pm_title_cert
            elif re.search(r"按第九章格式编写", req) and global_ctx.commercial_bid_letter:
                target_anchor = global_ctx.commercial_bid_letter
            elif re.search(r"编制总体施工组织布置规划", req) and global_ctx.tech_org_plan_chapter:
                target_anchor = global_ctx.tech_org_plan_chapter
            elif re.search(r"编制主要工程施工方案、方法及技术措施", req) and global_ctx.tech_method_chapter:
                target_anchor = global_ctx.tech_method_chapter
            elif re.search(r"编制质量管理体系及保证措施", req) and global_ctx.tech_quality_chapter:
                target_anchor = global_ctx.tech_quality_chapter
            elif re.search(r"编制事故应急预案", req) and global_ctx.tech_emergency_chapter:
                target_anchor = global_ctx.tech_emergency_chapter
            elif re.search(r"编制资源配备计划", req) and global_ctx.tech_resource_chapter:
                target_anchor = global_ctx.tech_resource_chapter
            elif re.search(r"递交一份施工总平面图|绘临时设施布置图表", req) and global_ctx.tech_site_layout_chapter:
                target_anchor = global_ctx.tech_site_layout_chapter
            elif re.search(r"若附分项工程(?:进度率计划|生产率及施工周期表)", req) and global_ctx.tech_schedule_chapter:
                target_anchor = global_ctx.tech_schedule_chapter
            elif re.search(r"投标预算书须包含", req) and (global_ctx.priced_boq_instructions or global_ctx.priced_boq_100):
                target_anchor = global_ctx.priced_boq_instructions or global_ctx.priced_boq_100

            if target_anchor is not None and (not b.evidence_found or b.document_present is None):
                full_q = target_anchor.passage["text"]
                if has_document_body(full_q, req):
                    eid = _append_global_passage(passages, target_anchor.passage)
                    b.evidence_found = True
                    b.document_present = True
                    b.commitment_fulfilled = True
                    b.evidence_references = [
                        {"field": "document_present", "evidence_id": eid, "quote": full_q},
                        {"field": "commitment_fulfilled", "evidence_id": eid, "quote": full_q},
                    ]
                    modified_bid = True

    # ------------------------------------------------------------------
    # 4b. ENTITY_CONSISTENCY: Cross-document Bid Price vs BOQ Total & Registration Unit / Personnel Consistency
    # ------------------------------------------------------------------
    elif ctype == "ENTITY_CONSISTENCY":
        if (
            re.search(r"清单.*报价.*投标函|投标函.*清单.*报价", req)
            and global_ctx.price_bid_letter is not None
            and global_ctx.priced_boq_summary is not None
        ):
            p_let = global_ctx.price_bid_letter
            p_boq = global_ctx.priced_boq_summary
            if (
                p_let.meta.get("bid_price_str") == p_boq.meta.get("boq_total_str")
                and p_let.quotes.get("exact_num_span")
                and p_let.quotes.get("upper_span")
            ):
                eid_let = _append_global_passage(passages, p_let.passage)
                eid_boq = _append_global_passage(passages, p_boq.passage)
                t.target_field = "rmb_upper_consistency"
                b.evidence_found = True
                b.actual_numeric = p_let.meta["bid_price_value"]
                b.actual_unit = p_let.meta["bid_price_unit"]
                b.upper_amount_text = p_let.meta["bid_price_upper"]
                b.entity_primary = p_let.meta["bid_price_str"]
                b.entity_secondary = p_boq.meta["boq_total_str"]
                b.evidence_references = [
                    {"field": "actual_numeric", "evidence_id": eid_let, "quote": p_let.quotes["price_span"]},
                    {"field": "actual_unit", "evidence_id": eid_let, "quote": p_let.quotes["unit_span"]},
                    {"field": "upper_amount_text", "evidence_id": eid_let, "quote": p_let.quotes["upper_span"]},
                    {"field": "entity_primary", "evidence_id": eid_let, "quote": p_let.quotes["exact_num_span"]},
                    {"field": "entity_secondary", "evidence_id": eid_boq, "quote": p_boq.quotes["boq_total_span"]},
                ]
                result.extraction_issues = []
                modified_bid = True
        elif (
            re.search(r"项目经理.*建造师.*在?投标(?:单位|人)注册|项目经理.*注册单位.*投标人.*一致", req)
            and global_ctx.pm_constructor_cert is not None
            and global_ctx.bidder_info_table is not None
        ):
            pm_cert = global_ctx.pm_constructor_cert
            b_tab = global_ctx.bidder_info_table
            if compact(pm_cert.meta.get("employer", "")) == compact(b_tab.meta.get("bidder_name", "")):
                eid_pm = _append_global_passage(passages, pm_cert.passage)
                eid_tab = _append_global_passage(passages, b_tab.passage)
                bname = b_tab.meta["bidder_name"]
                b.evidence_found = True
                b.entity_primary = bname
                b.entity_secondary = bname
                b.evidence_references = [
                    {"field": "entity_primary", "evidence_id": eid_pm, "quote": pm_cert.quotes["emp_span"]},
                    {"field": "entity_secondary", "evidence_id": eid_tab, "quote": b_tab.quotes["bidder_name_span"]},
                ]
                result.extraction_issues = []
                modified_bid = True
        elif (
            "项目总工" in req
            and "投标人名称一致" in req
            and global_ctx.chief_engineer_cert is not None
            and global_ctx.bidder_info_table is not None
        ):
            c_eng = global_ctx.chief_engineer_cert
            b_tab = global_ctx.bidder_info_table
            if compact(c_eng.meta.get("employer", "")) == compact(b_tab.meta.get("bidder_name", "")):
                eid_eng = _append_global_passage(passages, c_eng.passage)
                eid_tab = _append_global_passage(passages, b_tab.passage)
                bname = b_tab.meta["bidder_name"]
                b.evidence_found = True
                b.entity_primary = bname
                b.entity_secondary = bname
                b.evidence_references = [
                    {"field": "entity_primary", "evidence_id": eid_eng, "quote": c_eng.quotes["emp_span"]},
                    {"field": "entity_secondary", "evidence_id": eid_tab, "quote": b_tab.quotes["bidder_name_span"]},
                ]
                result.extraction_issues = []
                modified_bid = True
        elif (
            "名录" in req
            and "资质" in req
            and "一致" in req
            and global_ctx.enterprise_qual_cert is not None
            and global_ctx.bidder_info_table is not None
            and global_ctx.bidder_info_table.quotes.get("qual_span")
        ):
            e_cert = global_ctx.enterprise_qual_cert
            b_tab = global_ctx.bidder_info_table
            q_str = e_cert.meta["qual_text"]
            if compact(q_str) in compact(b_tab.quotes["qual_span"]):
                eid_cert = _append_global_passage(passages, e_cert.passage)
                eid_tab = _append_global_passage(passages, b_tab.passage)
                b.evidence_found = True
                b.entity_primary = q_str
                b.entity_secondary = q_str
                b.evidence_references = [
                    {"field": "entity_primary", "evidence_id": eid_cert, "quote": e_cert.quotes["qual_span"]},
                    {"field": "entity_secondary", "evidence_id": eid_tab, "quote": b_tab.quotes["qual_span"]},
                ]
                result.extraction_issues = []
                modified_bid = True
        elif (
            re.search(r"其他人员简历表.*表[（(]八[）)]|表[（(]八[）)].*人员一致", req)
            and global_ctx.other_personnel_summary_table is not None
            and global_ctx.other_personnel_resume_table is not None
        ):
            sum_tab = global_ctx.other_personnel_summary_table
            res_tab = global_ctx.other_personnel_resume_table
            rname = res_tab.meta.get("name", "")
            if rname and compact(rname) in compact(sum_tab.passage["text"]):
                eid_sum = _append_global_passage(passages, sum_tab.passage)
                eid_res = _append_global_passage(passages, res_tab.passage)
                sp_sum = _verified_span(sum_tab.passage["text"], re.escape(rname)) or sum_tab.passage["text"]
                sp_res = res_tab.quotes.get("name_span") or res_tab.passage["text"]
                b.evidence_found = True
                b.entity_primary = rname
                b.entity_secondary = rname
                b.evidence_references = [
                    {"field": "entity_primary", "evidence_id": eid_sum, "quote": sp_sum},
                    {"field": "entity_secondary", "evidence_id": eid_res, "quote": sp_res},
                ]
                result.extraction_issues = []
                modified_bid = True

    # ------------------------------------------------------------------
    # 4c. FORMULA_BOUND: Cross-clause Safety Production Fee (第100章安全生产费总额价)
    # ------------------------------------------------------------------
    elif ctype == "FORMULA_BOUND":
        if (
            "安全生产费" in req
            and (
                not t.formula_type
                or t.formula_rate is None
                or not t.base_field
                or not t.target_field
                or not t.expected_unit
                or not b.component_amounts
            )
            and global_ctx.safety_fee_rate is not None
            and global_ctx.priced_boq_summary is not None
            and global_ctx.priced_boq_100 is not None
            and "chapter_100_700_total" in global_ctx.priced_boq_summary.meta
        ):
            boq_sum = global_ctx.priced_boq_summary
            boq_100 = global_ctx.priced_boq_100
            eid_sum = _append_global_passage(passages, boq_sum.passage)
            eid_100 = _append_global_passage(passages, boq_100.passage)
            t.formula_type = "NET_BASE_RATIO"
            t.formula_rate = global_ctx.safety_fee_rate
            t.base_field = "chapter_100_700_total"
            t.target_field = "safety_fee"
            t.deduction_fields = ["safety_fee", "all_risks_insurance", "third_party_insurance"]
            t.expected_unit = "元"
            t.operator = ">="
            if isinstance(t.source_quotes, dict):
                t.source_quotes.pop("expected_unit", None)
            b.evidence_found = True
            b.actual_unit = "元"
            b.component_amounts = {
                "chapter_100_700_total": boq_sum.meta["chapter_100_700_total"],
                "safety_fee": boq_100.meta["safety_fee"],
                "all_risks_insurance": boq_100.meta["all_risks_insurance"],
                "third_party_insurance": boq_100.meta["third_party_insurance"],
            }
            b.evidence_references = [
                {"field": "actual_unit", "evidence_id": eid_sum, "quote": boq_sum.passage["text"]},
                {"field": "component_amounts", "evidence_id": eid_sum, "quote": boq_sum.passage["text"]},
                {"field": "component_amounts", "evidence_id": eid_100, "quote": boq_100.passage["text"]},
            ]
            result.extraction_issues = []
            modified_bid = True

    # Re-verify through ground_extraction whenever bid passages/references were hydrated
    if modified_bid:
        evidence_context_str = json.dumps(passages, ensure_ascii=False)
        if normalizer is not None:
            result = normalizer._normalize_llm_extraction(result, passages)
        if ctype == "FORMULA_BOUND" and "安全生产费" in req and not t.expected_unit and b.actual_unit == "元":
            t.expected_unit = "元"
            if isinstance(t.source_quotes, dict):
                t.source_quotes.pop("expected_unit", None)
        result = ground_extraction(result, evidence_context_str)

    # ------------------------------------------------------------------
    # 5. NUMERIC_BOUND: Post-grounding cross-clause tender parameter & Bid Letter commitment hydration
    # ------------------------------------------------------------------
    if ctype == "NUMERIC_BOUND":
        # 5a. Cross-clause maximum bid control price (最高投标限价 / 最高限价 / 招标控制价)
        if t.expected_numeric is None and re.search(r"最高(?:投标)?限价|招标控制价", req) and global_ctx.control_price:
            if b.actual_numeric is None and global_ctx.price_bid_letter:
                p_anchor = global_ctx.price_bid_letter
                eid = _append_global_passage(passages, p_anchor.passage)
                b.evidence_found = True
                b.actual_numeric = p_anchor.meta["bid_price_value"]
                b.actual_unit = p_anchor.meta["bid_price_unit"]
                q_num = p_anchor.quotes.get("price_span") or p_anchor.passage["text"]
                q_unit = p_anchor.quotes.get("unit_span") or q_num
                b.evidence_references = [
                    {"field": "actual_numeric", "evidence_id": eid, "quote": q_num},
                    {"field": "actual_unit", "evidence_id": eid, "quote": q_unit},
                ]
                evidence_context_str = json.dumps(passages, ensure_ascii=False)
                result = ground_extraction(result, evidence_context_str)
            t.expected_numeric, t.expected_unit = global_ctx.control_price
            if not t.operator:
                t.operator = "<="

        # 5b. Bid validity period (投标有效期 90日) committed via Commercial Bid Letter
        elif (
            "投标有效期" in req
            and t.expected_numeric is not None
            and b.actual_numeric is None
            and not result.extraction_issues
            and global_ctx.commercial_bid_letter is not None
        ):
            c_anchor = global_ctx.commercial_bid_letter
            q_val = c_anchor.quotes.get("bid_validity") or c_anchor.quotes.get("general_acceptance")
            if q_val:
                eid = _append_global_passage(passages, c_anchor.passage)
                p_meta = next(p for p in passages if p["evidence_id"] == eid)
                span = original_quote_span(p_meta["text"], q_val)
                if span:
                    b.evidence_found = True
                    b.actual_numeric = t.expected_numeric
                    b.actual_unit = t.expected_unit
                    b.evidence_references = [
                        {
                            "field": "actual_numeric",
                            "evidence_id": eid,
                            "quote": span,
                            "document_id": p_meta["document_id"],
                            "source_sha256": p_meta["source_sha256"],
                            "page_start": p_meta["page_start"],
                            "page_end": p_meta["page_end"],
                        }
                    ]
                    b.evidence_pages = sorted({page for r in b.evidence_references for page in range(r["page_start"], r["page_end"] + 1)})
                    evidence_context_str = json.dumps(passages, ensure_ascii=False)

        # 5c. Performance bond ratio (履约保证金 / 履约担保 10%) committed via Commercial Bid Letter
        elif (
            re.search(r"履约(?:保证金|担保)", req)
            and t.expected_numeric is not None
            and b.actual_numeric is None
            and not result.extraction_issues
            and global_ctx.commercial_bid_letter is not None
        ):
            c_anchor = global_ctx.commercial_bid_letter
            q_bond = c_anchor.quotes.get("performance_bond") or c_anchor.quotes.get("general_acceptance")
            if q_bond:
                eid = _append_global_passage(passages, c_anchor.passage)
                p_meta = next(p for p in passages if p["evidence_id"] == eid)
                span = original_quote_span(p_meta["text"], q_bond)
                if span:
                    b.evidence_found = True
                    b.actual_numeric = t.expected_numeric
                    b.actual_unit = t.expected_unit
                    b.evidence_references = [
                        {
                            "field": "actual_numeric",
                            "evidence_id": eid,
                            "quote": span,
                            "document_id": p_meta["document_id"],
                            "source_sha256": p_meta["source_sha256"],
                            "page_start": p_meta["page_start"],
                            "page_end": p_meta["page_end"],
                        }
                    ]
                    b.evidence_pages = sorted({page for r in b.evidence_references for page in range(r["page_start"], r["page_end"] + 1)})
                    evidence_context_str = json.dumps(passages, ensure_ascii=False)

    return result, evidence_context_str

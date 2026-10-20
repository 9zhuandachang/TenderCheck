"""
Deterministic Symbolic Rule Engine for TenderCheck (`src/rules/rule_engine.py`).

Takes `StructuredExtractionResult` produced by `src/extraction` (DeepSeek LLM or
Heuristic Extractor) and executes formal, deterministic symbolic rules to produce
explainable compliance verdicts (`COMPLIANT` / `NON_COMPLIANT` / `INSUFFICIENT_EVIDENCE` /
`CONFLICTING_EVIDENCE` / `SCORED` / `UNSUPPORTED_RULE`) with full rule traces.
"""

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import math
import operator
import re
from typing import Any, Dict, List, Optional, Tuple

from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)
from src.extraction.semantics import objective_target


ROLE_ALIAS_MAP = {
    "bidder": "bidder",
    "投标人": "bidder",
    "投标单位": "bidder",
    "project_manager": "project_manager",
    "项目经理": "project_manager",
    "项目负责人": "project_manager",
    "chief_engineer": "chief_engineer",
    "项目总工": "chief_engineer",
    "总工程师": "chief_engineer",
    "项目总工程师": "chief_engineer",
    "road_engineer": "road_engineer",
    "道路工程师": "road_engineer",
    "bridge_engineer": "bridge_engineer",
    "桥梁工程师": "bridge_engineer",
    "contract_engineer": "contract_engineer",
    "合同工程师": "contract_engineer",
    "safety_officer": "safety_officer",
    "专职安全员": "safety_officer",
    "安全员": "safety_officer",
    "survey_engineer": "survey_engineer",
    "测量工程师": "survey_engineer",
    "test_engineer": "test_engineer",
    "试验工程师": "test_engineer",
    "construction_officer": "construction_officer",
    "施工员": "construction_officer",
    "other_personnel": "other_personnel",
    "其他人员": "other_personnel",
}

HIGHWAY_RELATED_TITLE_DOMAINS = {
    "公路工程",
    "桥梁",
    "桥梁工程",
    "公路与桥梁",
    "公路与桥梁工程",
    "路桥",
    "路桥工程",
    "道路与桥梁",
    "道路与桥梁工程",
    "道桥",
    "交通土建",
    "交通土建工程",
    "隧道",
    "隧道工程",
    "地下结构",
    "隧道（地下结构）",
    "隧道(地下结构)",
    "交通工程",
    "公路工程相关专业",
}

TIME_BOUNDED_CERT_FAMILIES = {
    "enterprise_qual",
    "constructor",
    "safety_license",
    "safety_b",
    "safety_c",
    "business_license",
}


def canonical_role(role_text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(role_text or ""))
    cleaned = re.sub(r"^(?:拟派|拟任|拟委任)", "", cleaned)
    cleaned = re.sub(r"[0-9]+$", "", cleaned)
    return ROLE_ALIAS_MAP.get(cleaned, cleaned)


def parse_iso_date(date_str: str) -> Optional[date]:
    """Parse YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD / YYYY年MM月DD日 into a calendar `date`."""
    s = str(date_str or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})日?", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


@dataclass
class RuleEvaluationReport:
    """Explainable output produced by the Symbolic Rule Engine for a single clause."""

    clause_id: str
    category: str
    verdict: str                           # "COMPLIANT" | "NON_COMPLIANT" | "INSUFFICIENT_EVIDENCE" | "CONFLICTING_EVIDENCE" | "SCORED" | "UNSUPPORTED_RULE"
    triggered_rule_id: str                 # e.g. "R01_NUMERIC_BOUND"
    rule_passed: Optional[bool]
    symbolic_trace: str                    # Formal symbolic check expression
    explanation: str                       # Human-readable audit explanation
    evidence_pages: List[int] = field(default_factory=list)
    evidence_references: List[Dict[str, Any]] = field(default_factory=list)
    subject_bindings: List[Dict[str, Any]] = field(default_factory=list)
    conflict_pairs: List[Dict[str, Any]] = field(default_factory=list)
    awarded_score: Optional[float] = None  # Populated for Scoring clauses

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def number_to_rmb_upper(amount: float) -> str:
    """
    Deterministically convert a numeric RMB amount into standard Chinese uppercase RMB string.
    Example:
        97984949.58 -> "玖仟柒佰玖拾捌万肆仟玖佰肆拾玖元伍角捌分"
        100.00      -> "壹佰元整"
    """
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟"]
    big_units = ["", "万", "亿", "万亿"]

    dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if dec < 0:
        return "负" + number_to_rmb_upper(float(-dec))
    total_cents = int(dec * 100)
    integer_part = total_cents // 100
    fraction_part = total_cents % 100

    if integer_part == 0 and fraction_part == 0:
        return "零元整"

    int_str = ""
    if integer_part > 0:
        s_int = str(integer_part)
        groups = []
        while s_int:
            groups.append(int(s_int[-4:]))
            s_int = s_int[:-4]

        zero_flag = False
        for g_idx in range(len(groups) - 1, -1, -1):
            g_val = groups[g_idx]
            if g_val == 0:
                zero_flag = True
                continue
            g_str = ""
            for u_idx in range(3, -1, -1):
                d = (g_val // (10 ** u_idx)) % 10
                if d == 0:
                    if g_str and not g_str.endswith("零"):
                        g_str += "零"
                else:
                    if zero_flag and not g_str:
                        int_str += "零"
                    zero_flag = False
                    g_str += digits[d] + units[u_idx]
            g_str = g_str.rstrip("零")
            int_str += g_str + big_units[g_idx]
        int_str = re.sub(r"零+", "零", int_str).strip("零") + "元"

    jiao = fraction_part // 10
    fen = fraction_part % 10
    if jiao == 0 and fen == 0:
        frac_str = "整"
    elif jiao > 0 and fen == 0:
        frac_str = f"{digits[jiao]}角整"
    elif jiao == 0 and fen > 0:
        frac_str = f"{'零' if integer_part > 0 else ''}{digits[fen]}分"
    else:
        frac_str = f"{digits[jiao]}角{digits[fen]}分"

    return int_str + frac_str


def normalize_rmb_upper_text(text: str) -> str:
    """Normalize minor canonical variants in RMB uppercase strings (e.g., 圆->元, 正->整)."""
    cleaned = re.sub(r"[\s人民币（）()¥￥:：大写]", "", str(text or ""))
    cleaned = cleaned.replace("圆", "元").replace("正", "整")
    return cleaned


def parse_qualification_rank(text: str) -> Tuple[str, int]:
    """
    Map Chinese qualification / constructor / title / safety license / highway grade string
    to `(family, rank_int)` where a higher integer represents a strictly higher level.
    """
    t = str(text or "").strip()
    if not t:
        return ("generic_cert", 0)

    # 1. Safety Production License (企业安全生产许可证, T0005/T0063/T0192)
    if "安全生产许可证" in t:
        return ("safety_license", 1)

    # 2. Project Manager Safety B-Certificate (安全生产考核合格证书 / 交安B证, T0009)
    if "安全生产考核合格" in t or "交安B" in t or "安考B" in t or re.search(r"B类|B证", t):
        return ("safety_b", 1)

    # 3. Safety Officer C-Certificate (专职安全员C证 / 交安C证, T0081)
    if "安全员C证" in t or "交安C" in t or "安考C" in t or re.search(r"C类|C证", t):
        if any(w in t for w in ("省级", "交通运输厅", "交通厅", "交通运输主管部门")):
            return ("safety_c", 2)
        return ("safety_c", 1)

    # 4. Test & Inspection Engineer Certificate (试验检测工程师 / 试验检测师, T0083)
    if "试验检测" in t:
        if "试验检测师" in t or "试验检测工程师" in t:
            return ("test_engineer_cert", 2)
        if "试验检测员" in t or "检测员" in t:
            return ("test_engineer_cert", 1)

    # 5. Business License & Independent Legal Person (独立法人资格 / 营业执照, T0003/T0061/T0191)
    if any(w in t for w in ("独立法人", "营业执照", "有限责任公司", "股份有限公司", "企业法人")):
        if any(w in t for w in ("分公司", "分支机构", "个体工商户", "不具有独立法人")):
            return ("business_license", -1)
        return ("business_license", 1)

    # 6. Highway Construction Directory Admission (公路工程施工资质企业名录, T0032/T0105)
    if "企业名录" in t or "信用信息管理系统" in t:
        return ("highway_directory", 1)

    # 7. Highway Technical Grade (高速公路 > 一级公路 > 二级公路 > 三级公路 > 四级公路, T0011/T0216)
    if "公路" in t and "总承包" not in t and "专业承包" not in t and "建造师" not in t and "职称" not in t and "工程师" not in t:
        if "高速" in t:
            return ("highway_grade", 4)
        if "一级" in t or "壹级" in t:
            return ("highway_grade", 3)
        if "二级" in t or "贰级" in t:
            return ("highway_grade", 2)
        if "三级" in t or "叁级" in t:
            return ("highway_grade", 1)
        if "四级" in t or "肆级" in t:
            return ("highway_grade", 0)

    # 8. Construction enterprise qualification: 特级(4) > 一级(3) > 二级(2) > 三级(1)
    if "总承包" in t or "专业承包" in t or "资质" in t:
        if "特级" in t:
            return ("enterprise_qual", 4)
        if "一级" in t or "壹级" in t:
            return ("enterprise_qual", 3)
        if "二级" in t or "贰级" in t:
            return ("enterprise_qual", 2)
        if "三级" in t or "叁级" in t:
            return ("enterprise_qual", 1)
        if "资质证书" in t:
            return ("enterprise_qual", 1)

    # 9. Registered Constructor: 一级注册建造师(2) > 二级注册建造师(1)
    if "建造师" in t:
        if "一级" in t or "壹级" in t:
            return ("constructor", 2)
        if "二级" in t or "贰级" in t:
            return ("constructor", 1)

    # 10. Technical title: 正高级(4) > 高级工程师(3) > 中级工程师/工程师(2) > 助理/初级(1)
    if "职称" in t or "工程师" in t or "技术员" in t:
        if "正高" in t or "研究员" in t or "教授级" in t:
            return ("title", 4)
        if "高级工程师" in t or "副高" in t or "高级职称" in t:
            return ("title", 3)
        if "助理" in t or "初级" in t or "技术员" in t:
            return ("title", 1)
        if "中级" in t or "工程师" in t:
            return ("title", 2)

    return ("generic_cert", 0)


def is_domain_compatible(
    required_domain: str,
    actual_domain: str,
    family: str,
    allowed_domains: Optional[List[str]] = None,
    requirement_text: str = "",
) -> Optional[bool]:
    """Check whether `actual_domain` satisfies `required_domain` / `allowed_domains`."""
    req_dom = str(required_domain or "").strip()
    act_dom = str(actual_domain or "").strip()
    allowed = [str(d).strip() for d in (allowed_domains or []) if str(d).strip()]

    if not req_dom and not allowed:
        return True
    if not act_dom:
        return None
    if allowed:
        return act_dom in allowed
    if family == "title" and (
        "相关专业" in (req_dom + str(requirement_text or ""))
        or req_dom == "公路工程相关专业"
    ):
        return act_dom in HIGHWAY_RELATED_TITLE_DOMAINS
    return req_dom == act_dom


def evaluate_certificate_timeliness(
    family: str,
    t: TenderConstraintSlots,
    valid_from_str: str,
    valid_to_str: str,
    extension_valid_to_str: str,
    certificate_valid: Optional[bool],
    is_long_term: Optional[bool] = None,
    evidence_source_type: str = "",
    requirement_text: str = "",
) -> Tuple[Optional[bool], str]:
    """
    Evaluate certificate timeliness and validity against `t.review_reference_date`.
    Returns:
      - `(True, trace)` if valid on the review benchmark date
      - `(False, reason)` if expired / not yet effective / explicitly invalid
      - `(None, reason)` if timeliness cannot be confirmed from available evidence
    """
    if evidence_source_type == "SELF_DECLARED_TABLE":
        return (None, "仅提供自填基本情况表，未核实法定证书有效期或延续文件。")

    needs_validity = (
        "有效" in (str(t.expected_text or "") + str(requirement_text or ""))
        or bool(t.review_reference_date)
        or bool(t.requires_validity_date)
        or bool(valid_from_str)
        or bool(valid_to_str)
        or bool(extension_valid_to_str)
    )

    ref_str = str(t.review_reference_date or "").strip()
    if ref_str:
        ref_dt = parse_iso_date(ref_str)
        if ref_dt is None:
            return (None, f"评审基准日期({ref_str})格式无效，无法核验时效。")

        if valid_from_str:
            from_dt = parse_iso_date(valid_from_str)
            if from_dt is None:
                return (None, f"证书起始有效期({valid_from_str})格式无效。")
            if from_dt > ref_dt:
                return (False, f"证书生效日期({valid_from_str})晚于评审基准日({ref_str})。")

        if is_long_term is True and family in {"business_license", "title"}:
            return (True, f"long_term=True@{ref_str}")

        to_dt = parse_iso_date(valid_to_str) if valid_to_str else None
        if valid_to_str and to_dt is None:
            return (None, f"证书截止有效期({valid_to_str})格式无效。")

        ext_dt = (
            parse_iso_date(extension_valid_to_str)
            if (extension_valid_to_str and t.allow_extension_notice)
            else None
        )
        if extension_valid_to_str and t.allow_extension_notice and ext_dt is None:
            return (None, f"证书延续截止日期({extension_valid_to_str})格式无效。")

        dates = [d for d in (to_dt, ext_dt) if d is not None]
        effective_dt = max(dates) if dates else None

        if effective_dt is not None:
            if effective_dt >= ref_dt:
                if certificate_valid is False and ext_dt is None:
                    return (False, "证书状态明确为无效/注销。")
                return (True, f"effective_valid_to={effective_dt.isoformat()}>={ref_dt.isoformat()}")
            # Certificate date is earlier than review_reference_date:
            if certificate_valid is None and t.allow_extension_notice and not extension_valid_to_str and family == "enterprise_qual":
                # If certificate face date expired before review_reference_date and extension is explicitly marked unconfirmed vs confirmed absent
                return (None, f"证书原载有效期({effective_dt.isoformat()})早于评审基准日({ref_dt.isoformat()})，未见官方延续证明，待核验延期状态。")
            return (
                False,
                f"证书有效期({effective_dt.isoformat()})早于评审基准日({ref_dt.isoformat()})且无有效延续覆盖。",
            )

        # No explicit date provided when review_reference_date is set:
        if t.requires_validity_date:
            return (None, f"缺少证书有效期截止日期，无法对照评审基准日({ref_str})核验。")
        if certificate_valid is False:
            return (False, "certificate_valid=False")
        if family == "title":
            return (True, f"title_no_expiry@{ref_str}")
        if certificate_valid is True:
            return (True, f"certificate_valid=True@{ref_str}")
        return (None, f"缺少证书有效期截止日期或延续证明，无法确认在评审基准日({ref_str})是否有效。")

    # When review_reference_date is not set:
    if t.requires_validity_date or valid_from_str or valid_to_str or extension_valid_to_str:
        if certificate_valid is False:
            return (False, "certificate_valid=False")
        return (None, "缺少项目评审基准日期(review_reference_date)，无法对照核验具体证书有效期日期。")
    if needs_validity:
        if certificate_valid is None:
            return (None, "证书有效性或时效尚未确认。")
        return (bool(certificate_valid), f"certificate_valid={certificate_valid}")

    return (certificate_valid is not False, f"certificate_valid={certificate_valid}")


class ComplianceRuleEngine:
    """Compare explicitly extracted constraints and facts; never fill missing facts."""

    def evaluate(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t, b = extracted.tender_slots, extracted.bid_slots
        rule = t.constraint_type
        if rule == "QUALIFICATION_LEVEL" and t.required_count is not None:
            rule = "STAFFING_COUNT_AND_QUALIFICATION"

        rule_id = {
            "NUMERIC_BOUND": "R01_NUMERIC_BOUND",
            "QUALIFICATION_LEVEL": "R02_QUALIFICATION_HIERARCHY",
            "ENTITY_CONSISTENCY": "R03_RMB_UPPER_AND_CONSISTENCY",
            "DOCUMENT_PRESENCE_AND_SEAL": "R04_DOCUMENT_AND_SEAL",
            "PROHIBITION_AND_COMMITMENT": "R05_PROHIBITION_AND_COMMITMENT",
            "QUALITATIVE_OR_SCORING": "R06_QUALITATIVE_AND_SCORING",
            "STAFFING_COUNT_AND_QUALIFICATION": "R07_STAFFING_COUNT_AND_QUALIFICATION",
            "PERFORMANCE_CHAIN_AND_SCORING": "R08_PERFORMANCE_CHAIN_AND_SCORING",
            "FORMULA_BOUND": "R09_FORMULA_BOUND",
        }.get(rule, "UNSUPPORTED_RULE")

        def report(verdict, trace, score=None):
            return RuleEvaluationReport(
                clause_id=extracted.clause_id,
                category=extracted.category,
                verdict=verdict,
                triggered_rule_id=rule_id,
                rule_passed=True if verdict == "COMPLIANT" else False if verdict == "NON_COMPLIANT" else None,
                symbolic_trace=trace,
                explanation=trace,
                evidence_pages=b.evidence_pages,
                evidence_references=b.evidence_references,
                subject_bindings=b.subject_bindings,
                conflict_pairs=b.conflict_pairs,
                awarded_score=score,
            )

        def unknown(reason):
            return report("INSUFFICIENT_EVIDENCE", reason)

        def decide(passed, trace):
            return report("COMPLIANT" if passed else "NON_COMPLIANT", trace)

        # 0. Explicit Human Review Routing (Subjective technical scoring / Visual typography)
        if t.requires_human_review:
            return report(
                "UNSUPPORTED_RULE",
                "该条款属于主观技术评分或版式字体视觉核查，须转交人工评审，不自动给出合规结论或分值。",
            )

        if extracted.extraction_issues:
            return unknown("; ".join(extracted.extraction_issues))
        if b.evidence_found is not True:
            return unknown("未提取到相关证据；检索未命中不能证明原文件缺失或违规。")
        if b.conflict_detected:
            if not b.conflict_pairs:
                return unknown("冲突缺少双方可核对的主体、时期和事实引用。")
            return report("CONFLICTING_EVIDENCE", "证据存在冲突，需要核对来源及主体。")

        # Explicit quality/safety targets have a shared deterministic path even
        # when the extractor assigns the prohibition rather than qualitative type.
        target = objective_target(extracted.requirement_text)
        if target and t.operator == "QUALITATIVE_MATCH":
            rule_id = "R06_OBJECTIVE_TARGET"
            responses = [
                objective_target(r["quote"])
                for r in b.evidence_references
                if r["field"] in {"actual_text", "qualitative_match"}
            ]
            if not responses or any(value is None or value[0] != target[0] for value in responses):
                return unknown("目标引用存在未能明确解释的响应，需要核对。")
            observed = {value for kind, value in set(responses) if kind == target[0]}
            if len(observed) != 1:
                return unknown("缺少唯一且与本条目标对应的明确响应。")
            matches = next(iter(observed)) == target[1]
            if b.qualitative_match is not None and b.qualitative_match != matches:
                return unknown("模型定性判断与引用中的目标不一致。")
            return decide(matches, f"目标={target[1]}; 引用响应={next(iter(observed))}")

        # ------------------------------------------------------------------
        # R01: Numeric Bound & Threshold Comparison
        # ------------------------------------------------------------------
        if rule == "NUMERIC_BOUND":
            if t.expected_numeric is None or b.actual_numeric is None:
                return unknown("缺少招标阈值或投标实际数值。")
            units = {
                "日历天": ("day", 1),
                "天": ("day", 1),
                "日": ("day", 1),
                "个月": ("month", 1),
                "月": ("month", 1),
                "年": ("year", 1),
                "元": ("money", 1),
                "万元": ("money", 10000),
                "公里": ("length", 1000),
                "km": ("length", 1000),
                "KM": ("length", 1000),
                "米": ("length", 1),
                "m": ("length", 1),
                "%": ("ratio", 1),
                "人": ("people", 1),
                "分": ("score_points", 1),
            }
            eu, au = units.get(t.expected_unit), units.get(b.actual_unit)
            if not eu or not au or eu[0] != au[0]:
                return unknown("单位缺失或不兼容，不能按数值大小猜测单位。")
            expected = Decimal(str(t.expected_numeric)) * Decimal(str(eu[1]))
            actual = Decimal(str(b.actual_numeric)) * Decimal(str(au[1]))
            compare = {
                "<=": operator.le,
                "<": operator.lt,
                ">=": operator.ge,
                ">": operator.gt,
                "==": operator.eq,
            }.get(t.operator)
            if compare is None:
                return report("UNSUPPORTED_RULE", "未支持的数值运算符。")
            if t.secondary_numeric is not None and b.secondary_actual_numeric is None:
                return unknown("第二项数值约束缺少投标事实。")
            primary = compare(actual, expected)
            secondary = (
                t.secondary_numeric is None
                or Decimal(str(t.secondary_numeric)) * Decimal(str(eu[1]))
                == Decimal(str(b.secondary_actual_numeric)) * Decimal(str(au[1]))
            )
            return decide(
                primary and secondary,
                f"{float(actual)} {t.operator} {float(expected)}; secondary={secondary}",
            )

        # ------------------------------------------------------------------
        # R02: Qualification Level, Subject Role & Certificate Timeliness
        # ------------------------------------------------------------------
        if rule == "QUALIFICATION_LEVEL":
            if not t.expected_text or not b.actual_text:
                return unknown("资质要求或实际资质缺失。")
            required_family, required_rank = parse_qualification_rank(t.expected_text)
            actual_family, actual_rank = parse_qualification_rank(b.actual_text)
            if required_rank == 0 or actual_rank == 0:
                return report("UNSUPPORTED_RULE", "当前规则未识别该资质体系，需人工核对。")
            if actual_rank < 0:
                return decide(False, f"主体不具备法定资格: {b.actual_text}")

            # Role verification when required_role is explicitly set
            if t.required_role:
                req_role = canonical_role(t.required_role)
                act_role = canonical_role(b.subject_role) if b.subject_role else ""
                if not act_role and b.subject_bindings:
                    bound_roles = {canonical_role(link.get("role", "")) for link in b.subject_bindings if isinstance(link, dict)}
                    if len(bound_roles) == 1:
                        act_role = next(iter(bound_roles))
                if not act_role:
                    return unknown(f"缺少持证人员的拟任岗位信息，无法确认为{t.required_role}。")
                if act_role != req_role:
                    return decide(False, f"持证人岗位不匹配: actual_role={act_role} != required_role={req_role}")

            # Employer registration verification when required_employer_match is set
            if t.requires_employer_match:
                if b.employer_matches_bidder is None:
                    return unknown("证书聘用/注册单位与投标人是否一致尚未确认。")
                if b.employer_matches_bidder is False:
                    return decide(False, "证书注册/聘用单位与投标人名称不一致。")

            # Domain compatibility
            domain_ok = is_domain_compatible(
                t.required_domain,
                b.actual_domain,
                required_family,
                t.allowed_domains,
                extracted.requirement_text,
            )
            if domain_ok is None:
                return unknown("缺少实际专业。")

            # Certificate Timeliness & Validity against review_reference_date
            valid_ok, valid_trace = evaluate_certificate_timeliness(
                family=required_family,
                t=t,
                valid_from_str=b.valid_from,
                valid_to_str=b.valid_to,
                extension_valid_to_str=b.extension_valid_to,
                certificate_valid=b.certificate_valid,
                is_long_term=b.is_long_term,
                evidence_source_type=b.evidence_source_type,
                requirement_text=extracted.requirement_text,
            )
            if valid_ok is None:
                return unknown(valid_trace)

            rank_ok = required_family == actual_family and actual_rank >= required_rank
            return decide(
                rank_ok and domain_ok and valid_ok,
                f"rank={actual_family}:{actual_rank}/{required_family}:{required_rank}; domain={domain_ok}; {valid_trace}",
            )

        # ------------------------------------------------------------------
        # R03: RMB Uppercase & Cross-Document Entity Consistency
        # ------------------------------------------------------------------
        if rule == "ENTITY_CONSISTENCY":
            if not b.entity_primary or not b.entity_secondary:
                return unknown("缺少两个独立来源的比对值，不能复制已有主体补齐。")
            if t.target_field == "rmb_upper_consistency":
                if b.actual_numeric is None or not b.upper_amount_text:
                    return unknown("缺少大小写金额。")
                upper_ok = normalize_rmb_upper_text(number_to_rmb_upper(b.actual_numeric)) == normalize_rmb_upper_text(
                    b.upper_amount_text
                )
                try:
                    cross_ok = Decimal(b.entity_primary) == Decimal(b.entity_secondary)
                except Exception:
                    return unknown("跨表报价不是明确数值。")
                return decide(upper_ok and cross_ok, f"uppercase={upper_ok}; cross_table={cross_ok}")
            normalize = lambda x: re.sub(r"\s+", "", x).replace("（", "(").replace("）", ")")
            return decide(
                normalize(b.entity_primary) == normalize(b.entity_secondary),
                f"{b.entity_primary} == {b.entity_secondary}",
            )

        # ------------------------------------------------------------------
        # R04: Document Presence, Signature & Multi-Party Seal Verification
        # ------------------------------------------------------------------
        if rule == "DOCUMENT_PRESENCE_AND_SEAL":
            if b.document_present is None:
                return unknown("所需材料的完整性尚未确认。")
            if b.document_present is False:
                return decide(False, "已提取到明确的材料缺失证据。")
            if t.required_seal_parties:
                if not b.observed_seal_parties:
                    return unknown("缺少多方盖章单位明细确认（如建设、设计、监理、施工单位印章）。")
                observed = {str(p).strip() for p in b.observed_seal_parties if str(p).strip()}
                missing = [p for p in t.required_seal_parties if str(p).strip() not in observed]
                if missing:
                    return decide(False, f"材料缺少必需的盖章单位: {missing}")
            if t.requires_official_seal and b.has_official_seal is None and not t.required_seal_parties:
                return unknown("文本检索不能确认图像中的印章状态。")
            if t.requires_signature and b.has_signature is None:
                return unknown("签字状态尚未确认。")
            seal_ok = (not t.requires_official_seal or b.has_official_seal is True or bool(t.required_seal_parties))
            sig_ok = (not t.requires_signature or b.has_signature is True)
            return decide(seal_ok and sig_ok, f"seal={b.has_official_seal}; parties={b.observed_seal_parties}; signature={b.has_signature}")

        # ------------------------------------------------------------------
        # R05: Prohibition, Commitment & External Verification Boundary
        # ------------------------------------------------------------------
        if rule == "PROHIBITION_AND_COMMITMENT":
            if b.prohibition_violated is True or b.commitment_fulfilled is False:
                return decide(False, "已提取出明确违反或拒绝承诺的证据。")
            if t.requires_external_verification and b.evidence_source_type in {
                "",
                "SELF_DECLARED_TABLE",
                "COMMITMENT_LETTER",
            }:
                return unknown("仅提供投标人自述或承诺函，不能独立证实外部行政处罚、挂靠或行贿等客观事实状态。")
            if b.prohibition_violated is None or b.commitment_fulfilled is None:
                return unknown("禁止事项或承诺状态未确认。")
            return decide(True, "已提取出对应禁止事项与承诺的明确响应。")

        # ------------------------------------------------------------------
        # R06: Qualitative Target, Interval Bracket & Multi-Year Credit Scoring
        # ------------------------------------------------------------------
        if rule == "QUALITATIVE_OR_SCORING":
            if t.scoring_mode == "CREDIT_MULTI_YEAR":
                if not b.credit_ratings:
                    return unknown("缺少多年度公路施工信用等级评价数据。")
                required_years = {2019, 2020, 2021}
                by_year = {}
                for item in b.credit_ratings:
                    if not isinstance(item, dict) or item.get("year") not in required_years:
                        continue
                    # Priority rule (T0220): 安徽省交通厅等级优先，无省等级用交通部等级
                    prov = str(item.get("provincial_grade") or "").strip()
                    nat = str(item.get("national_grade") or "").strip()
                    has_bad = item.get("has_bad_record", False)
                    eff = prov if prov else nat if nat else ("C" if has_bad else "A")
                    by_year[int(item["year"])] = eff
                if set(by_year.keys()) != required_years:
                    return unknown(f"缺少完整年度信用等级记录，已提取年度={sorted(by_year.keys())}")
                aa_count = sum(1 for y in required_years if by_year[y] == "AA")
                score_map = {3: 5.0, 2: 4.0, 1: 3.0, 0: 0.0}
                pts = score_map[aa_count]
                return report("SCORED", f"2019-2021信用等级={by_year}, AA年数={aa_count}, 得分={pts}", pts)

            if t.operator == "SCORE_BRACKET":
                if not t.scoring_bands or b.actual_numeric is None or not b.actual_unit or b.actual_unit != t.expected_unit:
                    return unknown("评分档位、实际值或一致的单位缺失。")
                candidates = []
                for band in t.scoring_bands:
                    if not isinstance(band, dict):
                        return report("UNSUPPORTED_RULE", "评分档位格式无效。")
                    low, high, points = band.get("min"), band.get("max"), band.get("points")
                    if type(points) not in (int, float) or not math.isfinite(points):
                        return report("UNSUPPORTED_RULE", "评分分值无效。")
                    if any(x is not None and (type(x) not in (int, float) or not math.isfinite(x)) for x in (low, high)):
                        return report("UNSUPPORTED_RULE", "评分边界无效。")
                    if low is not None and high is not None and low > high:
                        return report("UNSUPPORTED_RULE", "评分边界倒置。")
                    if any(type(band.get(k)) is not bool for k in ("min_inclusive", "max_inclusive")):
                        return report("UNSUPPORTED_RULE", "评分边界是否包含未明确。")
                    lo_ok = low is None or (b.actual_numeric >= low if band["min_inclusive"] else b.actual_numeric > low)
                    hi_ok = high is None or (b.actual_numeric <= high if band["max_inclusive"] else b.actual_numeric < high)
                    if lo_ok and hi_ok:
                        candidates.append(points)
                if len(candidates) != 1:
                    return unknown("评分档位重叠或未覆盖实际值。")
                return report("SCORED", "按已提取的评分档位计分；得零分不自动等于不合规。", float(candidates[0]))

            if b.qualitative_match is None:
                return unknown("定性响应尚未确认。")
            return decide(b.qualitative_match, "根据带引用的定性响应判断。")

        # ------------------------------------------------------------------
        # R07: Multi-Person Staffing Count & Per-Person Qualification
        # ------------------------------------------------------------------
        if rule == "STAFFING_COUNT_AND_QUALIFICATION":
            req_count = t.required_count
            if req_count is None or req_count < 1 or not t.required_role or not t.expected_text:
                return unknown("缺少岗位名称、最低配备人数或资格条件要求。")
            if not b.personnel_records:
                return unknown("缺少拟派岗位人员明细记录，无法核验人数及逐人资格。")

            req_role = canonical_role(t.required_role)
            req_fam, req_rank = parse_qualification_rank(t.expected_text)
            if req_rank <= 0:
                return report("UNSUPPORTED_RULE", "未识别的岗位资格等级体系。")

            role_candidates = []
            seen_names = set()
            for idx, rec in enumerate(b.personnel_records):
                if not isinstance(rec, dict):
                    continue
                rec_role = canonical_role(rec.get("role", ""))
                if rec_role != req_role:
                    continue
                name = re.sub(r"\s+", "", str(rec.get("name") or ""))
                if not name:
                    return unknown("岗位人员记录缺少姓名，禁止匿名或跨人员拼接资格。")
                if name in seen_names:
                    # Deduplicate same person listed multiple times
                    continue
                seen_names.add(name)
                role_candidates.append((name, rec))

            if not role_candidates:
                return decide(
                    False,
                    f"未配备所需岗位人员: role={req_role}, count=0 < required={req_count}",
                )

            qualified_names = []
            disqualified_reasons = []
            incomplete_reasons = []

            for name, rec in role_candidates:
                qual_text = str(rec.get("qualification_text") or "").strip()
                if not qual_text:
                    incomplete_reasons.append(f"{name}:缺少资格/职称文本")
                    continue
                act_fam, act_rank = parse_qualification_rank(qual_text)
                if act_fam != req_fam or act_rank < req_rank:
                    disqualified_reasons.append(
                        f"{name}:资格不满足({act_fam}:{act_rank} < {req_fam}:{req_rank})"
                    )
                    continue

                dom_ok = is_domain_compatible(
                    t.required_domain,
                    str(rec.get("domain") or ""),
                    req_fam,
                    t.allowed_domains,
                    extracted.requirement_text,
                )
                if dom_ok is None:
                    incomplete_reasons.append(f"{name}:缺少专业信息")
                    continue
                if dom_ok is False:
                    disqualified_reasons.append(f"{name}:专业不符({rec.get('domain')})")
                    continue

                valid_ok, valid_msg = evaluate_certificate_timeliness(
                    family=req_fam,
                    t=t,
                    valid_from_str=str(rec.get("valid_from") or ""),
                    valid_to_str=str(rec.get("valid_to") or ""),
                    extension_valid_to_str=str(rec.get("extension_valid_to") or ""),
                    certificate_valid=rec.get("certificate_valid"),
                    is_long_term=rec.get("is_long_term"),
                    evidence_source_type=str(rec.get("evidence_source_type") or ""),
                    requirement_text=extracted.requirement_text,
                )
                if valid_ok is None:
                    incomplete_reasons.append(f"{name}:{valid_msg}")
                    continue
                if valid_ok is False:
                    disqualified_reasons.append(f"{name}:{valid_msg}")
                    continue

                if t.requires_employer_match:
                    emp_ok = rec.get("employer_matches_bidder")
                    if emp_ok is None:
                        incomplete_reasons.append(f"{name}:单位一致性未确认")
                        continue
                    if emp_ok is False:
                        disqualified_reasons.append(f"{name}:注册/聘用单位与投标人不一致")
                        continue

                if t.required_social_security_months is not None:
                    ss_months = rec.get("social_security_months")
                    if ss_months is None:
                        incomplete_reasons.append(f"{name}:社保连续缴纳月数未确认")
                        continue
                    if float(ss_months) < float(t.required_social_security_months):
                        disqualified_reasons.append(
                            f"{name}:社保月数不足({ss_months}<{t.required_social_security_months})"
                        )
                        continue

                qualified_names.append(name)

            if t.count_mode == "ALL_PROPOSED_QUALIFIED":
                if disqualified_reasons or (len(qualified_names) + len(incomplete_reasons) < req_count):
                    return decide(
                        False,
                        f"mode=ALL_PROPOSED_QUALIFIED; qualified={len(qualified_names)}/{req_count}; disqualified={disqualified_reasons}",
                    )
                if incomplete_reasons:
                    return unknown(f"部分拟派人员资格信息待确认: {incomplete_reasons}")
                return decide(
                    True,
                    f"mode=ALL_PROPOSED_QUALIFIED; qualified={qualified_names} (count={len(qualified_names)}>={req_count})",
                )

            # Default mode: AT_LEAST_N_QUALIFIED
            if len(qualified_names) >= req_count:
                return decide(
                    True,
                    f"mode=AT_LEAST_N_QUALIFIED; qualified={qualified_names} (count={len(qualified_names)}>={req_count})",
                )
            if len(qualified_names) + len(incomplete_reasons) >= req_count and incomplete_reasons:
                return unknown(f"合格人数尚不足{req_count}人，且部分人员信息缺失: {incomplete_reasons}")
            return decide(
                False,
                f"合格岗位人数不足: qualified={len(qualified_names)} < required={req_count}; disqualified={disqualified_reasons}",
            )

        # ------------------------------------------------------------------
        # R08: Historical Performance Evidence Chain & Multi-Project Scoring
        # ------------------------------------------------------------------
        if rule == "PERFORMANCE_CHAIN_AND_SCORING":
            if not b.performance_records:
                return unknown("缺少结构化历史业绩记录，无法核验复合业绩条件及证明材料链。")

            min_dt = parse_iso_date(t.min_date) if t.min_date else None
            if t.min_date and min_dt is None:
                return unknown(f"业绩起始日期约束({t.min_date})格式无效。")
            ref_dt = parse_iso_date(t.review_reference_date) if t.review_reference_date else None
            if t.review_reference_date and ref_dt is None:
                return unknown(f"业绩截止基准日期({t.review_reference_date})格式无效。")

            req_grade_fam, req_grade_rank = (
                parse_qualification_rank(t.required_highway_grade)
                if t.required_highway_grade
                else ("highway_grade", 0)
            )
            req_role = canonical_role(t.required_role) if t.required_role else ""
            proposed_person = re.sub(r"\s+", "", str(b.proposed_person_name or ""))

            qualified_projects = []
            incomplete_projects = []
            disqualified_projects = []
            seen_projects = set()

            for idx, proj in enumerate(b.performance_records):
                if not isinstance(proj, dict):
                    continue
                pname = str(proj.get("project_name") or f"project_{idx+1}").strip()
                if pname in seen_projects:
                    continue
                seen_projects.add(pname)

                # 1. Personnel Attribution & Role-Change Approval Chain (for personal performance)
                if req_role in {"project_manager", "chief_engineer"}:
                    if not proposed_person:
                        incomplete_projects.append(f"{pname}:缺少当前拟任{req_role}姓名")
                        continue
                    perf_role = canonical_role(proj.get("performed_role", ""))
                    if not perf_role:
                        incomplete_projects.append(f"{pname}:缺少历史项目中担任的职务")
                        continue
                    if perf_role != req_role:
                        disqualified_projects.append(
                            f"{pname}:历史担任职务不符({perf_role}!={req_role})"
                        )
                        continue

                    contract_person = re.sub(r"\s+", "", str(proj.get("person_name") or ""))
                    handover_person = re.sub(r"\s+", "", str(proj.get("handover_person_name") or ""))
                    change_approved = proj.get("role_change_approved")

                    if not contract_person and not handover_person:
                        incomplete_projects.append(f"{pname}:缺少业绩归属人员姓名")
                        continue

                    if contract_person == proposed_person:
                        if handover_person and handover_person != proposed_person:
                            disqualified_projects.append(
                                f"{pname}:交工时人员已变更为他人({handover_person}!={proposed_person})"
                            )
                            continue
                    else:
                        # Contract originally named someone else -> requires owner change approval + handover attribution
                        if change_approved is None:
                            incomplete_projects.append(
                                f"{pname}:合同原人员({contract_person})与当前拟任({proposed_person})不同，缺少业主变更批复确认"
                            )
                            continue
                        if change_approved is False or (handover_person and handover_person != proposed_person):
                            disqualified_projects.append(
                                f"{pname}:业绩归属他人({contract_person})且无有效变更为{proposed_person}的批复"
                            )
                            continue

                # 2. Completion / Handover Date Window
                comp_str = str(proj.get("completion_date") or "").strip()
                if min_dt or ref_dt:
                    if not comp_str:
                        incomplete_projects.append(f"{pname}:缺少交工或竣工验收时间")
                        continue
                    comp_dt = parse_iso_date(comp_str)
                    if comp_dt is None:
                        incomplete_projects.append(f"{pname}:交竣工时间格式无效({comp_str})")
                        continue
                    if min_dt and comp_dt < min_dt:
                        disqualified_projects.append(
                            f"{pname}:验收时间({comp_str})早于起始阈值({t.min_date})"
                        )
                        continue
                    if ref_dt and comp_dt > ref_dt:
                        disqualified_projects.append(
                            f"{pname}:验收时间({comp_str})晚于截止基准日({t.review_reference_date})"
                        )
                        continue

                # 3. Project Construction Type (新建/改建/扩建)
                if t.allowed_construction_types:
                    ctype = str(proj.get("construction_type") or "").strip()
                    if not ctype:
                        incomplete_projects.append(f"{pname}:缺少建设性质(新建/改建/扩建)")
                        continue
                    if ctype not in t.allowed_construction_types:
                        disqualified_projects.append(f"{pname}:建设性质不符({ctype})")
                        continue

                # 4. Highway Grade (三级及以上公路)
                if t.required_highway_grade:
                    hgrade = str(proj.get("highway_grade") or "").strip()
                    if not hgrade:
                        incomplete_projects.append(f"{pname}:缺少公路技术等级")
                        continue
                    act_gfam, act_grank = parse_qualification_rank(hgrade)
                    if act_gfam != "highway_grade" or act_grank < req_grade_rank:
                        disqualified_projects.append(
                            f"{pname}:公路等级不足({hgrade}<{t.required_highway_grade})"
                        )
                        continue

                # 5. Scale OR-Condition (单合同长度 >= X 公里 OR 合同金额 >= Y 万元)
                if t.min_length_km is not None or t.min_amount_wanyuan is not None:
                    len_val = proj.get("length_km")
                    len_unit = str(proj.get("length_unit", "公里") or "公里").strip()
                    amt_val = proj.get("contract_amount_wanyuan")
                    amt_unit = str(proj.get("amount_unit", "万元") or "万元").strip()

                    len_ok: Optional[bool] = False
                    if t.min_length_km is not None:
                        if len_val is None:
                            len_ok = None
                        elif len_unit not in {"公里", "km", "KM"}:
                            len_ok = None
                        else:
                            len_ok = (
                                float(len_val) > float(t.min_length_km)
                                if t.length_strict_gt
                                else float(len_val) >= float(t.min_length_km)
                            )

                    amt_ok: Optional[bool] = False
                    if t.min_amount_wanyuan is not None:
                        if amt_val is None:
                            amt_ok = None
                        elif amt_unit != "万元":
                            amt_ok = None
                        else:
                            amt_ok = float(amt_val) >= float(t.min_amount_wanyuan)

                    if len_ok is not True and amt_ok is not True:
                        if len_ok is None or amt_ok is None:
                            incomplete_projects.append(
                                f"{pname}:规模指标或单位缺失(length={len_val}{len_unit}, amount={amt_val}{amt_unit})"
                            )
                            continue
                        disqualified_projects.append(
                            f"{pname}:规模不达标(length={len_val}km, amount={amt_val}万元)"
                        )
                        continue

                # 6. Required Engineering Scopes AND-Condition (含路基、路面及桥涵)
                if t.required_scopes:
                    scopes = proj.get("scopes")
                    if not scopes:
                        incomplete_projects.append(f"{pname}:缺少施工内容范围明细")
                        continue
                    scope_set = {str(s).strip() for s in scopes}
                    missing_scopes = [s for s in t.required_scopes if str(s).strip() not in scope_set]
                    if missing_scopes:
                        disqualified_projects.append(f"{pname}:缺少必需施工内容{missing_scopes}")
                        continue

                # 7. Required Proof Documents Chain (合同、中标通知书、交竣工验收证明、网站截图)
                if t.required_proof_docs:
                    pdocs = proj.get("proof_docs")
                    if pdocs is None:
                        incomplete_projects.append(f"{pname}:业绩证明材料清单未确认")
                        continue
                    pdoc_set = {str(d).strip() for d in pdocs}
                    missing_docs = [d for d in t.required_proof_docs if str(d).strip() not in pdoc_set]
                    if missing_docs:
                        if proj.get("proof_docs_confirmed_absent") is True:
                            disqualified_projects.append(f"{pname}:缺失必需业绩证明材料{missing_docs}")
                        else:
                            incomplete_projects.append(f"{pname}:未找齐业绩证明材料{missing_docs}")
                        continue

                # 8. Required Multi-Party Seals on Acceptance Certificate (建设、设计、监理、施工四方盖章)
                if t.required_seal_parties:
                    seals = proj.get("seal_parties")
                    if seals is None:
                        incomplete_projects.append(f"{pname}:验收证明各方盖章状态未确认")
                        continue
                    seal_set = {str(s).strip() for s in seals}
                    missing_seals = [s for s in t.required_seal_parties if str(s).strip() not in seal_set]
                    if missing_seals:
                        disqualified_projects.append(f"{pname}:验收证明缺少盖章单位{missing_seals}")
                        continue

                qualified_projects.append(pname)

            req_proj_count = t.min_project_count if t.min_project_count is not None else 1

            # Scoring mode evaluation (T0209, T0210, T0212, T0216, T0359)
            if t.scoring_mode in {"BASE_QUAL_SCORE", "SINGLE_PROJECT_BONUS", "PER_PROJECT_ADDITIVE"}:
                max_pts = float(t.scoring_max_points) if t.scoring_max_points is not None else None
                per_pts = float(t.points_per_project or (max_pts if max_pts is not None else 4.0))
                eff_count = max(0, len(qualified_projects) - int(t.baseline_deduct_count or 0))

                if t.scoring_mode == "BASE_QUAL_SCORE":
                    raw_score = (max_pts or 6.0) if len(qualified_projects) >= req_proj_count else 0.0
                elif t.scoring_mode == "SINGLE_PROJECT_BONUS":
                    raw_score = (max_pts or per_pts) if eff_count >= req_proj_count else 0.0
                else:  # PER_PROJECT_ADDITIVE
                    raw_score = eff_count * per_pts
                final_score = min(raw_score, max_pts) if max_pts is not None else raw_score

                # If incomplete projects could potentially increase the score, abstain instead of guessing!
                if incomplete_projects and (max_pts is None or final_score < max_pts):
                    return unknown(
                        f"已确认合格业绩{len(qualified_projects)}项，但仍有业绩材料链待确认({incomplete_projects})，无法确定最终得分。"
                    )
                return report(
                    "SCORED",
                    f"mode={t.scoring_mode}; qualified={qualified_projects}; disqualified={disqualified_projects}; score={final_score}",
                    float(final_score),
                )

            # Compliance mode evaluation (T0011)
            if len(qualified_projects) >= req_proj_count:
                return decide(
                    True,
                    f"qualified_projects={qualified_projects} (count={len(qualified_projects)}>={req_proj_count})",
                )
            if len(qualified_projects) + len(incomplete_projects) >= req_proj_count and incomplete_projects:
                return unknown(f"业绩证据链尚未确认完整: {incomplete_projects}")
            return decide(
                False,
                f"有效业绩数量不足: qualified={len(qualified_projects)}<{req_proj_count}; disqualified={disqualified_projects}",
            )

        # ------------------------------------------------------------------
        # R09: Multi-Field Arithmetic Formula & Net-Base Ratio Verification
        # ------------------------------------------------------------------
        if rule == "FORMULA_BOUND":
            if (
                not t.formula_type
                or t.formula_rate is None
                or not t.base_field
                or not t.target_field
                or not t.expected_unit
                or not b.actual_unit
            ):
                return unknown("缺少公式类型、费率、基数字段、目标字段或金额单位。")
            if t.expected_unit != b.actual_unit:
                return unknown(f"公式核算单位不一致: expected={t.expected_unit}, actual={b.actual_unit}")

            amounts = b.component_amounts or {}
            required_keys = [t.base_field, t.target_field] + list(t.deduction_fields or [])
            missing_keys = [k for k in required_keys if k not in amounts or amounts[k] is None]
            if missing_keys:
                return unknown(f"缺少公式基数或扣减分项金额: {missing_keys}，不能仅凭单一金额判定合规。")

            base_dec = Decimal(str(amounts[t.base_field]))
            target_dec = Decimal(str(amounts[t.target_field])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            rate_dec = Decimal(str(t.formula_rate))

            if t.formula_type == "NET_BASE_RATIO":
                deduction_sum = sum(Decimal(str(amounts[k])) for k in (t.deduction_fields or []))
                net_base = base_dec - deduction_sum
                if net_base <= 0:
                    return decide(False, f"公式扣减后净基数非正: net_base={net_base}")
                expected_dec = (net_base * rate_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            elif t.formula_type == "GROSS_RATIO":
                net_base = base_dec
                expected_dec = (base_dec * rate_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            else:
                return report("UNSUPPORTED_RULE", f"未支持的公式类型: {t.formula_type}")

            tol = Decimal("0.01")
            if t.operator in {">=", "=="}:
                passed = (
                    (target_dec + tol >= expected_dec)
                    if t.operator == ">="
                    else (abs(target_dec - expected_dec) <= tol)
                )
            elif t.operator == "<=":
                passed = target_dec - tol <= expected_dec
            else:
                return report("UNSUPPORTED_RULE", f"公式核算未支持运算符: {t.operator}")

            return decide(
                passed,
                f"formula={t.formula_type}; net_base={net_base:.2f}; required={expected_dec:.2f}; actual={target_dec:.2f}; op={t.operator}",
            )

        return report("UNSUPPORTED_RULE", "未支持的约束类型。")

"""
Deterministic Symbolic Rule Engine for TenderCheck (`src/rules/rule_engine.py`).

Takes `StructuredExtractionResult` produced by `src/extraction` (DeepSeek LLM or
Heuristic Extractor) and executes formal, deterministic symbolic rules to produce
explainable compliance verdicts (`COMPLIANT` / `NON_COMPLIANT`) with full rule traces.
"""

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, Dict, List, Optional, Tuple

from src.extraction.schema import (
    BidEvidenceSlots,
    StructuredExtractionResult,
    TenderConstraintSlots,
)


@dataclass
class RuleEvaluationReport:
    """Explainable output produced by the Symbolic Rule Engine for a single clause."""

    clause_id: str
    category: str
    verdict: str                           # "COMPLIANT" | "NON_COMPLIANT"
    triggered_rule_id: str                 # e.g. "R01_NUMERIC_BOUND"
    rule_passed: Optional[bool]
    symbolic_trace: str                    # Formal symbolic check expression
    explanation: str                       # Human-readable audit explanation
    evidence_pages: List[int] = field(default_factory=list)
    evidence_references: List[Dict[str, Any]] = field(default_factory=list)
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
            if g_val < 1000 and g_idx < len(groups) - 1:
                pass
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
    """Normalize minor canonical variants in RMB uppercase strings (e.g., 圆->元, 仟万/万, trailing 整)."""
    cleaned = re.sub(r"[\s人民币（）()¥￥:：大写]", "", str(text or ""))
    cleaned = cleaned.replace("圆", "元").replace("正", "整")
    return cleaned


def parse_qualification_rank(text: str) -> Tuple[str, int]:
    """
    Map Chinese qualification / constructor / engineering title string to `(family, rank_int)`
    where a higher integer represents a strictly higher qualification level.
    """
    t = str(text or "")
    # 1. Construction enterprise qualification: 特级(4) > 一级(3) > 二级(2) > 三级(1)
    if "总承包" in t or "专业承包" in t or "资质" in t:
        if "特级" in t:
            return ("enterprise_qual", 4)
        if "一级" in t or "壹级" in t:
            return ("enterprise_qual", 3)
        if "二级" in t or "贰级" in t:
            return ("enterprise_qual", 2)
        if "三级" in t or "叁级" in t:
            return ("enterprise_qual", 1)

    # 2. Registered Constructor: 一级注册建造师(2) > 二级注册建造师(1)
    if "建造师" in t:
        if "一级" in t or "壹级" in t:
            return ("constructor", 2)
        if "二级" in t or "贰级" in t:
            return ("constructor", 1)

    # 3. Technical title: 正高级(4) > 高级工程师(3) > 中级工程师/工程师(2) > 助理/初级(1)
    if "职称" in t or "工程师" in t:
        if "正高" in t or "研究员" in t or "教授级" in t:
            return ("title", 4)
        if "高级工程师" in t or "副高" in t:
            return ("title", 3)
        if "助理" in t or "初级" in t:
            return ("title", 1)
        if "中级" in t or "工程师" in t:
            return ("title", 2)

    return ("generic_cert", 0)


class ComplianceRuleEngine:
    """Compare explicitly extracted constraints and facts; never fill missing facts."""

    def evaluate(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t, b = extracted.tender_slots, extracted.bid_slots
        rule = t.constraint_type
        rule_id = {'NUMERIC_BOUND':'R01_NUMERIC_BOUND','QUALIFICATION_LEVEL':'R02_QUALIFICATION_HIERARCHY',
                   'ENTITY_CONSISTENCY':'R03_RMB_UPPER_AND_CONSISTENCY','DOCUMENT_PRESENCE_AND_SEAL':'R04_DOCUMENT_AND_SEAL',
                   'PROHIBITION_AND_COMMITMENT':'R05_PROHIBITION_AND_COMMITMENT',
                   'QUALITATIVE_OR_SCORING':'R06_QUALITATIVE_AND_SCORING'}.get(rule, 'UNSUPPORTED_RULE')

        def report(verdict, trace, score=None):
            return RuleEvaluationReport(
                clause_id=extracted.clause_id, category=extracted.category, verdict=verdict,
                triggered_rule_id=rule_id,
                rule_passed=True if verdict == 'COMPLIANT' else False if verdict == 'NON_COMPLIANT' else None,
                symbolic_trace=trace, explanation=trace, evidence_pages=b.evidence_pages,
                evidence_references=b.evidence_references, awarded_score=score)

        def unknown(reason):
            return report('INSUFFICIENT_EVIDENCE', reason)

        def decide(passed, trace):
            return report('COMPLIANT' if passed else 'NON_COMPLIANT', trace)

        if extracted.extraction_issues:
            return unknown('; '.join(extracted.extraction_issues))
        if b.conflict_detected:
            return report('CONFLICTING_EVIDENCE', '证据存在冲突，需要核对来源及主体。')
        if b.evidence_found is not True:
            return unknown('未提取到相关证据；检索未命中不能证明原文件缺失或违规。')

        if rule == 'NUMERIC_BOUND':
            if t.expected_numeric is None or b.actual_numeric is None:
                return unknown('缺少招标阈值或投标实际数值。')
            units = {'日历天': ('day', 1), '天': ('day', 1), '日': ('day', 1),
                     '元': ('money', 1), '万元': ('money', 10000),
                     '公里': ('length', 1000), 'km': ('length', 1000), '米': ('length', 1),
                     '%': ('ratio', 1), '人': ('people', 1)}
            eu, au = units.get(t.expected_unit), units.get(b.actual_unit)
            if not eu or not au or eu[0] != au[0]:
                return unknown('单位缺失或不兼容，不能按数值大小猜测单位。')
            expected, actual = float(t.expected_numeric)*eu[1], float(b.actual_numeric)*au[1]
            import operator
            compare = {'<=': operator.le, '<': operator.lt, '>=': operator.ge,
                       '>': operator.gt, '==': operator.eq}.get(t.operator)
            if compare is None:
                return report('UNSUPPORTED_RULE', '未支持的数值运算符。')
            if t.secondary_numeric is not None and b.secondary_actual_numeric is None:
                return unknown('第二项数值约束缺少投标事实。')
            primary = compare(Decimal(str(actual)), Decimal(str(expected)))
            secondary = (t.secondary_numeric is None or
                         Decimal(str(t.secondary_numeric))*Decimal(str(eu[1])) ==
                         Decimal(str(b.secondary_actual_numeric))*Decimal(str(au[1])))
            return decide(primary and secondary, f'{actual} {t.operator} {expected}; secondary={secondary}')

        if rule == 'QUALIFICATION_LEVEL':
            if not t.expected_text or not b.actual_text:
                return unknown('资质要求或实际资质缺失。')
            if t.required_domain and not b.actual_domain:
                return unknown('缺少实际专业。')
            needs_validity = '有效' in (t.expected_text + extracted.requirement_text)
            if needs_validity and b.certificate_valid is None:
                return unknown('证书有效性尚未确认。')
            required_family, required_rank = parse_qualification_rank(t.expected_text)
            actual_family, actual_rank = parse_qualification_rank(b.actual_text)
            if required_rank == 0 or actual_rank == 0:
                return report('UNSUPPORTED_RULE', '当前规则未识别该资质体系，需人工核对。')
            domain_ok = not t.required_domain or t.required_domain.strip() == b.actual_domain.strip()
            valid_ok = b.certificate_valid is not False
            rank_ok = required_family == actual_family and actual_rank >= required_rank
            return decide(rank_ok and domain_ok and valid_ok,
                          f'rank={actual_family}:{actual_rank}/{required_family}:{required_rank}; domain={domain_ok}; valid={valid_ok}')

        if rule == 'ENTITY_CONSISTENCY':
            if not b.entity_primary or not b.entity_secondary:
                return unknown('缺少两个独立来源的比对值，不能复制已有主体补齐。')
            if t.target_field == 'rmb_upper_consistency':
                if b.actual_numeric is None or not b.upper_amount_text:
                    return unknown('缺少大小写金额。')
                upper_ok = normalize_rmb_upper_text(number_to_rmb_upper(b.actual_numeric)) == normalize_rmb_upper_text(b.upper_amount_text)
                try:
                    cross_ok = Decimal(b.entity_primary) == Decimal(b.entity_secondary)
                except Exception:
                    return unknown('跨表报价不是明确数值。')
                return decide(upper_ok and cross_ok, f'uppercase={upper_ok}; cross_table={cross_ok}')
            normalize = lambda x: re.sub(r'\s+', '', x).replace('（','(').replace('）',')')
            return decide(normalize(b.entity_primary) == normalize(b.entity_secondary),
                          f'{b.entity_primary} == {b.entity_secondary}')

        if rule == 'DOCUMENT_PRESENCE_AND_SEAL':
            if b.document_present is None:
                return unknown('所需材料的完整性尚未确认。')
            if b.document_present is False:
                return decide(False, '已提取到明确的材料缺失证据。')
            if t.requires_official_seal and b.has_official_seal is None:
                return unknown('文本检索不能确认图像中的印章状态。')
            if t.requires_signature and b.has_signature is None:
                return unknown('签字状态尚未确认。')
            passed = (not t.requires_official_seal or b.has_official_seal is True) and (not t.requires_signature or b.has_signature is True)
            return decide(passed, f'seal={b.has_official_seal}; signature={b.has_signature}')

        if rule == 'PROHIBITION_AND_COMMITMENT':
            if b.prohibition_violated is True or b.commitment_fulfilled is False:
                return decide(False, '已提取出明确违反或拒绝承诺的证据。')
            if b.prohibition_violated is None or b.commitment_fulfilled is None:
                return unknown('禁止事项或承诺状态未确认。')
            return decide(True, '已提取出对应禁止事项与承诺的明确响应。')

        if rule == 'QUALITATIVE_OR_SCORING':
            if t.operator == 'SCORE_BRACKET':
                if not t.scoring_bands or b.actual_numeric is None or not b.actual_unit or b.actual_unit != t.expected_unit:
                    return unknown('评分档位、实际值或一致的单位缺失。')
                import math
                candidates=[]
                for band in t.scoring_bands:
                    if not isinstance(band, dict):
                        return report('UNSUPPORTED_RULE', '评分档位格式无效。')
                    low, high, points = band.get('min'), band.get('max'), band.get('points')
                    if type(points) not in (int, float) or not math.isfinite(points):
                        return report('UNSUPPORTED_RULE', '评分分值无效。')
                    if any(x is not None and (type(x) not in (int,float) or not math.isfinite(x)) for x in (low,high)):
                        return report('UNSUPPORTED_RULE', '评分边界无效。')
                    if low is not None and high is not None and low > high:
                        return report('UNSUPPORTED_RULE', '评分边界倒置。')
                    if any(type(band.get(k)) is not bool for k in ('min_inclusive','max_inclusive')):
                        return report('UNSUPPORTED_RULE', '评分边界是否包含未明确。')
                    lo_ok = low is None or (b.actual_numeric >= low if band['min_inclusive'] else b.actual_numeric > low)
                    hi_ok = high is None or (b.actual_numeric <= high if band['max_inclusive'] else b.actual_numeric < high)
                    if lo_ok and hi_ok:
                        candidates.append(points)
                if len(candidates) != 1:
                    return unknown('评分档位重叠或未覆盖实际值。')
                return report('SCORED', '按已提取的评分档位计分；得零分不自动等于不合规。', candidates[0])
            if b.qualitative_match is None:
                return unknown('定性响应尚未确认。')
            return decide(b.qualitative_match, '根据带引用的定性响应判断。')
        return report('UNSUPPORTED_RULE', '未支持的约束类型。')

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
    rule_passed: bool
    symbolic_trace: str                    # Formal symbolic check expression
    explanation: str                       # Human-readable audit explanation
    evidence_pages: List[int] = field(default_factory=list)
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


def normalize_unit_value(value: Optional[float], unit: str, target_unit: str) -> Optional[float]:
    """Convert numeric value to match `target_unit` when units differ by `万元` vs `元`."""
    if value is None:
        return None
    u_src = (unit or "").strip()
    u_tgt = (target_unit or "").strip()
    if u_src == u_tgt or not u_src or not u_tgt:
        return float(value)
    if "万元" in u_src and u_tgt == "元":
        return float(value) * 10000.0
    if u_src == "元" and "万元" in u_tgt:
        return float(value) / 10000.0
    # Handle implicit unit scale mismatch when LLM leaves actual_unit blank or same
    return float(value)


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
        if "中级" in t or "工程师" in t:
            return ("title", 2)
        if "助理" in t or "初级" in t:
            return ("title", 1)

    return ("generic_cert", 1 if t.strip() else 0)


class ComplianceRuleEngine:
    """
    Deterministic Symbolic Rule Engine (`src/rules`).
    Evaluates `StructuredExtractionResult` across 6 formal symbolic rule families.
    """

    def evaluate(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        ctype = extracted.tender_slots.constraint_type
        if ctype == "NUMERIC_BOUND":
            return self._eval_numeric_bound(extracted)
        if ctype == "QUALIFICATION_LEVEL":
            return self._eval_qualification_level(extracted)
        if ctype == "ENTITY_CONSISTENCY":
            return self._eval_entity_consistency(extracted)
        if ctype == "DOCUMENT_PRESENCE_AND_SEAL":
            return self._eval_document_and_seal(extracted)
        if ctype == "PROHIBITION_AND_COMMITMENT":
            return self._eval_prohibition_and_commitment(extracted)
        return self._eval_qualitative_or_scoring(extracted)

    def _eval_numeric_bound(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t = extracted.tender_slots
        b = extracted.bid_slots
        rule_id = "R01_NUMERIC_BOUND"

        if not b.evidence_found or b.actual_numeric is None:
            return RuleEvaluationReport(
                clause_id=extracted.clause_id,
                category=extracted.category,
                verdict="NON_COMPLIANT",
                triggered_rule_id=rule_id,
                rule_passed=False,
                symbolic_trace=f"actual_numeric=None vs expected {t.operator} {t.expected_numeric} [FAIL: MISSING_VALUE]",
                explanation=f"未能在投标证据中提取到有效数值（招标要求: {t.target_field} {t.operator} {t.expected_numeric}{t.expected_unit}）。",
                evidence_pages=b.evidence_pages,
            )

        exp_val = float(t.expected_numeric) if t.expected_numeric is not None else 0.0
        act_val = normalize_unit_value(b.actual_numeric, b.actual_unit, t.expected_unit) or 0.0

        # Auto-reconcile 万元 vs 元 if ratio is ~10000x
        if exp_val > 0 and act_val > 0:
            if abs(act_val / 10000.0 - exp_val) < 1e-2 and ("万" in t.expected_unit or "万元" in extracted.requirement_text):
                act_val = act_val / 10000.0
            elif abs(act_val * 10000.0 - exp_val) < 1.0 and ("万" in b.actual_unit or act_val < 1000 < exp_val):
                act_val = act_val * 10000.0

        op = (t.operator or "<=").strip()
        if op in ("<=", "LE"):
            primary_ok = act_val <= exp_val + 1e-4
            op_sym = "<="
        elif op in ("<", "LT"):
            primary_ok = act_val < exp_val
            op_sym = "<"
        elif op in (">=", "GE"):
            primary_ok = act_val >= exp_val - 1e-4
            op_sym = ">="
        elif op in (">", "GT"):
            primary_ok = act_val > exp_val
            op_sym = ">"
        else:
            primary_ok = abs(act_val - exp_val) <= max(1e-2, abs(exp_val) * 1e-5)
            op_sym = "=="

        traces = [f"{act_val:g} {op_sym} {exp_val:g} ({'PASS' if primary_ok else 'FAIL'})"]

        # Check secondary numeric constraint if specified (e.g., provisional sum 暂列金 == 8000000.00)
        secondary_ok = True
        if t.secondary_numeric is not None and b.secondary_actual_numeric is not None:
            sec_exp = float(t.secondary_numeric)
            sec_act = float(b.secondary_actual_numeric)
            if sec_exp > 10000 and sec_act < 10000:
                sec_act *= 10000.0
            secondary_ok = abs(sec_act - sec_exp) <= 1.0
            traces.append(
                f"secondary: {sec_act:g} == {sec_exp:g} ({'PASS' if secondary_ok else 'FAIL'})"
            )
        elif t.secondary_numeric is not None and b.secondary_actual_numeric is None:
            # Check if secondary number appears in text summary
            if str(int(t.secondary_numeric)) not in (b.evidence_summary + " " + b.actual_text):
                secondary_ok = False
                traces.append(f"secondary: None != {t.secondary_numeric:g} (FAIL)")

        # Check cross-page numerical mentions consistency if multiple mentions exist
        mentions_ok = True
        if len(b.page_mentions) >= 2:
            vals = []
            for m in b.page_mentions:
                try:
                    vals.append(float(re.sub(r"[^\d.]", "", str(m.get("value", "")))))
                except ValueError:
                    pass
            if len(vals) >= 2 and (max(vals) - min(vals) > 1e-2):
                mentions_ok = False
                traces.append(f"cross_page_mentions={vals} (FAIL: INCONSISTENT)")

        passed = primary_ok and secondary_ok and mentions_ok
        trace_str = "; ".join(traces)
        if passed:
            expl = (
                f"符号规则校验通过：投标文件实测值 {act_val:g}{t.expected_unit} "
                f"满足招标约束 ({op_sym} {exp_val:g}{t.expected_unit})。"
            )
        else:
            expl = (
                f"符号规则校验不通过：投标文件实测值 {act_val:g}{t.expected_unit} "
                f"违反招标约束 ({op_sym} {exp_val:g}{t.expected_unit}) [{trace_str}]。"
            )
        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
        )

    def _eval_qualification_level(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t = extracted.tender_slots
        b = extracted.bid_slots
        rule_id = "R02_QUALIFICATION_HIERARCHY"

        if not b.evidence_found:
            return RuleEvaluationReport(
                clause_id=extracted.clause_id,
                category=extracted.category,
                verdict="NON_COMPLIANT",
                triggered_rule_id=rule_id,
                rule_passed=False,
                symbolic_trace="evidence_found=False [FAIL]",
                explanation="未找到对应的企业或人员资质/证书证明材料。",
                evidence_pages=b.evidence_pages,
            )

        valid_ok = b.certificate_valid is not False
        req_family, req_rank = parse_qualification_rank(t.expected_text or extracted.requirement_text)
        act_family, act_rank = parse_qualification_rank(b.actual_text or b.evidence_summary)

        # Check domain compatibility (e.g. 公路工程 vs 市政公用工程)
        domain_ok = True
        if t.required_domain and b.actual_domain:
            req_dom = t.required_domain.replace("工程", "").strip()
            act_dom = b.actual_domain.replace("工程", "").strip()
            if req_dom and act_dom and (req_dom not in act_dom) and (act_dom not in req_dom):
                # Allow 道路/桥梁/交通土建 for 公路工程相关专业
                if not (req_dom == "公路" and any(k in act_dom for k in ("公路", "道路", "桥梁", "交通", "隧道"))):
                    domain_ok = False

        # Evaluate rank hierarchy
        if req_family == act_family and req_rank > 0:
            rank_ok = act_rank >= req_rank
        else:
            rank_ok = (act_rank >= req_rank) and (not b.prohibition_violated)

        # Also check numeric sub-constraints on performance qualifications (e.g., >= 15km or >= 5000万)
        num_ok = True
        if t.expected_numeric is not None and b.actual_numeric is not None:
            num_ok = float(b.actual_numeric) >= float(t.expected_numeric) - 1e-4

        passed = valid_ok and domain_ok and rank_ok and num_ok
        trace_str = (
            f"rank({act_family}:{act_rank} >= {req_family}:{req_rank})={rank_ok}; "
            f"domain_ok={domain_ok}; cert_valid={valid_ok}; num_ok={num_ok}"
        )
        if passed:
            expl = f"资质与证书等级核验通过：投标方具备『{b.actual_text or b.evidence_summary}』，满足『{t.expected_text or extracted.requirement_text}』要求。"
        else:
            expl = f"资质与证书等级核验不通过：投标方『{b.actual_text or b.evidence_summary}』不满足『{t.expected_text or extracted.requirement_text}』 [{trace_str}]。"

        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
        )

    def _eval_entity_consistency(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t = extracted.tender_slots
        b = extracted.bid_slots
        rule_id = "R03_RMB_UPPER_AND_CONSISTENCY"

        # Case A: RMB Uppercase vs Lowercase Numeric Amount Verification
        if (
            t.target_field == "rmb_upper_consistency"
            or "大写" in extracted.requirement_text
            or (b.upper_amount_text and b.actual_numeric is not None)
        ):
            if b.actual_numeric is None or not b.upper_amount_text:
                return RuleEvaluationReport(
                    clause_id=extracted.clause_id,
                    category=extracted.category,
                    verdict="NON_COMPLIANT",
                    triggered_rule_id=rule_id,
                    rule_passed=False,
                    symbolic_trace="missing numeric or uppercase amount [FAIL]",
                    explanation="缺少小写报价或大写报价文本，无法完成大小写一致性核验。",
                    evidence_pages=b.evidence_pages,
                )
            computed_upper = number_to_rmb_upper(float(b.actual_numeric))
            norm_computed = normalize_rmb_upper_text(computed_upper)
            norm_actual = normalize_rmb_upper_text(b.upper_amount_text)
            upper_ok = norm_computed == norm_actual

            # Also verify if entity_primary and entity_secondary are two numeric prices (e.g. BOQ vs Bid Letter)
            cross_num_ok = True
            try:
                if b.entity_primary and b.entity_secondary and re.fullmatch(r"[\d.]+", str(b.entity_secondary)):
                    cross_num_ok = abs(float(b.entity_primary) - float(b.entity_secondary)) < 1e-2
            except ValueError:
                pass

            passed = upper_ok and cross_num_ok
            trace_str = (
                f"RMB_UPPER({b.actual_numeric:.2f})='{norm_computed}' == "
                f"actual='{norm_actual}' ({'PASS' if upper_ok else 'FAIL'}); "
                f"cross_num_ok={cross_num_ok}"
            )
            expl = (
                f"大小写与跨表报价一致性核验{'通过' if passed else '不通过'}："
                f"小写 {b.actual_numeric:.2f} 元对应标准大写『{computed_upper}』，"
                f"投标文件大写为『{b.upper_amount_text}』。"
            )
            return RuleEvaluationReport(
                clause_id=extracted.clause_id,
                category=extracted.category,
                verdict="COMPLIANT" if passed else "NON_COMPLIANT",
                triggered_rule_id=rule_id,
                rule_passed=passed,
                symbolic_trace=trace_str,
                explanation=expl,
                evidence_pages=b.evidence_pages,
            )

        # Case B: Entity / Organization Name Exact Equality
        ent1 = re.sub(r"[\s（）()]", "", str(b.entity_primary or ""))
        ent2 = re.sub(r"[\s（）()]", "", str(b.entity_secondary or ""))
        passed = bool(ent1 and ent2 and ent1 == ent2)
        trace_str = f"entity_primary='{ent1}' == entity_secondary='{ent2}' ({'PASS' if passed else 'FAIL'})"
        expl = (
            f"主体名称一致性核验{'通过' if passed else '不通过'}："
            f"主体1『{b.entity_primary}』与主体2『{b.entity_secondary}』{'完全一致' if passed else '存在不一致'}。"
        )
        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
        )

    def _eval_document_and_seal(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t = extracted.tender_slots
        b = extracted.bid_slots
        rule_id = "R04_DOCUMENT_AND_SEAL"

        doc_ok = bool(b.evidence_found) and not b.prohibition_violated
        seal_ok = (not t.requires_official_seal) or bool(b.has_official_seal)
        sig_ok = (not t.requires_signature) or bool(b.has_signature)

        passed = doc_ok and seal_ok and sig_ok
        trace_str = (
            f"document_found={doc_ok}; "
            f"seal(req={t.requires_official_seal}, actual={b.has_official_seal})={seal_ok}; "
            f"sig(req={t.requires_signature}, actual={b.has_signature})={sig_ok}"
        )
        expl = (
            f"文件组成与签章核验{'通过' if passed else '不通过'}："
            f"{b.evidence_summary or b.actual_text or '已核对文件存在性与盖章状态'} [{trace_str}]。"
        )
        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
        )

    def _eval_prohibition_and_commitment(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        b = extracted.bid_slots
        rule_id = "R05_PROHIBITION_AND_COMMITMENT"

        passed = (
            bool(b.evidence_found)
            and (not b.prohibition_violated)
            and bool(b.commitment_fulfilled)
        )
        trace_str = (
            f"evidence_found={b.evidence_found}; "
            f"prohibition_violated={b.prohibition_violated}; "
            f"commitment_fulfilled={b.commitment_fulfilled}"
        )
        expl = (
            f"禁止性底线与承诺核验{'通过' if passed else '不通过'}："
            f"{b.evidence_summary or b.actual_text or '已核验无违规情形且承诺完备'}。"
        )
        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
        )

    def _eval_qualitative_or_scoring(self, extracted: StructuredExtractionResult) -> RuleEvaluationReport:
        t = extracted.tender_slots
        b = extracted.bid_slots
        rule_id = "R06_QUALITATIVE_AND_SCORING"

        # Special symbolic bracket calculation for Debt-to-Asset Ratio (资产负债率 T0214)
        if "资产负债率" in extracted.requirement_text and b.actual_numeric is not None:
            ratio = float(b.actual_numeric)
            if ratio < 75.0:
                score = 6.0
            elif ratio <= 80.0:
                score = 4.0
            elif ratio <= 85.0:
                score = 2.0
            else:
                score = 0.0
            passed = score > 0.0
            trace_str = f"debt_ratio={ratio:.2f}% -> bracket_score={score:.1f}/6.0"
            return RuleEvaluationReport(
                clause_id=extracted.clause_id,
                category=extracted.category,
                verdict="COMPLIANT" if passed else "NON_COMPLIANT",
                triggered_rule_id=rule_id,
                rule_passed=passed,
                symbolic_trace=trace_str,
                explanation=f"财务评分规则计算：资产负债率为 {ratio:.2f}%，按档位规则得 {score:.1f} 分。",
                evidence_pages=b.evidence_pages,
                awarded_score=score,
            )

        passed = (
            bool(b.evidence_found)
            and bool(b.qualitative_match)
            and (not b.prohibition_violated)
        )
        score = b.estimated_score if b.estimated_score is not None else t.scoring_max_points
        trace_str = (
            f"evidence_found={b.evidence_found}; "
            f"qualitative_match={b.qualitative_match}; "
            f"score={score}"
        )
        expl = (
            f"定性响应与评审核验{'通过' if passed else '不通过'}："
            f"{b.evidence_summary or b.actual_text or '已响应招标技术与评审要求'}。"
        )
        return RuleEvaluationReport(
            clause_id=extracted.clause_id,
            category=extracted.category,
            verdict="COMPLIANT" if passed else "NON_COMPLIANT",
            triggered_rule_id=rule_id,
            rule_passed=passed,
            symbolic_trace=trace_str,
            explanation=expl,
            evidence_pages=b.evidence_pages,
            awarded_score=score if passed else 0.0,
        )

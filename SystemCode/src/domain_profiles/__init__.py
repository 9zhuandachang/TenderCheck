"""
Domain Profiles & Cross-Region Engineering Vocabulary for TenderCheck.

Externalizes domain-specific qualification hierarchies, related major/title sets,
nationwide safety assessment certificate codes (`交安`/`建安`/`水安` A/B/C),
credit-scoring specifications, and consortium agreement detection across
Highway, Water Conservancy, Municipal EPC, and Building Construction tenders.
"""
from __future__ import annotations

import datetime
import functools
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROFILES_PATH = Path(__file__).resolve().parent / "profiles.json"


@functools.lru_cache(maxsize=1)
def load_domain_profiles() -> Dict[str, Any]:
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def _build_role_alias_map() -> Dict[str, str]:
    data = load_domain_profiles()
    mapping: Dict[str, str] = {}
    for canonical, aliases in data.get("role_aliases", {}).items():
        for alias in aliases:
            mapping[alias] = canonical
    return mapping


ROLE_ALIAS_MAP: Dict[str, str] = _build_role_alias_map()

_PROVINCES = "".join(load_domain_profiles().get("province_Abbreviations", []))
NATIONWIDE_SAFETY_CERT_RE = re.compile(
    rf"(?:[{_PROVINCES}])?(?:交安|建安|水安)\s*([ABC])(?:\s*[（(]|\d|类|证|$)"
)

CN_COUNT_MAP = {"零": 0, "无": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2, "三": 3, "叁": 3, "四": 4, "五": 5}


def canonical_role(role_text: str) -> str:
    cleaned = re.sub(r"\s+", "", str(role_text or ""))
    cleaned = re.sub(r"^(?:评分用|拟派|拟任|拟委任|本标段)", "", cleaned)
    cleaned = re.sub(r"[0-9]+$", "", cleaned)
    return ROLE_ALIAS_MAP.get(cleaned, cleaned)


def extract_qualification_specialty(text: str) -> str:
    """
    Extract canonical engineering sector specialty from an enterprise or constructor
    qualification string (e.g., '水利水电工程施工总承包壹级' -> '水利水电工程').
    Returns '' if no sector-specific specialty is stated.
    """
    s = re.sub(r"\s+", "", str(text or ""))
    if not s:
        return ""
    profiles = load_domain_profiles()
    # Match longest alias first so '水利水电机电安装工程' precedes '水利水电' or '机电'
    candidates: List[Tuple[int, str]] = []
    for item in profiles.get("qualification_specialties", []):
        canon = item["canonical"]
        for alias in item.get("aliases", []):
            if alias in s:
                candidates.append((len(alias), canon))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def related_title_domains_for(required_domain: str, requirement_text: str = "") -> Set[str]:
    """
    Return the set of acceptable engineering title/major names for a '相关专业' or
    broad '工程类' requirement across Highway, Water Conservancy, Municipal, and Building.
    """
    profiles = load_domain_profiles()
    combined = f"{required_domain or ''} {requirement_text or ''}"
    domains_cfg = profiles.get("domains", {})

    # Broad '工程类专业' / '工程类相关专业' accepts union of all engineering domains
    if re.search(r"工程类(?:相关)?专业|工科(?:类)?专业", combined) or required_domain in {"工程类", "工程类专业"}:
        all_majors: Set[str] = {"工程类", "工程类专业", "土木工程", "工程管理", "工程测量"}
        for d_cfg in domains_cfg.values():
            all_majors.update(d_cfg.get("related_title_domains", []))
        return all_majors

    matched: Set[str] = set()
    for d_cfg in domains_cfg.values():
        if any(kw in combined for kw in d_cfg.get("trigger_keywords", [])):
            matched.update(d_cfg.get("related_title_domains", []))

    return matched


def all_market_directory_keywords() -> Tuple[str, ...]:
    profiles = load_domain_profiles()
    kws: List[str] = ["企业名录", "信用信息管理系统", "监管服务平台", "监管平台", "四库一平台"]
    for d_cfg in profiles.get("domains", {}).values():
        for kw in d_cfg.get("market_directories", []):
            if kw not in kws:
                kws.append(kw)
    return tuple(kws)


def match_safety_cert_level(text: str) -> Optional[str]:
    """
    Identify safety production assessment certificate level ('safety_a' | 'safety_b' | 'safety_c')
    across nationwide Traffic (交安), Housing/Municipal (建安), and Water Conservancy (水安) systems.
    """
    t = str(text or "").strip()
    if not t:
        return None

    m_code = NATIONWIDE_SAFETY_CERT_RE.search(t)
    if m_code:
        letter = m_code.group(1).upper()
        return {"A": "safety_a", "B": "safety_b", "C": "safety_c"}[letter]

    has_safety_ctx = any(
        w in t
        for w in (
            "安全生产考核合格",
            "安全考核",
            "安考",
            "安全员",
            "水行政主管部门",
            "建设行政主管部门",
            "住房和城乡建设",
            "交通运输主管部门",
            "交通运输厅",
        )
    )
    if re.search(r"(?:安考|安全考核|[（(])?A(?:类|证)", t) and (has_safety_ctx or "主要负责人" in t):
        return "safety_a"
    if re.search(r"(?:安考|安全考核|[（(])?B(?:类|证)", t) or ("安全生产考核合格" in t and not re.search(r"[AC](?:类|证)", t)):
        return "safety_b"
    if re.search(r"(?:专职安全员|安全员)?C(?:类|证)", t) or ("专职安全员" in t and "考核合格" in t):
        return "safety_c"
    return None


def is_substantive_consortium_agreement(text: str) -> bool:
    """
    Detect whether a bid passage represents a genuine, filled-in Consortium Agreement (联合体协议书),
    excluding blank templates (`____`) and independent-bidder '无 / 不适用' declarations.
    """
    s = re.sub(r"\s+", "", str(text or ""))
    if not s or "联合体" not in s:
        return False

    # Reject explicit non-consortium declarations or empty Chapter 8/9 placeholders
    body_after_title = re.sub(r"^[一二三四五六七八九十0-9、.．（）()]*联合体协议书", "", s)
    if re.match(r"^(?:[:：]?(?:无|不适用|空白|略|/|—|-)[。.]?)", body_after_title):
        return False
    if any(
        neg in s
        for neg in (
            "不接受联合体",
            "不采用联合体投标",
            "非联合体投标",
            "独立投标人身份",
            "不以联合体形式",
            "未组成联合体",
        )
    ):
        return False
    if re.search(r"_{3,}|（盖单位章）.*_{2,}", s) and not re.search(r"(?:公司|集团|院|局)[、，,与和][^\n_]{2,30}(?:公司|集团|院|局)", s):
        return False

    has_agreement_clause = bool(
        re.search(
            r"自愿组成.{0,30}联合体|(?:公司|集团|院|局)[^。；]{0,20}自愿组成|联合体牵头人[:：|][^\n_]{4,}(?:公司|集团|院|局)|联合体成员[:：|][^\n_]{4,}(?:公司|集团|院|局)",
            s,
        )
    )
    return has_agreement_clause


def parse_credit_scoring_spec(
    requirement_text: str,
    review_reference_date: str = "",
) -> Dict[str, Any]:
    """
    Deterministically parse credit-rating scoring rules from a Chinese tender clause without
    any hardcoded year sets or province assumptions.

    Supports two standard Chinese engineering credit scoring structures:
    1. `multi_year_count`: e.g. '2022、2023、2024三年均AA得5分，两年AA得4分，一年AA得3分，无AA得0分'
       or '近三年水利建设市场信用等级...' (resolved relative to `review_reference_date`).
    2. `single_grade_map`: e.g. '水利建设市场信用等级AAA得3分、AA得2分、A得1分'.
    """
    req = str(requirement_text or "")
    out: Dict[str, Any] = {
        "scheme": "",
        "years": [],
        "target_grade": "",
        "count_score_map": {},
        "grade_score_map": {},
    }
    if not req:
        return out

    # 1. Extract explicit 4-digit calendar years mentioned before/around credit grade
    explicit_years = sorted({int(y) for y in re.findall(r"(?<!\d)(20\d{2})(?!\d)", req)})
    if not explicit_years and review_reference_date:
        m_rel = re.search(r"近([二两三四五2345])(?:个)?年度?", req)
        m_ref = re.match(r"^(\d{4})-\d{2}-\d{2}$", str(review_reference_date).strip())
        if m_rel and m_ref:
            n_years = CN_COUNT_MAP.get(m_rel.group(1), int(m_rel.group(1)) if m_rel.group(1).isdigit() else 0)
            ref_year = int(m_ref.group(1))
            if 1 <= n_years <= 5:
                explicit_years = list(range(ref_year - n_years, ref_year))

    # 2. Check for multi-year count scoring patterns (e.g., '三年均AA得5分，两年AA得4分，一年AA得3分，无AA得0分')
    count_map: Dict[int, float] = {}
    target_grade = ""
    for m in re.finditer(
        r"(?:([零无一壹二两贰三叁四五0-5])\s*年(?:均|为|获|评为)?|(?P<zero>无))\s*(AAA|AA|A)(?:级)?[^0-9。；，,\n]{0,8}?得\s*(\d+(?:\.\d+)?)\s*分",
        req,
    ):
        cnt_tok, zero_tok, grade_tok, pts_tok = m.group(1), m.group("zero"), m.group(3), m.group(4)
        cnt = 0 if zero_tok else (int(cnt_tok) if (cnt_tok and cnt_tok.isdigit()) else CN_COUNT_MAP.get(cnt_tok or "", -1))
        if cnt >= 0:
            count_map[cnt] = float(pts_tok)
            if not target_grade:
                target_grade = grade_tok

    if count_map and explicit_years:
        out["scheme"] = "multi_year_count"
        out["years"] = explicit_years
        out["target_grade"] = target_grade or "AA"
        out["count_score_map"] = count_map
        return out

    # 3. Check for single credit-grade point mapping (e.g., 'AAA得3分、AA得2分、A得1分')
    grade_map: Dict[str, float] = {}
    for m in re.finditer(r"(?<![A-Z])(AAA|AA|A|BBB|BB|B|C)(?:级)?[^0-9A-Z。；，,、\n]{0,6}?得\s*(\d+(?:\.\d+)?)\s*分", req):
        grade_map[m.group(1)] = float(m.group(2))

    if grade_map:
        out["scheme"] = "single_grade_map"
        out["years"] = explicit_years
        out["grade_score_map"] = grade_map
        if "C" not in grade_map:
            grade_map.setdefault("B", 0.0)
            grade_map.setdefault("C", 0.0)
        return out

    if explicit_years:
        out["years"] = explicit_years
    return out

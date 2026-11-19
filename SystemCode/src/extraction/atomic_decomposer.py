"""
Deterministic Compound-to-Atomic Tender Requirement Decomposer (Phase 2.2).

Splits multi-predicate compound clauses (common in Chinese tender qualification &
bidder-instruction tables) into single-constraint atomic requirements before
8-class classification and single-slot `TenderConstraintSlots` extraction, while
strictly preserving:
  1. Trigger conditions (`如...`, `若...`, `采用...时`)
  2. OR-alternatives (`，或...`, `或者...`, `满足下列条件之一`)
  3. Multi-bracket scoring ladders (`R06`: `AAA得3分，AA得2分...`)
  4. Single-project composite performance criteria (`R08`: date + amount + length + scope)
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Clauses that MUST stay intact as a single symbolic unit
_DO_NOT_SPLIT_RE = re.compile(
    r"(?:"
    r"满足下列条件之一|符合以下条件之一"
    r"|得\s*\d+(?:\.\d+)?\s*分.*得\s*\d+(?:\.\d+)?\s*分"
    r"|扣\s*\d+(?:\.\d+)?\s*分.*扣\s*\d+(?:\.\d+)?\s*分"
    r"|最高(?:得)?\s*\d+(?:\.\d+)?\s*分"
    r"|评分标准|评分档位|不良行为记录.*分"
    r"|至少完成(?:过)?|类似(?:工程|项目)?业绩|单项合同(?:价|金额)|单合同含"
    r"|安全生产费.*%|暂列金.*暂估价|占扣除.*比例"
    r")"
)

# Leading conditional triggers that govern the entire sentence
_CONDITIONAL_START_RE = re.compile(r"^(?:如|若|当|如果|凡|采用[^，,。；;]{1,15}(?:的|时)|对于[^，,。；;]{1,15})")

# Subject extraction from leading label or sentence head
_PREFIX_SUBJECT_RE = re.compile(
    r"^(?P<head>"
    r"(?:拟派|拟任|拟委任)?(?:项目经理(?:（项目负责人）|\(项目负责人\))?|项目负责人|项目总工|总工程师|技术负责人|设计负责人|"
    r"专职安全员|专职质检员|安全员|质检员|施工员|造价人员|委托代理人|企业主要负责人|投标人|联合体各方|联合体牵头人|投标文件|报价文件|商务文件|技术文件)"
    r")(?:资格要求|资质要求|要求|条件|基本要求)?\s*[：:]\s*(?P<body>.+)$"
)

_INLINE_SUBJECT_RE = re.compile(
    r"^(?P<head>"
    r"(?:拟派|拟任|拟委任)?(?:项目经理(?:（项目负责人）|\(项目负责人\))?|项目负责人|项目总工|总工程师|技术负责人|设计负责人|"
    r"专职安全员|专职质检员|安全员|质检员|施工员|造价人员|委托代理人|企业主要负责人|投标人|联合体各方|联合体牵头人|投标文件|报价文件|商务文件|技术文件)"
    r")(?=(?:须|必须|应|应当|不得|严禁|具|持|未|目前|为|在|按|均|提供|提交|附))"
)

# Action / predicate starters that indicate an independent requirement sub-clause
_PREDICATE_START_RE = re.compile(
    r"^(?:且|并|并且|同时|以及)?\s*"
    r"(?:须|必须|应|应当|均)?\s*"
    r"(?:具有|具备|持有|持|取得|获得|拥有|提供|提交|附|加盖|签署|签字|在投标单位注册|在(?:本|投标)单位注册|"
    r"为(?:投标人)?本单位人员|目前未在|未在|不得|严禁|无|并在人员)"
)

# OR / exception continuation that MUST be glued back to the previous clause
_OR_OR_EXCEPTION_START_RE = re.compile(
    r"^(?:或|或者|否则|但|但是|除外|其中|不含|含|以[^，,]{1,12}为准|具体|包括)"
)


def _split_top_level_commas(text: str) -> List[str]:
    """Split text on Chinese/ASCII commas or semicolons outside parentheses/brackets."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    open_chars = "（(【[《"
    close_chars = "）)】]》"
    for ch in text:
        if ch in open_chars:
            depth += 1
            buf.append(ch)
        elif ch in close_chars:
            depth = max(0, depth - 1)
            buf.append(ch)
        elif depth == 0 and ch in "，,；;":
            seg = "".join(buf).strip()
            if seg:
                parts.append(seg)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _split_inline_and_registration(seg: str) -> List[str]:
    """
    Split tightly coupled '具备...建造师资格且在投标单位注册' into two independent checks:
    qualification level (R02) and employer registration consistency (R03).
    """
    m = re.match(r"^(.{8,}?建造师(?:执业)?(?:资格|证书)?)\s*且\s*(在(?:投标|本)(?:单位|人)注册.*)$", seg)
    if m:
        return [m.group(1).strip(), m.group(2).strip()]
    return [seg]


def _normalize_subject(subject: str) -> str:
    # Strip parenthetical alias like '项目经理（项目负责人）' -> '项目经理' for concise clauses
    return re.sub(r"[（(][^）)]+[）)]", "", subject).strip()


def _attach_subject(subject: str, clause: str) -> str:
    clause = clause.strip().rstrip("，,；;")
    clause = re.sub(r"^(?:且|并|并且|同时|以及)\s*", "", clause).strip()
    if not clause:
        return ""
    if subject and not clause.startswith(subject) and not _INLINE_SUBJECT_RE.match(clause):
        if re.match(r"^(?:须|必须|应|应当|不得|严禁|目前|未|无)", clause):
            out = f"{subject}{clause}"
        else:
            out = f"{subject}须{clause}"
    else:
        out = clause
    if out and out[-1] not in "。；;！!？?）)":
        out += "。"
    return out


def decompose_compound_requirement(text: str, section: str = "") -> List[str]:
    """
    Decompose a single normalized tender requirement into 1 or more atomic requirements.
    Returns `[text]` unchanged when the clause is already atomic or should not be split.
    """
    raw = (text or "").strip()
    if not raw or len(raw) < 22:
        return [raw] if raw else []

    if _DO_NOT_SPLIT_RE.search(raw):
        return [raw]

    if _CONDITIONAL_START_RE.match(raw):
        return [raw]

    # Extract governing subject & body
    subject = ""
    body = raw
    m_pref = _PREFIX_SUBJECT_RE.match(raw)
    if m_pref:
        subject = _normalize_subject(m_pref.group("head"))
        body = m_pref.group("body").strip()
    else:
        m_inl = _INLINE_SUBJECT_RE.match(raw)
        if m_inl:
            subject = _normalize_subject(m_inl.group("head"))
            body = raw[m_inl.end():].strip()

    if not subject:
        return [raw]

    # Remove trailing period before comma splitting
    body_clean = body.rstrip("。.")
    raw_segments = _split_top_level_commas(body_clean)
    if len(raw_segments) <= 1:
        # Still check for tightly coupled '建造师资格且在投标单位注册'
        sub = _split_inline_and_registration(body_clean)
        if len(sub) > 1:
            return [_attach_subject(subject, s) for s in sub if s]
        return [raw]

    # Expand tightly coupled inline '且在投标单位注册'
    expanded: List[str] = []
    for seg in raw_segments:
        expanded.extend(_split_inline_and_registration(seg))

    # Regroup segments: only start a new atomic clause on a genuine independent predicate,
    # keeping OR alternatives / exceptions / modifiers attached to the preceding clause.
    grouped: List[str] = []
    for idx, seg in enumerate(expanded):
        if idx == 0:
            grouped.append(seg)
            continue
        if _OR_OR_EXCEPTION_START_RE.match(seg):
            grouped[-1] = f"{grouped[-1]}，{seg}"
            continue
        if _PREDICATE_START_RE.match(seg) and len(re.findall(r"[\u4e00-\u9fff0-9]", seg)) >= 5:
            grouped.append(seg)
        else:
            grouped[-1] = f"{grouped[-1]}，{seg}"

    if len(grouped) <= 1:
        return [raw]

    atomic_clauses: List[str] = []
    for g in grouped:
        clause = _attach_subject(subject, g)
        if clause and clause not in atomic_clauses:
            atomic_clauses.append(clause)

    return atomic_clauses if len(atomic_clauses) > 1 else [raw]

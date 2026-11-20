"""
Deterministic Chinese Tender Addendum & Clarification Superseding Engine (Phase 2.4).

Resolves conflicts between original tender requirements and subsequent addenda /
clarifications (`补遗书` / `答疑澄清` / `更正公告`) across all Chinese engineering domains:
1. Parses explicit Chinese amendment directives (`MODIFY`, `DELETE`, `SUPPLEMENT`).
2. Anchors each directive to prior active clauses via dual-signal matching
   (clause number + canonical bidding topic + old-value hint + chapter boundary guard).
3. Supports multi-round chained addenda (e.g., Addendum #2 superseding Addendum #1).
4. Enforces strict zero-silent-deletion: superseded/cancelled clauses retain full
   audit provenance (`lifecycle_status`, `superseded_by`), and ambiguous directives
   never silently delete an active tender redline (routed to human review instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Canonical engineering bidding topics for cross-domain semantic anchoring.
# Each tuple is (canonical_topic, is_singleton_topic, keywords).
_TOPIC_SPECS: Tuple[Tuple[str, bool, Tuple[str, ...]], ...] = (
    ("MAX_BID_PRICE", True, ("最高投标限价", "招标控制价", "最高限价", "控制价", "预算价")),
    ("CONSTRUCTION_DURATION", True, ("计划工期", "工期要求", "工期", "施工总工期", "交货期")),
    ("BID_BOND", True, ("投标保证金", "保证金金额", "保证金交纳", "保证金递交")),
    ("PROVISIONAL_SUM", True, ("暂列金额", "暂列金", "暂估价")),
    ("BID_DEADLINE", True, ("投标文件递交截止时间", "投标截止时间", "开标时间", "递交截止时间")),
    ("BID_VALIDITY", True, ("投标有效期",)),
    ("PERFORMANCE_BOND", True, ("履约保证金", "履约担保")),
    ("SAFETY_PRODUCTION_FEE", True, ("安全生产费", "安全文明施工费")),
    ("CONSORTIUM_RULE", True, ("联合体投标", "接受联合体", "不接受联合体", "联合体")),
    ("SUBCONTRACT_RULE", True, ("分包", "转包")),
    ("FINANCIAL_AUDIT", True, ("财务审计报告", "财务状况", "审计报告", "资产负债率")),
    ("CREDIT_RATING", True, ("信用等级", "信用评价", "信用分")),
    ("QUALITY_TARGET", True, ("质量要求", "质量标准", "工程质量")),
    ("SAFETY_TARGET", True, ("安全目标", "安全生产目标", "安全管理目标")),
    ("PROJECT_MANAGER", False, ("项目经理", "项目负责人", "建造师")),
    ("CHIEF_ENGINEER", False, ("项目总工", "技术负责人")),
    ("ENTERPRISE_QUALIFICATION", False, ("企业资质", "资质要求", "施工总承包", "专业承包", "设计资质")),
    ("SAFETY_LICENSE", False, ("安全生产许可证", "安许证")),
    ("ENTERPRISE_PERFORMANCE", False, ("企业业绩", "企业类似业绩", "投标人业绩", "类似项目业绩")),
    ("PM_PERFORMANCE", False, ("项目经理业绩", "项目负责人业绩")),
    ("STAFFING", False, ("主要人员", "项目管理机构", "专职安全员", "五大员", "八大员", "造价师")),
)

_SINGLETON_TOPICS = {name for name, is_single, _ in _TOPIC_SPECS if is_single}

_MODIFY_VERBS_RE = re.compile(
    r"(?:修改为|变更为|调整为|更正为|更改为|修正为|改为|现调整为|现修改为|现变更为|现更正为|现改为|以本(?:补遗|答疑|澄清|更正)[^。；]*为准)"
)
_DELETE_VERBS_RE = re.compile(
    r"(?:删除|删去|取消|废止|不再要求|免予提供|无需提供|不作要求|予以取消|予以删除)"
)
_SUPPLEMENT_VERBS_RE = re.compile(
    r"(?:补充(?:如下|条款|要求|规定|内容)|新增(?:条款|要求|规定|内容)|增加(?:以下|下列|一条|条款|要求))"
)

_CHAPTER_REF_RE = re.compile(r"第([一二三四五六七八九十0-9]+)[章节部分篇]")
# Match structured clause numbers like 3.2.4, 1.4.1, 10.2 (avoid dates 2025.03.01 & amounts 5000.00万元)
_CLAUSE_NO_RE = re.compile(
    r"(?<![0-9.])(\d{1,2}\.\d{1,2}(?:\.\d{1,2})?)(?![0-9.])(?!\s*(?:万元|亿元|元|%|％|公里|km|米|m|天|日历天|年|月|分|吨|平方米|㎡))"
)
_CN_ARTICLE_NO_RE = re.compile(r"第([一二三四五六七八九十百0-9]+)[条款项]")

_OLD_VALUE_BY_RE = re.compile(
    r"(?:由|将原(?:招标文件)?[^。；]{0,30}?(?:中的|的)?)\s*[“\"「]?([^”\"」修改变更调整更正]{2,45}?)[”\"」]?\s*(?:修改为|变更为|调整为|更正为|更改为|修正为|改为)"
)
_ORIG_PREFIX_RE = re.compile(
    r"原(?:招标文件|条款|要求|规定|前附表)?[^。；]{0,50}?(?:为|是|要求)\s*[“\"「]?([^”\"」，。；]{2,40})[”\"」]?"
)
_ADDENDUM_SEQ_RE = re.compile(r"(?:第\s*([0-9一二三四五六七八九十]+)\s*(?:号|次)?\s*(?:补遗|答疑|澄清|更正)|(?:补遗|答疑|澄清|更正)\s*([0-9一二三四五六七八九十]+))")

_CN_DIGIT_MAP = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


@dataclass
class AddendumDirective:
    """Parsed amendment intent extracted from a single addendum / clarification clause."""

    action: str  # "MODIFY" | "DELETE" | "SUPPLEMENT" | "NONE"
    target_clause_nos: List[str] = field(default_factory=list)
    target_chapter: str = ""
    target_topics: List[str] = field(default_factory=list)
    old_value_hint: str = ""
    new_requirement_text: str = ""


@dataclass
class SupersedingReport:
    """Audit summary returned by `apply_addendum_superseding`."""

    total_items: int = 0
    active_count: int = 0
    superseded_count: int = 0
    cancelled_count: int = 0
    directive_only_count: int = 0
    unmatched_addenda_count: int = 0
    links: List[Dict[str, Any]] = field(default_factory=list)


def _cn_or_int_to_int(token: str) -> int:
    token = (token or "").strip()
    if token.isdigit():
        return int(token)
    if token in _CN_DIGIT_MAP:
        return _CN_DIGIT_MAP[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CN_DIGIT_MAP:
        return 10 + _CN_DIGIT_MAP[token[1]]
    return 0


def _extract_addendum_round(doc_name: str, section: str, text: str) -> int:
    for source in (doc_name, section, text[:60]):
        m = _ADDENDUM_SEQ_RE.search(source or "")
        if m:
            raw = m.group(1) or m.group(2) or ""
            val = _cn_or_int_to_int(raw)
            if val > 0:
                return val
    return 0


def _extract_clause_numbers(text: str) -> List[str]:
    found: List[str] = []
    for m in _CLAUSE_NO_RE.finditer(text or ""):
        num = m.group(1)
        # Skip likely year-month patterns like 2025.03
        head = int(num.split(".")[0])
        if head > 30:
            continue
        if num not in found:
            found.append(num)
    return found


def _extract_chapter(text: str) -> str:
    m = _CHAPTER_REF_RE.search(text or "")
    if not m:
        return ""
    raw = m.group(1)
    # Normalize Arabic/Chinese chapter numbers to integer string for reliable comparison
    val = _cn_or_int_to_int(raw)
    return f"CH{val}" if val > 0 else f"CH_{raw}"


def _extract_topics(text: str) -> List[str]:
    found: List[str] = []
    for topic_name, _, keywords in _TOPIC_SPECS:
        if any(kw in (text or "") for kw in keywords):
            found.append(topic_name)
    return found


def parse_addendum_directive(text: str, section: str = "") -> AddendumDirective:
    """Deterministically parse amendment action, target anchors, and effective text from an addendum clause."""
    raw = (text or "").strip()
    if not raw:
        return AddendumDirective(action="NONE")

    combined = f"{section} {raw}".strip()
    has_modify = bool(_MODIFY_VERBS_RE.search(raw))
    has_delete = bool(_DELETE_VERBS_RE.search(raw))
    has_supplement = bool(_SUPPLEMENT_VERBS_RE.search(raw))

    if has_modify:
        action = "MODIFY"
    elif has_delete:
        action = "DELETE"
    elif has_supplement:
        action = "SUPPLEMENT"
    else:
        return AddendumDirective(
            action="NONE",
            target_clause_nos=_extract_clause_numbers(combined),
            target_chapter=_extract_chapter(combined),
            target_topics=_extract_topics(combined),
            new_requirement_text=raw,
        )

    clause_nos = _extract_clause_numbers(combined)
    chapter = _extract_chapter(combined)
    topics = _extract_topics(combined)

    old_hint = ""
    m_old = _OLD_VALUE_BY_RE.search(raw)
    if m_old:
        old_hint = m_old.group(1).strip(" ，,。；;：:")
    else:
        m_orig = _ORIG_PREFIX_RE.search(raw)
        if m_orig:
            old_hint = m_orig.group(1).strip(" ，,。；;：:")

    new_text = raw
    if action == "MODIFY":
        m_mod = _MODIFY_VERBS_RE.search(raw)
        if m_mod:
            after = raw[m_mod.end():].strip(" ：:，,。")
            if len(after) >= 3:
                # Preserve topic prefix so downstream extractors know what the new value constrains
                prefix = ""
                if topics:
                    first_kw = next((kw for name, _, kws in _TOPIC_SPECS if name == topics[0] for kw in kws if kw in raw), "")
                    if first_kw and first_kw not in after:
                        prefix = f"{first_kw}："
                new_text = f"{prefix}{after}"
                if new_text and new_text[-1] not in "。；;！!？?":
                    new_text += "。"

    return AddendumDirective(
        action=action,
        target_clause_nos=clause_nos,
        target_chapter=chapter,
        target_topics=topics,
        old_value_hint=old_hint,
        new_requirement_text=new_text,
    )


def _get_attr(item: Any, key: str, default: Any = "") -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _set_attr(item: Any, key: str, value: Any) -> None:
    if isinstance(item, dict):
        item[key] = value
    else:
        setattr(item, key, value)


def _is_addendum_item(item: Any) -> bool:
    explicit = _get_attr(item, "is_addendum", None)
    if isinstance(explicit, bool):
        return explicit
    sec = str(_get_attr(item, "Section", "") or _get_attr(item, "section", "") or "")
    doc_name = str(_get_attr(item, "doc_name", "") or "")
    return any(k in f"{doc_name} {sec}" for k in ("补遗", "答疑", "澄清", "更正公告", "修改通知"))


def _compact_tokens(text: str) -> List[str]:
    cleaned = re.sub(r"[^\u4e00-\u9fff0-9A-Za-z.%]+", " ", text or "")
    return [tok for tok in cleaned.split() if len(tok) >= 2]


def _old_hint_matches_target(old_hint: str, target_text: str) -> bool:
    if not old_hint or not target_text:
        return False
    compact_hint = re.sub(r"\s+", "", old_hint)
    compact_target = re.sub(r"\s+", "", target_text)
    if len(compact_hint) >= 3 and compact_hint in compact_target:
        return True
    # Check numeric anchors inside old_hint (e.g. "5000万元" / "360日历天")
    nums = re.findall(r"\d+(?:\.\d+)?", compact_hint)
    if nums and all(n in compact_target for n in nums):
        return True
    tokens = [t for t in _compact_tokens(old_hint) if len(t) >= 3]
    return bool(tokens) and any(t in compact_target for t in tokens)


def apply_addendum_superseding(items: Sequence[Any]) -> Tuple[List[Any], SupersedingReport]:
    """
    Resolve addendum/clarification superseding over a sequence of requirement items
    (`AutoAnnotatedRequirement` instances or v1.3 requirement dicts) in place.

    Returns:
        `(items_list, SupersedingReport)` where superseded prior items have
        `lifecycle_status="SUPERSEDED"` (or `"CANCELLED"`) and `superseded_by=<addendum_id>`,
        while effective items remain `lifecycle_status="ACTIVE"`.
    """
    items_list = list(items)
    for item in items_list:
        if not _get_attr(item, "lifecycle_status", ""):
            _set_attr(item, "lifecycle_status", "ACTIVE")
        if _get_attr(item, "superseded_by", None) is None:
            _set_attr(item, "superseded_by", "")
        if _get_attr(item, "supersedes_ids", None) is None:
            _set_attr(item, "supersedes_ids", [])
        if _get_attr(item, "superseding_note", None) is None:
            _set_attr(item, "superseding_note", "")

    base_indices: List[int] = []
    addendum_entries: List[Tuple[int, int, int]] = []  # (round_no, original_pos, item_idx)
    for idx, item in enumerate(items_list):
        if _is_addendum_item(item):
            doc_name = str(_get_attr(item, "doc_name", "") or "")
            sec = str(_get_attr(item, "Section", "") or _get_attr(item, "section", "") or "")
            req = str(_get_attr(item, "Requirement", "") or _get_attr(item, "requirement", "") or "")
            rnd = _extract_addendum_round(doc_name, sec, req)
            addendum_entries.append((rnd, idx, idx))
        else:
            base_indices.append(idx)

    if not addendum_entries:
        report = SupersedingReport(
            total_items=len(items_list),
            active_count=len(items_list),
        )
        return items_list, report

    # Sort addenda by round number (keeping original order for equal round numbers)
    addendum_entries.sort(key=lambda tup: (tup[0], tup[1]))

    active_pool: List[int] = list(base_indices)
    links: List[Dict[str, Any]] = []
    unmatched_count = 0

    for _, _, add_idx in addendum_entries:
        add_item = items_list[add_idx]
        add_id = str(_get_attr(add_item, "ID", "") or _get_attr(add_item, "id", "") or f"ADD_{add_idx}")
        add_sec = str(_get_attr(add_item, "Section", "") or _get_attr(add_item, "section", "") or "")
        add_req = str(_get_attr(add_item, "Requirement", "") or _get_attr(add_item, "requirement", "") or "")

        directive = parse_addendum_directive(add_req, section=add_sec)
        if directive.action in ("NONE", "SUPPLEMENT"):
            _set_attr(add_item, "lifecycle_status", "ACTIVE")
            if directive.action == "SUPPLEMENT":
                _set_attr(add_item, "superseding_note", "补遗新增条款")
            active_pool.append(add_idx)
            continue

        # Score all currently ACTIVE prior items in `active_pool`
        scored_candidates: List[Tuple[float, int, Dict[str, bool]]] = []
        for cand_idx in active_pool:
            cand = items_list[cand_idx]
            if _get_attr(cand, "lifecycle_status", "ACTIVE") != "ACTIVE":
                continue
            cand_sec = str(_get_attr(cand, "Section", "") or _get_attr(cand, "section", "") or "")
            cand_req = str(_get_attr(cand, "Requirement", "") or _get_attr(cand, "requirement", "") or "")
            cand_raw = str(_get_attr(cand, "raw_text", "") or "")
            cand_full = f"{cand_sec} {cand_req} {cand_raw}"

            cand_clause_nos = _extract_clause_numbers(cand_full)
            cand_chapter = _extract_chapter(cand_full)
            cand_topics = _extract_topics(cand_full)

            clause_match = bool(set(directive.target_clause_nos) & set(cand_clause_nos))
            shared_topics = sorted(set(directive.target_topics) & set(cand_topics))
            topic_match = bool(shared_topics)
            singleton_topic_match = any(t in _SINGLETON_TOPICS for t in shared_topics)
            old_hint_match = _old_hint_matches_target(directive.old_value_hint, cand_full)
            chapter_conflict = bool(directive.target_chapter and cand_chapter and directive.target_chapter != cand_chapter)
            chapter_match = bool(directive.target_chapter and cand_chapter and directive.target_chapter == cand_chapter)

            if chapter_conflict:
                continue

            score = 0.0
            if clause_match:
                score += 4.0
            if topic_match:
                score += 3.0 * len(shared_topics)
            if old_hint_match:
                score += 3.0
            if chapter_match:
                score += 1.0

            if score > 0.0:
                scored_candidates.append(
                    (
                        score,
                        cand_idx,
                        {
                            "clause_match": clause_match,
                            "topic_match": topic_match,
                            "singleton_topic_match": singleton_topic_match,
                            "old_hint_match": old_hint_match,
                            "chapter_match": chapter_match,
                        },
                    )
                )

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        matched_indices: List[int] = []

        if scored_candidates:
            top_score, top_idx, top_flags = scored_candidates[0]
            second_score = scored_candidates[1][0] if len(scored_candidates) > 1 else -1.0

            # High-confidence unique match or strong dual-anchor match
            is_dual_anchor = (
                (top_flags["clause_match"] and (top_flags["topic_match"] or top_flags["old_hint_match"] or top_flags["chapter_match"]))
                or (top_flags["topic_match"] and top_flags["old_hint_match"])
            )
            is_unique_clause_no = top_flags["clause_match"] and top_score > second_score
            is_unique_singleton_topic = top_flags["singleton_topic_match"] and second_score < top_score

            if is_dual_anchor:
                # If multiple prior clauses share the exact same top dual-anchor score (e.g. decomposed sub-items of the same clause),
                # only supersede them when clause_match is True and they share the exact clause number; otherwise require unique winner.
                if top_score > second_score:
                    matched_indices = [top_idx]
                elif top_flags["clause_match"] and top_flags["topic_match"]:
                    matched_indices = [
                        c_idx for sc, c_idx, fl in scored_candidates
                        if sc == top_score and fl["clause_match"] and fl["topic_match"]
                    ]
            elif is_unique_clause_no or is_unique_singleton_topic:
                matched_indices = [top_idx]

        if not matched_indices:
            unmatched_count += 1
            _set_attr(add_item, "lifecycle_status", "ACTIVE")
            _set_attr(
                add_item,
                "superseding_note",
                f"补遗{directive.action}指令未唯一锚定原条款，保留原条款并交人工复核",
            )
            active_pool.append(add_idx)
            continue

        superseded_ids: List[str] = []
        new_prior_status = "CANCELLED" if directive.action == "DELETE" else "SUPERSEDED"
        for m_idx in matched_indices:
            prior = items_list[m_idx]
            prior_id = str(_get_attr(prior, "ID", "") or _get_attr(prior, "id", "") or f"ITEM_{m_idx}")
            _set_attr(prior, "lifecycle_status", new_prior_status)
            _set_attr(prior, "superseded_by", add_id)
            _set_attr(
                prior,
                "superseding_note",
                f"已被补遗条款 {add_id} {'取消/删除' if directive.action == 'DELETE' else '修改覆盖'}",
            )
            superseded_ids.append(prior_id)

        _set_attr(add_item, "supersedes_ids", superseded_ids)
        if directive.action == "DELETE":
            _set_attr(add_item, "lifecycle_status", "DIRECTIVE_ONLY")
            _set_attr(add_item, "superseding_note", f"已取消原条款: {', '.join(superseded_ids)}")
        else:
            _set_attr(add_item, "lifecycle_status", "ACTIVE")
            _set_attr(add_item, "superseding_note", f"已覆盖原条款: {', '.join(superseded_ids)}")
            # Keep raw_text intact for PDF highlight while updating effective Requirement if cleanly parsed
            if directive.new_requirement_text and directive.new_requirement_text != add_req:
                if not _get_attr(add_item, "raw_text", ""):
                    _set_attr(add_item, "raw_text", add_req)
                if isinstance(add_item, dict):
                    key = "Requirement" if "Requirement" in add_item else "requirement"
                    add_item[key] = directive.new_requirement_text
                else:
                    setattr(add_item, "Requirement", directive.new_requirement_text)
            active_pool.append(add_idx)

        links.append(
            {
                "addendum_id": add_id,
                "action": directive.action,
                "superseded_ids": superseded_ids,
                "topics": directive.target_topics,
                "clause_nos": directive.target_clause_nos,
            }
        )

    active_cnt = sum(1 for it in items_list if _get_attr(it, "lifecycle_status", "ACTIVE") == "ACTIVE")
    sup_cnt = sum(1 for it in items_list if _get_attr(it, "lifecycle_status", "") == "SUPERSEDED")
    can_cnt = sum(1 for it in items_list if _get_attr(it, "lifecycle_status", "") == "CANCELLED")
    dir_cnt = sum(1 for it in items_list if _get_attr(it, "lifecycle_status", "") == "DIRECTIVE_ONLY")

    report = SupersedingReport(
        total_items=len(items_list),
        active_count=active_cnt,
        superseded_count=sup_cnt,
        cancelled_count=can_cnt,
        directive_only_count=dir_cnt,
        unmatched_addenda_count=unmatched_count,
        links=links,
    )
    return items_list, report

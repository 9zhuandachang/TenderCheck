"""Real backend bridge for the TenderCheck Streamlit review workspace.

Connects the Streamlit UI (`tcui`) to the real compliance pipeline in `SystemCode/src`:
  1. Hybrid RAG retrieval (`s604_repaired` index & multi-volume PDF source mapping)
  2. LLM structured slot extraction & anti-hallucination grounding (`DeepSeekSlotExtractor`, `ground_extraction`)
  3. Project-level global context & canonical bid anchor hydration (`build_project_global_context`, `hydrate_with_global_context`)
  4. Symbolic compliance rule engine (`ComplianceRuleEngine`, rules R01-R09)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymupdf as fitz

from . import config, store

# Ensure SystemCode root is importable so `src.*` modules resolve cleanly
SYSTEMCODE_ROOT = config.SYSTEMCODE_DIR
if str(SYSTEMCODE_ROOT) not in sys.path:
    sys.path.insert(0, str(SYSTEMCODE_ROOT))

from src.extraction.evidence import ground_extraction  # noqa: E402
from src.extraction.global_context import (  # noqa: E402
    ProjectGlobalContext,
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

REPO_ROOT = SYSTEMCODE_ROOT.parent

# Canonical paths for the S604 full-359 dataset and index
S604_ANNOTATIONS_PATH = SYSTEMCODE_ROOT / "data" / "annotation" / "2022" / "11.11annotations.json"
S604_CASES_PATH = SYSTEMCODE_ROOT / "reports" / "rag_experiments" / "s604_full359_reasoning_inputs" / "cases.json"
S604_RAW_DIR = SYSTEMCODE_ROOT / "reports" / "rag_experiments" / "s604_full359_reasoning_deepseek"
S604_REPLAY_V3_DIR = SYSTEMCODE_ROOT / "reports" / "rag_experiments" / "s604_full359_replay_v3"
S604_DOC_SOURCES_PATH = SYSTEMCODE_ROOT / "data" / "rag_index" / "s604_repaired" / "document_sources.json"
S604_REVIEW_DATE = "2022-11-11"

SUB_VERDICT_LABELS: Dict[str, Tuple[str, str]] = {
    "COMPLIANT": ("✓ Compliant", "green"),
    "SCORED": ("★ Scored", "blue"),
    "CONFLICTING_EVIDENCE": ("⚠ Conflicting Evidence", "red"),
    "NON_COMPLIANT": ("✗ Non-compliant", "red"),
    "SEAL_SIGNATURE_CHECK": ("🔍 Seal & Signature Check", "orange"),
    "EXTERNAL_WEB_CHECK": ("🌐 External Web Check", "violet"),
    "PERFORMANCE_LINKAGE": ("👤 Performance Linkage", "orange"),
    "SUBJECTIVE_SCORING": ("📝 Subjective Scoring", "gray"),
    "ATTACHMENT_OR_FORMULA": ("📎 Attachment / Formula", "orange"),
}

OP_SYMBOL = {"<=": "≤", ">=": "≥", "==": "=", "<": "<", ">": ">"}


def is_s604_project(project: Optional[Dict[str, Any]]) -> bool:
    if not project:
        return False
    name = project.get("name") or ""
    return "S604" in name or "飞毕路" in name or "359" in name


def _find_s604_data_dir() -> Optional[Path]:
    data_extracted = REPO_ROOT / "data_extracted"
    if not data_extracted.exists():
        return None
    matches = list(data_extracted.rglob("*11.11-S604*"))
    return matches[0] if matches else None


def _load_doc_sources() -> Dict[str, Dict[str, Any]]:
    """Map S604 RAG `document_id` to resolved PDF path, short label, and page count."""
    if not S604_DOC_SOURCES_PATH.exists():
        return {}
    raw = json.loads(S604_DOC_SOURCES_PATH.read_text(encoding="utf-8"))
    s604_dir = _find_s604_data_dir()
    resolved: Dict[str, Dict[str, Any]] = {}
    page_cache: Dict[str, int] = {}
    for doc_id, raw_path in raw.items():
        p = Path(raw_path)
        if not p.exists() and s604_dir is not None:
            candidates = list(s604_dir.rglob(p.name))
            if candidates:
                p = candidates[0]
        path_str = str(p)
        if path_str not in page_cache:
            if p.exists():
                try:
                    with fitz.open(path_str) as pdf_doc:
                        page_cache[path_str] = len(pdf_doc)
                except Exception:
                    page_cache[path_str] = 1
            else:
                page_cache[path_str] = 1
        short_name = p.name.replace("S604皖南交旅融合（飞毕路美丽公路）项目", "")
        resolved[doc_id] = {
            "path": path_str,
            "label": short_name,
            "filename": p.name,
            "pages": page_cache[path_str],
            "exists": p.exists(),
        }
    return resolved


def list_project_document_catalog(project_id: int) -> List[Dict[str, Any]]:
    """Return the complete catalog of project documents (Tender, Addenda, Primary Bid, Supporting Attachments)."""
    project = store.get_project(project_id)
    db_docs = store.list_documents(project_id)
    catalog: List[Dict[str, Any]] = []
    seen_filenames = set()

    cat_map = {
        "tender": "Tender Document",
        "addendum": "Addendum / Clarification",
        "bid": "Primary Bid",
    }
    for d in db_docs:
        fname = d.get("filename") or d.get("label") or ""
        seen_filenames.add(fname)
        catalog.append(
            {
                "group": "Tender & Addenda" if d["category"] in ("tender", "addendum") else "Primary Bid",
                "type": cat_map.get(d["category"], d["category"].title()),
                "label": d.get("label") or fname,
                "filename": fname,
                "pages": int(d.get("pages") or 1),
                "status": d.get("parse_status") or "Parsed",
                "selected": bool(d.get("selected", 1)),
            }
        )

    if is_s604_project(project):
        doc_sources = _load_doc_sources()
        for doc_id in sorted(doc_sources.keys()):
            src = doc_sources[doc_id]
            fname = src["filename"]
            if fname in seen_filenames:
                continue
            seen_filenames.add(fname)
            is_primary_tech = "技术标" in fname or "施工组织" in fname
            catalog.append(
                {
                    "group": "Primary Bid" if is_primary_tech else "Supporting Attachment",
                    "type": "Technical Bid Volume" if is_primary_tech else "Bid Attachment / Certificate",
                    "label": src["label"],
                    "filename": fname,
                    "pages": int(src["pages"] or 1),
                    "status": "Parsed & Indexed" if src.get("exists") else "Indexed",
                    "selected": True,
                }
            )
    return catalog


def classify_sub_verdict(raw_verdict: str, tender_slots: Dict[str, Any], req_text: str) -> str:
    """Classify the fine-grained review category for UI filtering and human review routing."""
    if raw_verdict == "SCORED":
        return "SCORED"
    if raw_verdict == "COMPLIANT":
        return "COMPLIANT"
    if raw_verdict == "NON_COMPLIANT":
        return "NON_COMPLIANT"
    if raw_verdict == "CONFLICTING_EVIDENCE":
        return "CONFLICTING_EVIDENCE"
    if raw_verdict == "UNSUPPORTED_RULE":
        return "SUBJECTIVE_SCORING"

    ctype = tender_slots.get("constraint_type") or tender_slots.get("clause_type") or ""
    if tender_slots.get("requires_external_verification") or re.search(
        r"信用中国|裁判文书网|失信被执行人|行贿犯罪|不良行为|国家企业信用|政务网", req_text
    ):
        return "EXTERNAL_WEB_CHECK"
    if (
        tender_slots.get("requires_official_seal")
        or tender_slots.get("requires_signature")
        or tender_slots.get("required_seal_parties")
        or re.search(r"盖章|公章|签字|签名|签章|手写|宋体|暗标|页眉|页脚|字迹清晰", req_text)
    ):
        return "SEAL_SIGNATURE_CHECK"
    if ctype == "HISTORICAL_PERFORMANCE" or re.search(r"业绩|交工验收", req_text):
        return "PERFORMANCE_LINKAGE"
    return "ATTACHMENT_OR_FORMULA"


def map_ui_verdict(raw_verdict: str) -> str:
    """Map 6 backend verdicts to the 3 top-level UI verdicts while preserving positive rate (263/359)."""
    if raw_verdict in ("COMPLIANT", "SCORED"):
        return config.COMPLIANT
    if raw_verdict == "NON_COMPLIANT":
        return config.NON_COMPLIANT
    return config.INSUFFICIENT


def build_constraint_dict(tender_slots: Dict[str, Any], req_text: str, category: str) -> Dict[str, Any]:
    """Convert backend `tender_slots` into a UI-compatible `constraint` dict."""
    ctype = tender_slots.get("constraint_type") or tender_slots.get("clause_type") or "UNSUPPORTED"
    target_field = tender_slots.get("target_field") or ctype
    if ctype == "NUMERIC_BOUND" and tender_slots.get("expected_numeric") is not None:
        op = tender_slots.get("operator") or "<="
        if op not in ("<=", ">=", "=="):
            op = "<=" if "<" in op else (">=" if ">" in op else "==")
        return {
            "type": "numeric",
            "clause_type": ctype,
            "field": target_field,
            "operator": op,
            "value": float(tender_slots["expected_numeric"]),
            "unit": tender_slots.get("expected_unit") or "",
            "tender_slots": tender_slots,
        }
    return {
        "type": "document" if category == "Document" else "presence",
        "clause_type": ctype,
        "field": target_field,
        "tender_slots": tender_slots,
    }


def _build_fields_comparison(
    ext_dict: Dict[str, Any],
    rule_dict: Dict[str, Any],
    ui_verdict: str,
    sub_verdict: str,
) -> List[Dict[str, Any]]:
    """Build structured field-by-field comparison rows (`Field`, `Tender requirement`, `Bid response`, `Result`)."""
    t = ext_dict.get("tender_slots") or {}
    b = ext_dict.get("bid_slots") or {}
    ctype = t.get("constraint_type") or t.get("clause_type") or "UNSUPPORTED"
    rows: List[Dict[str, Any]] = []

    if ctype == "NUMERIC_BOUND":
        op = OP_SYMBOL.get(t.get("operator") or "==", t.get("operator") or "=")
        req_val = (
            f"{op} {t['expected_numeric']:g} {t.get('expected_unit') or ''}".strip()
            if t.get("expected_numeric") is not None
            else (t.get("expected_text") or "详见招标文件基准")
        )
        act_val = (
            f"{b['actual_numeric']:g} {b.get('actual_unit') or ''}".strip()
            if b.get("actual_numeric") is not None
            else (b.get("actual_text") or "未在Top-K检索段落中检出")
        )
        rows.append(
            {
                "field": f"数值边界 ({t.get('target_field') or 'Numeric Bound'})",
                "required": req_val,
                "actual": act_val,
                "result": ui_verdict,
            }
        )
    elif ctype == "QUALIFICATION_LEVEL":
        req_parts = [p for p in [t.get("expected_text"), t.get("required_domain")] if p]
        act_parts = [p for p in [b.get("actual_text"), b.get("actual_domain")] if p]
        if b.get("certificate_holder"):
            act_parts.append(f"持证主体: {b['certificate_holder']}")
        eff_date = b.get("extension_valid_to") or b.get("valid_to")
        if eff_date:
            act_parts.append(f"有效期至: {eff_date}")
        rows.append(
            {
                "field": f"资质与证书等级 ({t.get('subject_role') or 'bidder'})",
                "required": " · ".join(req_parts) or "满足招标资质等级要求",
                "actual": " · ".join(act_parts) if act_parts else "未检出有效证书扫描件或主体锚点",
                "result": ui_verdict,
            }
        )
    elif ctype == "STAFFING_COUNT_AND_QUALIFICATION":
        role_name = t.get("required_role") or t.get("subject_role") or "岗位人员"
        req_desc = f"{role_name} ≥ {t.get('min_headcount') or t.get('required_count') or 1}人"
        if t.get("expected_text"):
            req_desc += f" ({t['expected_text']})"
        if t.get("min_experience_years"):
            req_desc += f" · 经验≥{t['min_experience_years']:g}年"
        recs = b.get("personnel_records") or []
        if recs:
            names = [
                f"{r.get('name', '未知')}({r.get('qualification') or r.get('role') or '已配'}"
                + (f", {r['experience_years']:g}年" if r.get("experience_years") is not None else "")
                + ")"
                for r in recs[:6]
            ]
            act_desc = f"实配 {len(recs)} 人: " + "、".join(names)
        else:
            act_desc = "未检出完整人员配备明细"
        rows.append(
            {
                "field": f"人员配置 ({role_name})",
                "required": req_desc,
                "actual": act_desc,
                "result": ui_verdict,
            }
        )
    elif ctype == "HISTORICAL_PERFORMANCE":
        req_desc = f"业绩数量 ≥ {t.get('min_project_count') or 1}项"
        if t.get("required_project_type"):
            req_desc += f" · 类型: {t['required_project_type']}"
        if t.get("expected_numeric"):
            val = t.get("expected_numeric")
            req_desc += f" · 规模 ≥ {val:g}{t.get('expected_unit') or ''}"
        projs = b.get("performance_records") or []
        if projs:
            p_desc = "；".join(
                f"{p.get('project_name', '项目')} (合同额:{p.get('contract_amount') or '—'}, "
                f"里程:{p.get('mileage_km') or '—'}km, 角色:{p.get('manager_name') or p.get('subject_role') or '投标人'})"
                for p in projs[:3]
            )
        else:
            p_desc = "未检出符合全部证明链要求的历史业绩"
        rows.append(
            {
                "field": f"历史业绩核验 ({t.get('subject_role') or 'bidder'})",
                "required": req_desc,
                "actual": p_desc,
                "result": ui_verdict,
            }
        )
    elif ctype == "ENTITY_CONSISTENCY":
        rows.append(
            {
                "field": f"跨文档一致性 ({t.get('target_field') or 'Consistency'})",
                "required": "主次文档实体/大写金额完全一致",
                "actual": (
                    f"主项: {b.get('entity_primary') or b.get('actual_numeric') or '—'} | "
                    f"次项: {b.get('entity_secondary') or b.get('upper_amount_text') or '—'}"
                ),
                "result": ui_verdict,
            }
        )
    elif ctype == "DOCUMENT_PRESENCE_AND_SEAL":
        req_parts = ["提供对应实质性文件正文"]
        if t.get("requires_official_seal"):
            req_parts.append("加盖公章(需人工视觉核验)")
        if t.get("requires_signature"):
            req_parts.append("法定代表人/授权人签字(需人工视觉核验)")
        if t.get("required_seal_parties"):
            req_parts.append("签章方: " + "、".join(t["required_seal_parties"]))
        act_parts = []
        if b.get("document_present"):
            act_parts.append("正文已提供")
        if sub_verdict == "SEAL_SIGNATURE_CHECK":
            act_parts.append("纯文本不作红章/手写笔迹假阳性放行，请核对右侧PDF原件")
        elif not act_parts:
            act_parts.append("未在检索段落中定位到完整单据")
        rows.append(
            {
                "field": "单据存在性与签章版式",
                "required": " · ".join(req_parts),
                "actual": " · ".join(act_parts),
                "result": ui_verdict,
            }
        )
    elif ctype == "PROHIBITION_AND_COMMITMENT":
        if sub_verdict == "CONFLICTING_EVIDENCE":
            pairs = b.get("conflict_pairs") or rule_dict.get("conflict_pairs") or []
            if pairs:
                cp = pairs[0]
                left_side = cp.get("left") or {}
                right_side = cp.get("right") or {}
                q_a = left_side.get("quote") or cp.get("quote_a") or ""
                q_b = right_side.get("quote") or cp.get("quote_b") or ""
                eid_a = left_side.get("evidence_id") or cp.get("evidence_id_a") or "A"
                eid_b = right_side.get("evidence_id") or cp.get("evidence_id_b") or "B"
                act_str = f"【冲突A ({eid_a})】{q_a[:45]}... ⚡ 【冲突B ({eid_b})】{q_b[:45]}..."
            else:
                act_str = "标书内部存在自相矛盾声明"
        elif sub_verdict == "EXTERNAL_WEB_CHECK":
            act_str = "涉及外部政府网站失信/行贿/不良行为记录，按防自证规范需联机核查"
        elif b.get("commitment_fulfilled") and not b.get("prohibition_violated"):
            refs = rule_dict.get("evidence_references") or b.get("evidence_references") or []
            q_preview = refs[0].get("quote", "")[:55] + "..." if refs else "已响应承诺且无违规情形"
            act_str = f"已承诺/响应：{q_preview}"
        else:
            act_str = "未检出专项响应或需人工复核"
        rows.append(
            {
                "field": "实质性响应与禁止性承诺",
                "required": "响应招标条款且无禁止性违规/前后矛盾",
                "actual": act_str,
                "result": ui_verdict,
            }
        )
    elif ctype == "OBJECTIVE_SCORING":
        score_str = (
            f"得分: {rule_dict['awarded_score']:g} 分"
            if rule_dict.get("awarded_score") is not None
            else "待核对计分条件"
        )
        rows.append(
            {
                "field": "客观量化计分 (Objective Scoring)",
                "required": f"最高 {t.get('max_score') or '—'} 分",
                "actual": f"{score_str} ({rule_dict.get('symbolic_trace') or ''})",
                "result": ui_verdict,
            }
        )
    else:
        rows.append(
            {
                "field": "施工组织设计/主观评审项",
                "required": "由评标委员会专家按评审细则综合打分",
                "actual": "已定位技术标施工组织设计相关章节，请专家审阅打分",
                "result": ui_verdict,
            }
        )
    return rows


def _build_evidence_list(
    ext_dict: Dict[str, Any],
    rule_dict: Dict[str, Any],
    passages: List[Dict[str, Any]],
    doc_sources: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build up to 5 deduplicated evidence page items with multi-document PDF resolution and Chinese quote snippets."""
    evidence: List[Dict[str, Any]] = []
    seen_doc_pages = set()
    seen_page_text_sigs = set()
    passage_by_id = {p.get("evidence_id"): p for p in passages if p.get("evidence_id")}

    def _add_ev(doc_id: Optional[str], page: Optional[int], snippet: str, role_label: str, eid: str = "") -> None:
        if not page:
            return
        p_meta = passage_by_id.get(eid, {})
        resolved_doc_id = doc_id or p_meta.get("document_id") or ""
        src = doc_sources.get(resolved_doc_id, {})
        doc_key = src.get("path") or resolved_doc_id
        page_int = int(page)

        # 1. Deduplicate identical (document, page) so the same page is never added twice
        dp_key = (doc_key, page_int)
        if dp_key in seen_doc_pages:
            return

        page_passages = [
            p.get("text", "").strip()
            for p in passages
            if (p.get("document_id") == resolved_doc_id or not resolved_doc_id)
            and int(p.get("page_start") or 0) == page_int
            and p.get("text")
        ]
        if p_meta.get("text") and p_meta["text"].strip() not in page_passages:
            page_passages.insert(0, p_meta["text"].strip())
        rag_full = "\n".join(page_passages) if page_passages else snippet.strip()

        # 2. Deduplicate near-identical attachment copies sharing the same page number and text (e.g. sub02 vs sub03)
        norm_sig = re.sub(r"\s+", "", (rag_full or snippet))[:60]
        pt_key = (page_int, norm_sig)
        if norm_sig and pt_key in seen_page_text_sigs:
            return

        seen_doc_pages.add(dp_key)
        if norm_sig:
            seen_page_text_sigs.add(pt_key)

        evidence.append(
            {
                "page": page_int,
                "snippet": snippet.strip(),
                "rag_text": rag_full,
                "role": role_label,
                "evidence_id": eid,
                "document_id": resolved_doc_id,
                "doc_path": src.get("path"),
                "doc_label": src.get("label") or resolved_doc_id or "Bid",
                "doc_pages": src.get("pages"),
            }
        )

    # 1. Conflict pairs first (for T0056 so both conflicting pages are front and center)
    for cp in rule_dict.get("conflict_pairs") or ext_dict.get("bid_slots", {}).get("conflict_pairs") or []:
        left = cp.get("left") or {}
        right = cp.get("right") or {}
        eid_a = left.get("evidence_id") or cp.get("evidence_id_a") or ""
        eid_b = right.get("evidence_id") or cp.get("evidence_id_b") or ""
        pa = passage_by_id.get(eid_a, {})
        pb = passage_by_id.get(eid_b, {})
        _add_ev(
            left.get("document_id") or pa.get("document_id"),
            left.get("page_start") or cp.get("page_a") or pa.get("page_start"),
            left.get("quote") or cp.get("quote_a") or pa.get("text", ""),
            f"⚡ 冲突证据 A ({eid_a})",
            eid_a,
        )
        _add_ev(
            right.get("document_id") or pb.get("document_id"),
            right.get("page_start") or cp.get("page_b") or pb.get("page_start"),
            right.get("quote") or cp.get("quote_b") or pb.get("text", ""),
            f"⚡ 冲突证据 B ({eid_b})",
            eid_b,
        )

    # 2. Grounded evidence_references from rule evaluation / extraction
    refs = rule_dict.get("evidence_references") or ext_dict.get("bid_slots", {}).get("evidence_references") or []
    for ref in refs:
        eid = ref.get("evidence_id") or ""
        p_meta = passage_by_id.get(eid, {})
        doc_id = ref.get("document_id") or p_meta.get("document_id")
        page = ref.get("page_start") or p_meta.get("page_start")
        quote = ref.get("quote") or p_meta.get("text", "")
        origin_tag = "全局权威锚点" if eid.startswith("G") else "核验引文"
        _add_ev(doc_id, page, quote, f"★ {origin_tag} · {ref.get('field', '')} ({eid})", eid)

    # 3. Subject bindings role anchors
    for sb in rule_dict.get("subject_bindings") or ext_dict.get("bid_slots", {}).get("subject_bindings") or []:
        r_eid = sb.get("role_evidence_id") or ""
        if r_eid and r_eid in passage_by_id:
            p_meta = passage_by_id[r_eid]
            _add_ev(
                p_meta.get("document_id"),
                p_meta.get("page_start"),
                sb.get("role_quote") or p_meta.get("text", ""),
                f"🔗 主体绑定 ({sb.get('subject_name', '')} · {sb.get('role', '')})",
                r_eid,
            )

    # 4. Top-K retrieved candidate passages (so reviewers can visually check stamps/signatures even when abstained)
    for p in passages[:5]:
        eid = p.get("evidence_id") or ""
        score = p.get("retrieval_score") or p.get("score")
        score_str = f"score {float(score):.2f}" if isinstance(score, (int, float)) else "retrieved"
        _add_ev(
            p.get("document_id"),
            p.get("page_start"),
            p.get("text", ""),
            f"检索候选段落 · {eid} ({score_str})",
            eid,
        )

    return evidence[:5]


def format_backend_result(
    req_text: str,
    category: str,
    ext: StructuredExtractionResult,
    rule_res: Any,
    passages: List[Dict[str, Any]],
    doc_sources: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Package `StructuredExtractionResult` + `RuleEvalResult` into the `results` table format expected by `service.py`."""
    ext_dict = ext.to_dict()
    rule_dict = rule_res.to_dict()
    raw_verdict = rule_res.verdict
    ui_verdict = map_ui_verdict(raw_verdict)
    sub_verdict = classify_sub_verdict(raw_verdict, ext_dict.get("tender_slots") or {}, req_text)
    sub_label, sub_color = SUB_VERDICT_LABELS.get(sub_verdict, (raw_verdict, "gray"))

    fields = _build_fields_comparison(ext_dict, rule_dict, ui_verdict, sub_verdict)
    evidence = _build_evidence_list(ext_dict, rule_dict, passages, doc_sources)
    constraint = build_constraint_dict(ext_dict.get("tender_slots") or {}, req_text, category)

    hits = []
    for p in passages[:6]:
        eid = p.get("evidence_id") or ""
        src = doc_sources.get(p.get("document_id") or "", {})
        hits.append(
            {
                "evidence_id": eid,
                "page": p.get("page_start") or 1,
                "doc_label": src.get("label") or p.get("document_id") or "",
                "score": float(p.get("retrieval_score") or p.get("score") or 1.0),
                "is_global": eid.startswith("G"),
            }
        )

    explanation_parts = [f"【{sub_label}】"]
    if rule_res.awarded_score is not None:
        explanation_parts.append(f"量化得分：{rule_res.awarded_score:g} 分。")
    if rule_res.explanation:
        explanation_parts.append(rule_res.explanation)
    if ext.extraction_issues:
        explanation_parts.append("防幻觉/接地拦截：" + "；".join(ext.extraction_issues))

    t_dict = ext_dict.get("tender_slots") or {}
    ctype = t_dict.get("constraint_type") or "UNSUPPORTED"
    chain = {
        "is_real_backend": True,
        "raw_verdict": raw_verdict,
        "sub_verdict": sub_verdict,
        "sub_verdict_label": sub_label,
        "sub_verdict_color": sub_color,
        "awarded_score": rule_res.awarded_score,
        "classify": {
            "category": category,
            "clause_type": ctype,
            "score": None,
            "method": "RoBERTa Clause Classifier + LLM Semantic Schema (v11-arch)",
        },
        "retrieve": {
            "method": "Hybrid RAG (BGE-M3 + BM25 + Category Rerank) + ProjectGlobalContext",
            "keywords": [
                k
                for k in [
                    ctype,
                    t_dict.get("target_field"),
                    t_dict.get("required_role") or t_dict.get("subject_role"),
                    t_dict.get("expected_text"),
                ]
                if k
            ],
            "hits": hits,
        },
        "extract": {
            "method": f"{ext.extractor_backend} + ground_extraction + hydrate_with_global_context",
            "required": constraint,
            "actual": {
                "value": ext.bid_slots.actual_numeric,
                "unit": ext.bid_slots.actual_unit or ext.tender_slots.expected_unit or "",
                "text": ext.bid_slots.actual_text,
                "page": evidence[0]["page"] if evidence else None,
            },
            "issues": ext.extraction_issues,
        },
        "rule": {
            "rule_id": rule_res.triggered_rule_id,
            "rule_version": "ComplianceRuleEngine-v3 (R01-R09)",
            "trace": rule_res.symbolic_trace or raw_verdict,
        },
        "backend_state": {
            "extraction": ext_dict,
            "evidence_context": passages,
        },
    }

    return {
        "verdict": ui_verdict,
        "rule_id": rule_res.triggered_rule_id,
        "explanation": " ".join(explanation_parts),
        "fields": fields,
        "evidence": evidence,
        "chain": chain,
        "constraint": constraint,
    }


def _load_raw_payloads() -> Dict[str, Tuple[Dict[str, Any], str]]:
    """Load saved raw DeepSeek responses from `s604_full359_reasoning_deepseek` if present."""
    manifest = S604_RAW_DIR / "api_calls.json"
    if not manifest.exists():
        return {}
    events = json.loads(manifest.read_text(encoding="utf-8"))
    payloads: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for index, event in enumerate(events, 1):
        cid = event["clause_id"]
        path = S604_RAW_DIR / f"raw_{cid}_{index:03d}.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            payload = json.loads(raw["choices"][0]["message"]["content"])
            payloads[cid] = (payload, raw.get("model") or "deepseek-chat")
    return payloads


def evaluate_all_s604_clauses(
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Run the real backend pipeline (`DeepSeekSlotExtractor` + `ProjectGlobalContext` + `ComplianceRuleEngine`) on all 359 S604 clauses."""
    bundle = json.loads(S604_CASES_PATH.read_text(encoding="utf-8"))
    cases = bundle["cases"]
    doc_sources = _load_doc_sources()

    normalizer = DeepSeekSlotExtractor(cache_only=True, review_reference_date=S604_REVIEW_DATE)
    rule_engine = ComplianceRuleEngine()
    global_ctx: ProjectGlobalContext = build_project_global_context(cases)
    payloads = _load_raw_payloads()

    cached_exts: Dict[str, Dict[str, Any]] = {}
    ext_file = S604_REPLAY_V3_DIR / "extractions.json"
    if ext_file.exists():
        for item in json.loads(ext_file.read_text(encoding="utf-8")):
            cached_exts[item["clause_id"]] = item

    total = len(cases)
    outputs: Dict[str, Dict[str, Any]] = {}

    for idx, case in enumerate(cases, start=1):
        cid = case["clause_id"]
        req_text = case["requirement"]
        category = case["category"]
        ev_ctx = case["evidence_context"]
        evidence_str = ev_ctx if isinstance(ev_ctx, str) else json.dumps(ev_ctx, ensure_ascii=False)
        initial_passages = json.loads(evidence_str) if isinstance(ev_ctx, str) else ev_ctx

        if progress and (idx == 1 or idx % 25 == 0 or idx == total):
            progress(f"正在通过全局上下文与符号规则引擎审查条款 {cid} ({idx}/{total})", idx, total)

        if cid in payloads:
            payload, model = payloads[cid]
            tender = TenderConstraintSlots.from_dict(payload["tender_slots"])
            bid = BidEvidenceSlots.from_dict(payload["bid_slots"])
            ext = StructuredExtractionResult(
                cid, req_text, category, tender, bid, extractor_backend=model
            )
            ext = normalizer._inject_reference_date(ext, S604_REVIEW_DATE)
            ext = normalizer._normalize_llm_extraction(ext, initial_passages)
            ext = ground_extraction(ext, evidence_str)
            ext, hydrated_ctx_str = hydrate_with_global_context(
                ext, evidence_str, global_ctx, normalizer=normalizer
            )
            passages = json.loads(hydrated_ctx_str)
        elif cid in cached_exts:
            ext = StructuredExtractionResult.from_dict(cached_exts[cid])
            ext = normalizer._inject_reference_date(ext, S604_REVIEW_DATE)
            ext, hydrated_ctx_str = hydrate_with_global_context(
                ext, evidence_str, global_ctx, normalizer=normalizer
            )
            passages = json.loads(hydrated_ctx_str)
        else:
            ext = normalizer.extract(cid, req_text, category, evidence_str)
            ext, hydrated_ctx_str = hydrate_with_global_context(
                ext, evidence_str, global_ctx, normalizer=normalizer
            )
            passages = json.loads(hydrated_ctx_str)

        rule_res = rule_engine.evaluate(ext)
        outputs[cid] = format_backend_result(req_text, category, ext, rule_res, passages, doc_sources)

    return outputs


def rereview_real_backend_result(
    req: Dict[str, Any],
    existing_result: Dict[str, Any],
    corrected_constraint: Optional[Dict[str, Any]] = None,
    actual_value: Optional[float] = None,
    actual_unit: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Re-evaluate a single clause using the real `ComplianceRuleEngine` after a user edits values in the UI."""
    chain = existing_result.get("chain") or {}
    if not chain.get("is_real_backend"):
        return None
    backend_state = chain.get("backend_state") or {}
    ext_dict = backend_state.get("extraction")
    passages = backend_state.get("evidence_context") or []
    if not ext_dict:
        return None

    ext = StructuredExtractionResult.from_dict(ext_dict)
    ext.requirement_text = req["text"]
    ext.category = req["category"]

    c = corrected_constraint or req.get("constraint") or {}
    if c.get("type") == "numeric":
        ext.tender_slots.constraint_type = "NUMERIC_BOUND"
        if c.get("operator"):
            ext.tender_slots.operator = c["operator"]
        if c.get("value") is not None:
            ext.tender_slots.expected_numeric = float(c["value"])
        if c.get("unit") is not None:
            ext.tender_slots.expected_unit = c["unit"]
        if c.get("field"):
            ext.tender_slots.target_field = c["field"]

    if actual_value is not None or actual_unit is not None:
        ext.bid_slots.evidence_found = True
        ext.bid_slots.actual_numeric = actual_value
        if actual_unit is not None:
            ext.bid_slots.actual_unit = actual_unit

    rule_engine = ComplianceRuleEngine()
    rule_res = rule_engine.evaluate(ext)
    doc_sources = _load_doc_sources()
    return format_backend_result(req["text"], req["category"], ext, rule_res, passages, doc_sources)


_AUTO_ANNOTATOR_SINGLETON: Optional[Any] = None


def _get_auto_annotator() -> Any:
    """Lazy-load and cache `AutoTenderAnnotator` (`BinaryRequirementFilter` + 8-class `roberta_wwm_ext`)."""
    global _AUTO_ANNOTATOR_SINGLETON
    if _AUTO_ANNOTATOR_SINGLETON is None:
        from src.extraction.auto_annotator import AutoTenderAnnotator

        bin_dir = SYSTEMCODE_ROOT / "models" / "binary_filter"
        roberta_dir = SYSTEMCODE_ROOT / "models" / "roberta_wwm_ext"
        _AUTO_ANNOTATOR_SINGLETON = AutoTenderAnnotator(
            binary_model_dir=bin_dir,
            eight_class_model_dir=roberta_dir,
            chapter_aware=True,
        )
    return _AUTO_ANNOTATOR_SINGLETON


def is_chinese_pdf(pdf_path: Path) -> bool:
    """Detect whether a PDF document contains Chinese engineering text (vs. synthetic English demo PDFs)."""
    if not pdf_path.exists():
        return False
    try:
        with fitz.open(str(pdf_path)) as doc:
            sample_pages = min(len(doc), 8)
            cjk_count = 0
            for idx in range(sample_pages):
                txt = doc[idx].get_text("text") or ""
                cjk_count += len(re.findall(r"[\u4e00-\u9fff]", txt))
                if cjk_count >= 25:
                    return True
    except Exception:
        return False
    return False


def extract_chinese_project_requirements(
    project_id: int,
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> Optional[Dict[str, Any]]:
    """
    If the project's tender PDFs are Chinese documents, run the end-to-end local
    `AutoTenderAnnotator` (`segment_tender_pdf` -> `BinaryRequirementFilter` ->
    8-class `roberta_wwm_ext`/`tfidf_svm` classifier), persist `auto_annotations.json`,
    and populate the `requirements` table. Returns `None` if documents are English demo PDFs.
    """
    tenders = store.list_documents(project_id, "tender", selected_only=True)
    addenda = store.list_documents(project_id, "addendum", selected_only=True)
    if not tenders:
        return None

    if not any(is_chinese_pdf(Path(d["path"])) for d in tenders):
        return None

    store.execute("DELETE FROM requirements WHERE project_id=? AND origin IN ('extracted','addendum')", (project_id,))
    store.execute("DELETE FROM drawings WHERE project_id=? AND source='extracted'", (project_id,))

    annotator = _get_auto_annotator()
    tender_paths = [Path(d["path"]) for d in tenders]
    addendum_paths = [Path(d["path"]) for d in addenda]
    doc_by_name = {Path(d["path"]).name: d for d in (tenders + addenda)}
    default_tender_doc = tenders[0]

    items, stats = annotator.annotate_pdfs(tender_paths, addendum_paths, progress=progress)

    # Persist standard v1.3 auto_annotations.json in the project folder
    proj_folder = store.project_dir(project_id)
    proj_folder.mkdir(parents=True, exist_ok=True)
    (proj_folder / "auto_annotations.json").write_text(
        json.dumps([x.to_v13_dict() for x in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = {"requirements": 0, "amendments": 0, "drawings": 0, "stats": stats}
    now_ts = store.now()
    with store.connect() as con:
        for item in items:
            matched_doc = doc_by_name.get(item.doc_name, default_tender_doc)
            is_addendum = matched_doc["category"] == "addendum"
            category = item.Requirement_Type if item.Requirement_Type in config.CATEGORIES else "Document"
            constraint = {
                "type": "document" if category == "Document" else "presence",
                "clause_type": category,
                "field": category,
            }
            original_meta = {
                "text": item.Requirement,
                "raw_text": item.raw_text,
                "category": category,
                "constraint": constraint,
                "page": item.Page,
                "section": item.Section,
                "binary_prob": item.binary_prob,
                "category_confidence": item.category_confidence,
            }
            con.execute(
                """
                INSERT INTO requirements (
                    project_id, clause_no, text, category, category_score,
                    constraint_json, source_doc_id, page, origin, original_json,
                    amended_by_doc_id, amendment_page, amendment_text, amendment_status,
                    applicable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    project_id,
                    item.ID,
                    item.Requirement,
                    category,
                    float(item.category_confidence),
                    json.dumps(constraint, ensure_ascii=False),
                    matched_doc["id"],
                    int(item.Page),
                    "addendum" if is_addendum else "extracted",
                    json.dumps(original_meta, ensure_ascii=False),
                    matched_doc["id"] if is_addendum else None,
                    int(item.Page) if is_addendum else None,
                    item.Requirement if is_addendum else None,
                    "accepted" if is_addendum else None,
                    now_ts,
                ),
            )
            if is_addendum:
                counts["amendments"] += 1
            else:
                counts["requirements"] += 1

    store.update("projects", project_id, {"req_confirmed_at": now_ts})
    store.log_action(project_id, "extract_chinese_requirements", payload=counts)
    return counts


def evaluate_chinese_project_clauses(
    project_id: int,
    reqs: List[Dict[str, Any]],
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> Optional[Dict[int, Dict[str, Any]]]:
    """
    Run the end-to-end Chinese RAG + GlobalContext + DeepSeekSlotExtractor +
    ComplianceRuleEngine pipeline on any Chinese project's requirements and bid PDFs.
    Returns `None` if the requirements are English demo clauses.
    """
    if not reqs:
        return {}
    cjk_clauses = sum(1 for r in reqs[:20] if re.search(r"[\u4e00-\u9fff]", str(r.get("text") or "")))
    if cjk_clauses == 0:
        return None

    from src.extraction.chinese_pipeline import (
        FastChineseBidRetriever,
        build_global_context_with_full_bid_scan,
        parse_bid_pdfs_for_rag,
    )

    bid_docs = store.list_documents(project_id, "bid", selected_only=True)
    if not bid_docs:
        return None

    child_chunks, parent_map, doc_sources = parse_bid_pdfs_for_rag(bid_docs, progress=progress)
    retriever = FastChineseBidRetriever(child_chunks, parent_map, doc_sources)

    cases: List[Dict[str, Any]] = []
    passages_by_req_id: Dict[int, List[Dict[str, Any]]] = {}
    for r in reqs:
        rid = int(r["id"])
        cid = str(r.get("clause_no") or f"T{rid:04d}")
        r_text = str(r.get("text") or "")
        r_cat = str(r.get("category") or "Document")
        top_passages = retriever.retrieve_passages(r_text, r_cat, top_k=5)
        passages_by_req_id[rid] = top_passages
        cases.append(
            {
                "clause_id": cid,
                "requirement": r_text,
                "category": r_cat,
                "evidence_context": json.dumps(top_passages, ensure_ascii=False),
            }
        )

    global_ctx = build_global_context_with_full_bid_scan(cases, parent_map, doc_sources)
    deepseek_extractor = DeepSeekSlotExtractor(cache_only=False)
    rule_engine = ComplianceRuleEngine()

    total = len(reqs)
    outputs_by_req_id: Dict[int, Dict[str, Any]] = {}
    for idx, r in enumerate(reqs, start=1):
        rid = int(r["id"])
        cid = str(r.get("clause_no") or f"T{rid:04d}")
        r_text = str(r.get("text") or "")
        r_cat = str(r.get("category") or "Document")
        if progress and (idx == 1 or idx % 20 == 0 or idx == total):
            progress(f"正在检索投标证据并执行符号规则核验 {cid} ({idx}/{total})", idx, total)

        local_passages = [dict(p) for p in passages_by_req_id.get(rid, [])]
        ev_str = json.dumps(local_passages, ensure_ascii=False)
        ext = deepseek_extractor.extract(cid, r_text, r_cat, ev_str)
        ext, hydrated_str = hydrate_with_global_context(ext, ev_str, global_ctx, normalizer=deepseek_extractor)
        final_passages = json.loads(hydrated_str) if hydrated_str else local_passages

        rule_res = rule_engine.evaluate(ext)
        out = format_backend_result(r_text, r_cat, ext, rule_res, final_passages, doc_sources)
        outputs_by_req_id[rid] = out

    return outputs_by_req_id



S604_KNOWN_ISSUES = [
    {
        "clause_no": "T0056",
        "expected": config.INSUFFICIENT,
        "note": "标书内部真实自相矛盾：P0404《投标诚信承诺书》承诺项目经理无在建 vs P0380《投标函》声明王文年在G345任项目经理",
    },
    {
        "clause_no": "T0012",
        "expected": config.INSUFFICIENT,
        "note": "历史业绩项目经理不匹配：所附省道214业绩合同项目经理为韦秀刚，而非本项目拟派项目经理王文年",
    },
    {
        "clause_no": "T0013",
        "expected": config.INSUFFICIENT,
        "note": "项目经理个人业绩证明链主体不一致（韦秀刚 vs 王文年）",
    },
    {
        "clause_no": "T0014",
        "expected": config.INSUFFICIENT,
        "note": "项目经理业绩交工验收与任职主体待核验",
    },
    {
        "clause_no": "T0016",
        "expected": config.INSUFFICIENT,
        "note": "外部政务网核查项：国家企业信用信息公示系统严重违法失信企业名单（防自证拦截）",
    },
    {
        "clause_no": "T0017",
        "expected": config.INSUFFICIENT,
        "note": "外部政务网核查项：信用中国失信被执行人名单（防自证拦截）",
    },
    {
        "clause_no": "T0018",
        "expected": config.INSUFFICIENT,
        "note": "外部政务网核查项：中国裁判文书网行贿犯罪记录（防自证拦截）",
    },
    {
        "clause_no": "T0021",
        "expected": config.INSUFFICIENT,
        "note": "人工视觉查章项：法定代表人身份证明或授权委托书红章/手写签字原件核验",
    },
]


def _register_external_pdf(
    project_id: int,
    category: str,
    label: str,
    pdf_path: Path,
    version: int = 1,
) -> int:
    """Register an existing PDF file on disk into `documents` without copying 500MB+ files."""
    if pdf_path.exists():
        stat = pdf_path.stat()
        sha = hashlib.sha256(f"{pdf_path.name}:{stat.st_size}".encode("utf-8")).hexdigest()
        try:
            with fitz.open(str(pdf_path)) as doc:
                pages = len(doc)
        except Exception:
            pages = 1
        filename = pdf_path.name
        path_str = str(pdf_path)
    else:
        folder = store.project_dir(project_id) / "documents"
        folder.mkdir(parents=True, exist_ok=True)
        fallback = folder / f"{category}_v{version}.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), f"S604 {label}", fontsize=14)
        fallback.write_bytes(doc.tobytes())
        doc.close()
        sha = hashlib.sha256(fallback.read_bytes()).hexdigest()
        pages = 1
        filename = fallback.name
        path_str = str(fallback)

    return store.insert(
        "documents",
        {
            "project_id": project_id,
            "category": category,
            "label": label,
            "version": version,
            "filename": filename,
            "path": path_str,
            "sha256": sha,
            "pages": pages,
            "failed_pages": [],
            "parse_status": "Parsed",
            "selected": 1,
            "uploaded_at": store.now(),
        },
    )


def create_s604_project(progress: Optional[Callable[[str, int, int], None]] = None) -> int:
    """Create the S604 full 359-clause real engineering project and run Round 1 with the real backend engine."""
    existing = next(
        (p for p in store.list_projects() if "S604" in (p.get("name") or "")),
        None,
    )
    if existing and store.list_rounds(existing["id"]):
        return existing["id"]
    if existing:
        store.delete_project(existing["id"])

    project_id = store.create_project("11.11-S604皖南交旅融合（飞毕路美丽公路）项目 (全量359条)", is_demo=False)
    s604_dir = _find_s604_data_dir()

    tender_pdf = (
        s604_dir / "01_招标文件" / "招标文件正文.pdf"
        if s604_dir
        else REPO_ROOT / "招标文件正文.pdf"
    )
    addendum_pdf = (
        s604_dir / "01_招标文件" / "答疑与补遗" / "S604皖南交旅融合（飞毕路美丽公路）项目工程答疑澄清.pdf"
        if s604_dir
        else REPO_ROOT / "工程答疑澄清.pdf"
    )
    bid_pdf = (
        s604_dir / "02_投标文件" / "S604投标文件11日8点修改.PDF"
        if s604_dir
        else REPO_ROOT / "S604投标文件.pdf"
    )

    tender_doc_id = _register_external_pdf(project_id, "tender", "招标文件正文.pdf", tender_pdf)
    addendum_doc_id = _register_external_pdf(project_id, "addendum", "工程答疑澄清.pdf", addendum_pdf)
    bid_doc_id = _register_external_pdf(project_id, "bid", "S604投标文件(含分册索引).pdf", bid_pdf)

    ann_by_id: Dict[str, Dict[str, Any]] = {}
    if S604_ANNOTATIONS_PATH.exists():
        for a in json.loads(S604_ANNOTATIONS_PATH.read_text(encoding="utf-8")):
            ann_by_id[a["ID"]] = a

    evaluated = evaluate_all_s604_clauses(progress=progress)

    bundle = json.loads(S604_CASES_PATH.read_text(encoding="utf-8"))
    cases = bundle["cases"]

    round_id = store.insert(
        "rounds",
        {
            "project_id": project_id,
            "no": 1,
            "bid_doc_id": bid_doc_id,
            "doc_ids": [tender_doc_id, addendum_doc_id, bid_doc_id],
            "engine": config.ENGINE_NAME,
            "rule_version": config.RULE_VERSION,
            "status": "running",
            "created_at": store.now(),
        },
    )

    first_req_id: Optional[int] = None
    with store.connect() as con:
        now_ts = store.now()
        for case in cases:
            cid = case["clause_id"]
            req_text = case["requirement"]
            category = case["category"] if case["category"] in config.CATEGORIES else "Document"
            ann = ann_by_id.get(cid, {})
            page = int(ann.get("Page") or 1)
            sec = str(ann.get("Section") or "")
            is_addendum = sec.startswith("答疑")
            src_doc_id = addendum_doc_id if is_addendum else tender_doc_id

            out = evaluated[cid]
            constraint = out["constraint"]

            cur = con.execute(
                """
                INSERT INTO requirements (
                    project_id, clause_no, text, category, category_score,
                    constraint_json, source_doc_id, page, origin, original_json,
                    amended_by_doc_id, amendment_page, amendment_text, amendment_status,
                    applicable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    project_id,
                    cid,
                    req_text,
                    category,
                    1.0,
                    json.dumps(constraint, ensure_ascii=False),
                    src_doc_id,
                    page,
                    "addendum" if is_addendum else "extracted",
                    json.dumps(
                        {"text": req_text, "category": category, "constraint": constraint, "page": page, "section": sec},
                        ensure_ascii=False,
                    ),
                    addendum_doc_id if is_addendum else None,
                    page if is_addendum else None,
                    req_text if is_addendum else None,
                    "accepted" if is_addendum else None,
                    now_ts,
                ),
            )
            req_id = cur.lastrowid
            if cid == "T0056" or (first_req_id is None and out["verdict"] != config.COMPLIANT):
                first_req_id = req_id

            initial_status = config.CONFIRMED if out["verdict"] == config.COMPLIANT else config.UNREVIEWED
            con.execute(
                """
                INSERT INTO results (
                    round_id, requirement_id, verdict, original_verdict, rule_id,
                    explanation, fields_json, evidence_json, chain_json,
                    corrected, human_status, review_seconds, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0, ?)
                """,
                (
                    round_id,
                    req_id,
                    out["verdict"],
                    out["verdict"],
                    out["rule_id"],
                    out["explanation"],
                    json.dumps(out["fields"], ensure_ascii=False),
                    json.dumps(out["evidence"], ensure_ascii=False),
                    json.dumps(out["chain"], ensure_ascii=False),
                    initial_status,
                    now_ts,
                ),
            )

        for ki in S604_KNOWN_ISSUES:
            con.execute(
                "INSERT INTO known_issues (project_id, clause_no, expected, note) VALUES (?, ?, ?, ?)",
                (project_id, ki["clause_no"], ki["expected"], ki["note"]),
            )

        con.execute("UPDATE rounds SET status='done' WHERE id=?", (round_id,))
        con.execute(
            "UPDATE projects SET req_confirmed_at=?, last_requirement_id=?, baseline_minutes=480 WHERE id=?",
            (now_ts, first_req_id, project_id),
        )

    store.log_action(
        project_id,
        "load_s604_real_project",
        "System",
        "Reviewer",
        "Loaded S604 full 359-clause real project with GlobalContext + ComplianceRuleEngine v3",
        {"clauses": len(cases), "round_id": round_id},
        round_id=round_id,
    )
    return project_id

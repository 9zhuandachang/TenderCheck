"""
End-to-End Automated Tender Requirement Extractor & 8-Class Annotator (No LLM API).

Pipeline:
1. Structural PDF Segmentation (`segment_tender_pdf`):
   Extracts table cells (with merged row/column headers) and hierarchical body clauses
   from tender & addendum PDFs without any external API calls.
2. High-Recall Binary Requirement Filtering (`BinaryRequirementFilter`):
   Filters out non-requirement boilerplate (TOC, contract definitions, blank forms)
   using the chapter-aware high-recall threshold (`chapter_aware=True`).
3. Lightweight Text Normalization (`normalize_candidate_to_requirement`):
   Normalizes `【表头】` prefixes, checkbox markers (`☑`), and form fill-in lines into
   clean, self-contained Chinese requirement sentences while preserving `raw_text`
   for exact PDF page highlighting.
4. 8-Class Requirement Classification (`BertRequirementClassifier`):
   Assigns each retained requirement to one of the 8 standard categories
   (`Qualification`, `Document`, `Technical`, `Commercial`, `Schedule`,
   `Consistency`, `Integrity`, `Scoring`) with confidence scores, and outputs
   standard v1.3 `annotations.json` items (`ID`, `Page`, `Section`, `Requirement Type`, `Requirement`).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import pymupdf

from ..classification.binary_filter import (
    BinaryRequirementFilter,
    align_candidates_with_annotations,
    compute_clause_overlap,
)
from ..data.schema import ID2LABEL, REQUIREMENT_TYPES
from .addendum_superseder import apply_addendum_superseding
from .atomic_decomposer import decompose_compound_requirement
from .tender_segmenter import CandidateClause, _CHAPTER_RE, _split_long_unit, segment_tender_pdf

if TYPE_CHECKING:
    from ..classification import BaseRequirementClassifier

logger = logging.getLogger(__name__)

_PREFIX_BRACKET_RE = re.compile(r"^【([^】]+)】\s*(.+)$", flags=re.DOTALL)
_LEADING_CLAUSE_NO_RE = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)+)|(?:\d+[、.．])|(?:[一二三四五六七八九十]+[、.．])|(?:[（(][一二三四五六七八九十0-9]+[)）])|(?:[⑴⑵⑶⑷⑸⑹⑺⑻⑼⑽①②③④⑤⑥⑦⑧⑨⑩]))\s*"
)


@dataclass
class AutoAnnotatedRequirement:
    """Standard v1.3 requirement item enriched with binary & 8-class confidence metadata."""

    ID: str
    Page: int
    Section: str
    Requirement_Type: str
    Requirement: str
    raw_text: str = ""
    source_type: str = "body"
    binary_prob: float = 1.0
    category_confidence: float = 1.0
    doc_name: str = ""
    is_addendum: bool = False
    lifecycle_status: str = "ACTIVE"  # "ACTIVE" | "SUPERSEDED" | "CANCELLED" | "DIRECTIVE_ONLY"
    superseded_by: str = ""
    supersedes_ids: List[str] = field(default_factory=list)
    superseding_note: str = ""

    def to_v13_dict(self) -> Dict[str, Any]:
        """Return strict v1.3 annotation dictionary (`ID`, `Page`, `Section`, `Requirement Type`, `Requirement`)."""
        return {
            "ID": self.ID,
            "Page": self.Page,
            "Section": self.Section,
            "Requirement Type": self.Requirement_Type,
            "Requirement": self.Requirement,
        }

    def to_full_dict(self) -> Dict[str, Any]:
        """Return enriched dictionary including `raw_text`, `binary_prob`, `category_confidence`, and superseding audit fields."""
        d = self.to_v13_dict()
        d.update(
            {
                "raw_text": self.raw_text,
                "source_type": self.source_type,
                "binary_prob": round(float(self.binary_prob), 4),
                "category_confidence": round(float(self.category_confidence), 4),
                "doc_name": self.doc_name,
                "is_addendum": self.is_addendum,
                "lifecycle_status": self.lifecycle_status,
                "superseded_by": self.superseded_by,
                "supersedes_ids": list(self.supersedes_ids),
                "superseding_note": self.superseding_note,
            }
        )
        return d


def normalize_candidate_to_requirement(cand: CandidateClause) -> str:
    """
    Convert a segmented `CandidateClause` into a clean, self-contained Chinese
    requirement sentence suitable for 8-class classification and downstream RAG.
    """
    text = (cand.text or "").strip()
    if not text:
        return ""

    # Clean checkbox glyphs and long blank underlines
    text = text.replace("☑", "").replace("■", "")
    text = re.sub(r"_{3,}", "___", text)
    text = re.sub(r"\s+", " ", text).strip()

    m = _PREFIX_BRACKET_RE.match(text)
    if m:
        raw_prefix = m.group(1).strip()
        body = m.group(2).strip()
        body = _LEADING_CLAUSE_NO_RE.sub("", body).strip()

        # Clean multi-part table header prefix `A | B`
        parts = [
            _LEADING_CLAUSE_NO_RE.sub("", p).strip()
            for p in raw_prefix.split("|")
            if p.strip()
        ]
        parts = [p for p in parts if p and p not in ("条款号", "条款名称", "编列内容", "内容", "评审因素", "评审标准")]

        if parts:
            subject = " - ".join(parts[-2:]) if len(parts) > 1 else parts[0]
            # Check if body already starts with or contains the core subject
            subj_core = re.sub(r"[：:（）()\s]", "", subject)
            body_core = re.sub(r"[：:（）()\s]", "", body[: max(len(subj_core) + 8, 20)])
            if subj_core and (subj_core in body_core or len(body) >= 45 and any(k in body[:15] for k in ("投标人", "项目经理", "项目总工", "本工程", "本项目", "投标文件"))):
                cleaned = body
            else:
                cleaned = f"{subject}：{body}"
        else:
            cleaned = body
    else:
        cleaned = _LEADING_CLAUSE_NO_RE.sub("", text).strip()

    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned and cleaned[-1] not in "。；;！!？?）)":
        cleaned += "。"
    return cleaned


def _segment_single_pdf_with_ocr_fallback(
    pdf_path: Path,
    is_addendum: bool = False,
    ocr_backend: Optional[Any] = None,
) -> List[CandidateClause]:
    """
    Segment a tender or addendum PDF using `segment_tender_pdf`, and run cached OCR
    fallback (`LocalHeuristicOCRBackend`) on any image-only scanned pages (both for
    main tender PDFs and addenda/clarifications) so scanned pages never yield 0 clauses.
    """
    pdf_path = Path(pdf_path)
    candidates = segment_tender_pdf(pdf_path)

    # Identify pages lacking a searchable text layer that contain embedded scanned images
    scanned_pages: List[int] = []
    try:
        with pymupdf.open(str(pdf_path)) as doc:
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                txt = re.sub(r"\s+", "", page.get_text("text") or "")
                if len(txt) < 30 and (len(page.get_images(full=True)) > 0 or not candidates):
                    scanned_pages.append(page_idx + 1)
    except Exception as exc:
        logger.warning("Failed to inspect scanned pages in %s: %s", pdf_path, exc)
        return candidates

    if not scanned_pages:
        return candidates

    try:
        if ocr_backend is None:
            from ..rag.vision import DiskCachedVisionBackend, LocalHeuristicOCRBackend

            cache_dir = Path(__file__).resolve().parents[2] / "data" / "vision_cache"
            ocr_backend = DiskCachedVisionBackend(
                LocalHeuristicOCRBackend(dpi=180),
                cache_dir=cache_dir,
            )

        doc = pymupdf.open(str(pdf_path))
        doc_name = pdf_path.name
        default_chapter = "答疑与澄清" if is_addendum else "招标文件（扫描件）"
        current_chapter = default_chapter
        cand_seq = 0
        ocr_candidates: List[CandidateClause] = []

        for page_no in scanned_pages:
            res = ocr_backend.analyze_page(doc, page_no, existing_text="", heading_hint=current_chapter)
            ocr_text = (res.ocr_text or "").strip()
            if not ocr_text:
                continue
            for ln in ocr_text.splitlines():
                m_chap = _CHAPTER_RE.match(ln.strip())
                if m_chap:
                    current_chapter = f"{m_chap.group(1)} {m_chap.group(2).strip()}".strip()
                    break
            for unit in _split_long_unit(ocr_text, min_cjk_len=6):
                cand_seq += 1
                prefix = "ADD" if is_addendum else "OCR"
                sec_label = f"{doc_name} / {'答疑澄清' if is_addendum else current_chapter.split()[0]}"
                ocr_candidates.append(
                    CandidateClause(
                        candidate_id=f"{prefix}_{cand_seq:04d}",
                        doc_name=doc_name,
                        page=page_no,
                        chapter=current_chapter,
                        section=sec_label,
                        text=unit,
                        raw_text=unit,
                        source_type="body",
                    )
                )
        doc.close()

        if ocr_candidates:
            candidates.extend(ocr_candidates)
            candidates.sort(key=lambda c: c.page)
    except Exception as exc:
        logger.warning("OCR fallback on %s failed: %s", pdf_path, exc)

    return candidates


class AutoTenderAnnotator:
    """
    End-to-end automated tender requirement extractor and 8-class classifier.
    Loads local `BinaryRequirementFilter` and 8-class classifier (`roberta_wwm_ext`) and processes raw tender PDFs into standard v1.3 annotations.
    """

    def __init__(
        self,
        binary_model_dir: Union[str, Path],
        eight_class_model_dir: Union[str, Path],
        device: Optional[str] = None,
        chapter_aware: bool = True,
        binary_threshold: Optional[float] = None,
        decompose_atomic: bool = True,
    ) -> None:
        self.binary_model_dir = Path(binary_model_dir)
        self.eight_class_model_dir = Path(eight_class_model_dir)
        self.device = device
        self.chapter_aware = chapter_aware
        self.binary_threshold = binary_threshold
        self.decompose_atomic = decompose_atomic

        from ..classification import load_classifier

        self.binary_filter: BinaryRequirementFilter = BinaryRequirementFilter.load(self.binary_model_dir)
        self.classifier: "BaseRequirementClassifier" = load_classifier(self.eight_class_model_dir, device=self.device)

    def annotate_pdfs(
        self,
        tender_pdfs: Sequence[Union[str, Path]],
        addendum_pdfs: Optional[Sequence[Union[str, Path]]] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[List[AutoAnnotatedRequirement], Dict[str, Any]]:
        """
        Run the complete 3-stage local extraction & classification pipeline on
        tender and addendum PDFs:
          1. Structural segmentation (`segment_tender_pdf`)
          2. High-recall binary filtering (`BinaryRequirementFilter.filter_candidates`)
          3. 8-class requirement classification (`self.classifier.predict_proba`)

        Returns:
            Tuple of `(annotated_items, stats_dict)`.
        """
        all_candidates: List[CandidateClause] = []
        tender_list = [Path(p) for p in tender_pdfs]
        addendum_list = [Path(p) for p in (addendum_pdfs or [])]
        total_files = len(tender_list) + len(addendum_list)

        for idx, pdf_path in enumerate(tender_list, start=1):
            if progress:
                progress(f"正在结构化切分招标文件 {pdf_path.name} ({idx}/{total_files})", idx - 1, total_files + 2)
            cands = _segment_single_pdf_with_ocr_fallback(pdf_path, is_addendum=False)
            all_candidates.extend(cands)

        addendum_doc_names = {p.name for p in addendum_list}
        for idx, pdf_path in enumerate(addendum_list, start=len(tender_list) + 1):
            if progress:
                progress(f"正在切分答疑/补遗文件 {pdf_path.name} ({idx}/{total_files})", idx - 1, total_files + 2)
            cands = _segment_single_pdf_with_ocr_fallback(pdf_path, is_addendum=True)
            all_candidates.extend(cands)

        if not all_candidates:
            return [], {
                "total_candidates": 0,
                "retained_requirements": 0,
                "filtered_noise": 0,
                "noise_reduction_rate": 0.0,
                "category_distribution": {},
            }

        if progress:
            progress(
                f"正在执行高召回二分类过滤 (候选块: {len(all_candidates)})...",
                total_files,
                total_files + 2,
            )

        retained_pairs = self.binary_filter.filter_candidates(
            all_candidates,
            threshold=self.binary_threshold,
            chapter_aware=self.chapter_aware,
        )

        if not retained_pairs:
            # Safety fallback: if threshold was too strict on an unusual document, take top candidates
            probs = self.binary_filter.predict_proba(all_candidates)[:, 1]
            top_indices = probs.argsort()[::-1][: min(50, len(all_candidates))]
            retained_pairs = [(all_candidates[i], float(round(probs[i], 4))) for i in sorted(top_indices)]

        if progress:
            progress(
                f"正在执行8分类合规类别打标 (保留条款: {len(retained_pairs)})...",
                total_files + 1,
                total_files + 2,
            )

        # Normalize & optionally decompose compound multi-predicate clauses into atomic requirements
        expanded_pairs: List[Tuple[CandidateClause, float, str]] = []
        cls_inputs: List[Dict[str, str]] = []
        for cand, bin_prob in retained_pairs:
            norm_req = normalize_candidate_to_requirement(cand)
            atomic_list = (
                decompose_compound_requirement(norm_req, section=cand.section or cand.chapter)
                if self.decompose_atomic
                else [norm_req]
            )
            for atom_req in atomic_list:
                expanded_pairs.append((cand, bin_prob, atom_req))
                cls_inputs.append(
                    {
                        "Section": cand.section or cand.chapter,
                        "Requirement": atom_req,
                    }
                )

        probs_8 = self.classifier.predict_proba(cls_inputs)
        items: List[AutoAnnotatedRequirement] = []
        cat_counts: Dict[str, int] = {cat: 0 for cat in REQUIREMENT_TYPES}

        for idx, ((cand, bin_prob, atom_req), prob_vec) in enumerate(
            zip(expanded_pairs, probs_8), start=1
        ):
            best_cls_id = int(prob_vec.argmax())
            pred_cat = ID2LABEL[best_cls_id]
            conf = float(round(prob_vec[best_cls_id], 4))
            cat_counts[pred_cat] = cat_counts.get(pred_cat, 0) + 1

            section_str = cand.section or cand.chapter or cand.doc_name
            if cand.doc_name and not section_str.startswith(cand.doc_name):
                section_str = f"{cand.doc_name} / {section_str}"

            items.append(
                AutoAnnotatedRequirement(
                    ID=f"T{idx:04d}",
                    Page=int(cand.page),
                    Section=section_str,
                    Requirement_Type=pred_cat,
                    Requirement=atom_req,
                    raw_text=cand.raw_text or cand.text,
                    source_type=cand.source_type,
                    binary_prob=bin_prob,
                    category_confidence=conf,
                    doc_name=cand.doc_name,
                    is_addendum=(cand.doc_name in addendum_doc_names),
                )
            )

        sup_report = None
        if addendum_list and items:
            items, sup_report = apply_addendum_superseding(items)

        if progress:
            progress("招标条款自动抽取与8分类标注完成", total_files + 2, total_files + 2)

        stats = {
            "total_candidates": len(all_candidates),
            "retained_requirements": len(items),
            "active_requirements": sup_report.active_count if sup_report else len(items),
            "superseded_count": sup_report.superseded_count if sup_report else 0,
            "cancelled_count": sup_report.cancelled_count if sup_report else 0,
            "unmatched_addenda_count": sup_report.unmatched_addenda_count if sup_report else 0,
            "filtered_noise": len(all_candidates) - len(items),
            "noise_reduction_rate": round(
                (len(all_candidates) - len(items)) / max(len(all_candidates), 1), 4
            ),
            "category_distribution": {k: v for k, v in cat_counts.items() if v > 0},
        }
        return items, stats


def evaluate_auto_annotations_against_gold(
    auto_items: Sequence[AutoAnnotatedRequirement],
    gold_annotations: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Evaluate end-to-end auto-annotated requirements (`PDF -> Segmenter -> BinaryFilter -> 8-Class`)
    against human-annotated gold clauses on a holdout project:
    1. Gold Clause Recall (how many human-annotated main-text clauses are covered by `auto_items`).
    2. 8-Class Classification Accuracy & Macro-F1 on the matched gold clauses.
    """
    main_gold = [
        a for a in gold_annotations
        if "答疑" not in str(a.get("Section", ""))
        and "澄清" not in str(a.get("Section", ""))
        and "补遗" not in str(a.get("Section", ""))
    ]

    by_page_auto: Dict[int, List[AutoAnnotatedRequirement]] = {}
    for item in auto_items:
        by_page_auto.setdefault(item.Page, []).append(item)

    recalled_gold = 0
    y_true_cats: List[str] = []
    y_pred_cats: List[str] = []
    exact_cat_matches = 0
    matched_details: List[Dict[str, Any]] = []

    for g in main_gold:
        pno = int(g.get("Page", 1) or 1)
        g_id = str(g.get("ID", ""))
        g_req = str(g.get("Requirement", "")).strip()
        g_cat = str(g.get("Requirement Type", "")).strip()
        if not g_req:
            continue

        pool = list(by_page_auto.get(pno, []))
        if not pool:
            pool = list(by_page_auto.get(pno - 1, [])) + list(by_page_auto.get(pno + 1, []))

        best_item: Optional[AutoAnnotatedRequirement] = None
        best_comb = 0.0
        best_grec = 0.0
        for cand in pool:
            g_rec1, _, comb1 = compute_clause_overlap(cand.Requirement, g_req)
            g_rec2, _, comb2 = compute_clause_overlap(cand.raw_text, g_req)
            g_rec, comb = (g_rec2, comb2) if comb2 > comb1 else (g_rec1, comb1)
            if comb > best_comb:
                best_comb = comb
                best_grec = g_rec
                best_item = cand

        if best_item is not None and (best_grec >= 0.45 or best_comb >= 0.40):
            recalled_gold += 1
            y_true_cats.append(g_cat)
            y_pred_cats.append(best_item.Requirement_Type)
            if g_cat == best_item.Requirement_Type:
                exact_cat_matches += 1
            matched_details.append(
                {
                    "gold_id": g_id,
                    "page": pno,
                    "gold_type": g_cat,
                    "pred_id": best_item.ID,
                    "pred_type": best_item.Requirement_Type,
                    "overlap_score": round(best_comb, 4),
                    "gold_text": g_req[:80],
                    "pred_text": best_item.Requirement[:80],
                }
            )

    cat_acc = round(exact_cat_matches / max(recalled_gold, 1), 4)
    return {
        "gold_main_clauses": len(main_gold),
        "auto_extracted_clauses": len(auto_items),
        "recalled_gold_clauses": recalled_gold,
        "end_to_end_gold_recall": round(recalled_gold / max(len(main_gold), 1), 4),
        "matched_category_accuracy": cat_acc,
        "exact_category_matches": exact_cat_matches,
        "sample_matches": matched_details[:15],
    }

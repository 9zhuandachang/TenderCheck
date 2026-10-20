"""
Multimodal Bid Document Parser & Parent-Child Chunker for TenderCheck RAG.

Upgrades the legacy Stage-1 text chunker into a full three-stream ingestion pipeline:
1. Narrative Stream: Section-hierarchy-aware Parent-Child chunking
   (Parent ~900 chars for full context, Child ~300 chars for high-precision retrieval).
2. Table Stream: Extracts structured tables via PyMuPDF `find_tables()`, storing
   the full Markdown table as the Parent Chunk and row-level natural-language
   key-value statements as Child Chunks.
3. Vision/Scanned Certificate Stream: Routes scanned pages, certificates, and
   commitment pages through the unified `BaseVisionBackend` (`PageVisionResult`)
   so zero pages are dropped and official seal / signature metadata is preserved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz
except ImportError:  # Older supported PyMuPDF installations.
    import fitz

from .vision import (
    BaseVisionBackend,
    DiskCachedVisionBackend,
    LocalHeuristicOCRBackend,
    PageVisionResult,
    detect_red_seal_from_pixmap,
    infer_doc_category_from_text,
)

logger = logging.getLogger(__name__)

PAGE_NUMBER_RE = re.compile(r"^\s*第?\s*\d+\s*页(?:\s*/\s*共\s*\d+\s*页)?\s*$")
ONLY_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零〇\d]+[章节部分篇]\s*.+")
ATTACHMENT_RE = re.compile(r"^附件[一二三四五六七八九十百零〇\d]+[：:\s]*.+")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;|\n])")


@dataclass
class BidChunk:
    """
    Standardized chunk representation for both Parent and Child chunks in the
    TenderCheck Hybrid RAG index.
    """

    chunk_id: str
    parent_id: str
    is_parent: bool
    document_id: str
    volume_type: str
    doc_category: str
    content_type: str  # "narrative" | "table" | "table_row" | "certificate" | "commitment" | "scanned"
    section_path: str
    page_start: int
    page_end: int
    printed_page_start: Optional[int]
    printed_page_end: Optional[int]
    text: str
    embedding_text: str
    has_official_seal: bool = False
    seal_company_name: Optional[str] = None
    has_signature: bool = False
    extracted_kv: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BidChunk":
        return cls(
            chunk_id=str(data["chunk_id"]),
            parent_id=str(data["parent_id"]),
            is_parent=bool(data.get("is_parent", False)),
            document_id=str(data["document_id"]),
            volume_type=str(data.get("volume_type", "商务与资信标")),
            doc_category=str(data.get("doc_category", "其他投标材料")),
            content_type=str(data.get("content_type", "narrative")),
            section_path=str(data.get("section_path", "")),
            page_start=int(data.get("page_start", 1)),
            page_end=int(data.get("page_end", 1)),
            printed_page_start=data.get("printed_page_start"),
            printed_page_end=data.get("printed_page_end"),
            text=str(data.get("text", "")),
            embedding_text=str(data.get("embedding_text", data.get("text", ""))),
            has_official_seal=bool(data.get("has_official_seal", False)),
            seal_company_name=data.get("seal_company_name"),
            has_signature=bool(data.get("has_signature", False)),
            extracted_kv=dict(data.get("extracted_kv") or {}),
        )


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _stable_hash(*parts: str) -> str:
    joined = "||".join(str(p) for p in parts if p is not None)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _detect_heading_level(line: str) -> Optional[int]:
    s = line.strip()
    if not s or len(_compact(s)) > 48:
        return None
    if s[-1] in "。；;，,：:":
        return None
    if CHAPTER_RE.match(s) or ATTACHMENT_RE.match(s):
        return 1
    if re.match(r"^[一二三四五六七八九十]+、\s*\S+", s):
        return 2
    if re.match(r"^[(（][一二三四五六七八九十]+[)）]\s*\S+", s):
        return 3
    if re.match(r"^\d+\.\d+(?:\.\d+)?\s+\S+", s):
        return 3
    return None


def _extract_tables_as_markdown_and_rows(
    page: fitz.Page,
    pdf_page: int,
    section_path: str,
) -> Tuple[List[str], List[str]]:
    """
    Extract native tables from a PDF page using PyMuPDF `find_tables()`.

    Returns:
        Tuple of `(markdown_tables, row_kv_sentences)`:
        - `markdown_tables`: Full Markdown table strings for Parent Chunks.
        - `row_kv_sentences`: Natural-language row statements for Child Chunks.
    """
    md_tables: List[str] = []
    row_statements: List[str] = []

    try:
        finder = page.find_tables()
    except Exception:
        return md_tables, row_statements

    for tbl in getattr(finder, "tables", []):
        raw_rows = tbl.extract()
        if not raw_rows or len(raw_rows) < 2:
            continue

        cleaned_rows: List[List[str]] = []
        for row in raw_rows:
            cells = [re.sub(r"\s+", " ", (c or "").strip()) for c in row]
            if any(cells):
                cleaned_rows.append(cells)

        if len(cleaned_rows) < 2:
            continue

        headers = [h if h else f"列{i+1}" for i, h in enumerate(cleaned_rows[0])]
        md_lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]

        for row_cells in cleaned_rows[1:]:
            padded = row_cells + [""] * max(0, len(headers) - len(row_cells))
            padded = padded[: len(headers)]
            md_lines.append("| " + " | ".join(padded) + " |")

            kv_pairs = [
                f"{headers[col_i]}: {val}"
                for col_i, val in enumerate(padded)
                if val and val != headers[col_i]
            ]
            if kv_pairs:
                prefix = f"[{section_path or '投标表格'} | 第{pdf_page}页]"
                row_statements.append(f"{prefix} " + "；".join(kv_pairs))

        md_tables.append("\n".join(md_lines))

    return md_tables, row_statements


def _split_text_into_spans(
    text: str,
    target_chars: int = 300,
    max_chars: int = 420,
    overlap_chars: int = 50,
) -> List[str]:
    """Split a parent text block into overlapping child spans along sentence boundaries."""
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(_compact(cleaned)) <= max_chars:
        return [cleaned]

    pieces = [p.strip() for p in SENTENCE_SPLIT_RE.split(cleaned) if p.strip()]
    spans: List[str] = []
    buf: List[str] = []

    for piece in pieces:
        candidate = "".join(buf) + piece
        if buf and len(_compact(candidate)) > target_chars:
            chunk_str = "".join(buf).strip()
            if chunk_str:
                spans.append(chunk_str)
            overlap = chunk_str[-overlap_chars:] if overlap_chars > 0 else ""
            buf = [overlap, piece] if overlap else [piece]
        else:
            buf.append(piece)

    if buf:
        tail = "".join(buf).strip()
        if tail:
            spans.append(tail)

    return spans


class BidDocumentParser:
    """
    Multimodal Bid Document Parser that produces Parent Chunks, Child Chunks,
    and page-level metadata without dropping scanned certificate or table pages.
    """

    def __init__(
        self,
        vision_backend: Optional[BaseVisionBackend] = None,
        cache_dir: Optional[Path] = None,
        parent_target_chars: int = 900,
        child_target_chars: int = 300,
        child_overlap_chars: int = 50,
    ) -> None:
        if vision_backend is None:
            base_backend = LocalHeuristicOCRBackend()
            if cache_dir is not None:
                vision_backend = DiskCachedVisionBackend(base_backend, cache_dir=cache_dir)
            else:
                vision_backend = base_backend
        elif cache_dir is not None and not isinstance(vision_backend, DiskCachedVisionBackend):
            vision_backend = DiskCachedVisionBackend(vision_backend, cache_dir=cache_dir)

        self.vision_backend = vision_backend
        self.parent_target_chars = parent_target_chars
        self.child_target_chars = child_target_chars
        self.child_overlap_chars = child_overlap_chars
        self.document_sources: Dict[str, str] = {}

    @staticmethod
    def _detect_volume_cover(page_text: str) -> Optional[str]:
        lines = {_compact(ln) for ln in page_text.splitlines() if ln.strip()}
        if len(_compact(page_text)) > 600:
            return None
        if "商务文件" in lines or "资信文件" in lines or "资格审查资料" in lines:
            return "商务与资信标"
        if "报价文件" in lines or "商务及报价文件" in lines:
            return "报价标"
        if "技术文件" in lines or "施工组织设计" in lines:
            return "技术标"
        return None

    def parse_pdf(
        self,
        pdf_path: Path,
        document_id: Optional[str] = None,
        start_page: int = 1,
        end_page: Optional[int] = None,
        forced_volume: Optional[str] = None,
        forced_doc_category: Optional[str] = None,
    ) -> Tuple[List[BidChunk], List[BidChunk], Dict[str, Any]]:
        """
        Parse a bid PDF into `(parent_chunks, child_chunks, stats_dict)`.

        Args:
            pdf_path: Path to the bid PDF file.
            document_id: Unique identifier for the bid document.
            start_page: 1-indexed start page.
            end_page: 1-indexed end page (inclusive).
            forced_volume: Optional volume override (when parsing sub-chapter files).
            forced_doc_category: Optional category override (when parsing sub-chapter files).

        Returns:
            Tuple of `(parent_chunks, child_chunks, stats_dict)`.
        """
        pdf_path = Path(pdf_path)
        doc_id = document_id or pdf_path.stem
        self.document_sources[doc_id] = str(pdf_path.resolve())
        doc = fitz.open(pdf_path)
        last_page = min(end_page or len(doc), len(doc))

        # Pass 1: Extract raw lines & identify repeated running headers/footers
        header_Bottom_counter: Counter[str] = Counter()
        raw_page_lines: Dict[int, List[str]] = {}
        for pno in range(start_page, last_page + 1):
            page = doc[pno - 1]
            text = page.get_text("text") or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            raw_page_lines[pno] = lines
            if lines:
                header_Bottom_counter[_compact(lines[0])] += 1
                if len(lines) > 1:
                    header_Bottom_counter[_compact(lines[-1])] += 1

        repeated_noise = {
            k for k, cnt in header_Bottom_counter.items() if cnt >= 6 and len(k) <= 45
        }

        parent_chunks: List[BidChunk] = []
        child_chunks: List[BidChunk] = []
        current_volume = forced_volume or "商务与资信标"
        headings: Dict[int, str] = {}
        page_type_counts: Counter[str] = Counter()

        parent_seq = 0
        child_seq = 0

        def _emit_parent_and_children(
            content_type: str,
            doc_category: str,
            section_path: str,
            p_start: int,
            p_end: int,
            parent_text: str,
            child_texts: Sequence[str],
            has_seal: bool = False,
            seal_name: Optional[str] = None,
            has_sig: bool = False,
            kv: Optional[Dict[str, Any]] = None,
        ) -> None:
            nonlocal parent_seq, child_seq
            if not parent_text.strip():
                return
            parent_seq += 1
            p_id = f"{doc_id}_P{parent_seq:05d}"
            prefix = f"[{current_volume} | {doc_category} | {section_path}]"
            p_emb_text = f"{prefix}\n{parent_text.strip()}"

            p_chunk = BidChunk(
                chunk_id=p_id,
                parent_id=p_id,
                is_parent=True,
                document_id=doc_id,
                volume_type=current_volume,
                doc_category=doc_category,
                content_type=content_type,
                section_path=section_path,
                page_start=p_start,
                page_end=p_end,
                printed_page_start=p_start,
                printed_page_end=p_end,
                text=parent_text.strip(),
                embedding_text=p_emb_text,
                has_official_seal=has_seal,
                seal_company_name=seal_name,
                has_signature=has_sig,
                extracted_kv=dict(kv or {}),
            )
            parent_chunks.append(p_chunk)

            effective_children = [c.strip() for c in child_texts if c.strip()] or [parent_text.strip()]
            for c_text in effective_children:
                child_seq += 1
                c_id = f"{doc_id}_C{child_seq:06d}"
                c_emb_text = f"{prefix}\n{c_text}"
                child_chunks.append(
                    BidChunk(
                        chunk_id=c_id,
                        parent_id=p_id,
                        is_parent=False,
                        document_id=doc_id,
                        volume_type=current_volume,
                        doc_category=doc_category,
                        content_type="table_row" if content_type == "table" else content_type,
                        section_path=section_path,
                        page_start=p_start,
                        page_end=p_end,
                        printed_page_start=p_start,
                        printed_page_end=p_end,
                        text=c_text,
                        embedding_text=c_emb_text,
                        has_official_seal=has_seal,
                        seal_company_name=seal_name,
                        has_signature=has_sig,
                        extracted_kv=dict(kv or {}),
                    )
                )

        # Narrative buffer state across consecutive narrative pages
        nar_buf: List[str] = []
        nar_start_page: Optional[int] = None
        nar_end_page: Optional[int] = None
        nar_has_seal: bool = False
        nar_has_sig: bool = False

        def _flush_narrative_buffer() -> None:
            nonlocal nar_buf, nar_start_page, nar_end_page, nar_has_seal, nar_has_sig
            if not nar_buf:
                return
            joined = "\n".join(nar_buf).strip()
            sec_path = " > ".join(headings[k] for k in sorted(headings)) or current_volume
            doc_cat = forced_doc_category or infer_doc_category_from_text(joined, sec_path)
            kv = {}
            c_spans = _split_text_into_spans(
                joined,
                target_chars=self.child_target_chars,
                overlap_chars=self.child_overlap_chars,
            )
            _emit_parent_and_children(
                content_type="narrative",
                doc_category=doc_cat,
                section_path=sec_path,
                p_start=nar_start_page or 1,
                p_end=nar_end_page or nar_start_page or 1,
                parent_text=joined,
                child_texts=c_spans,
                has_seal=nar_has_seal,
                has_sig=nar_has_sig,
                kv=kv,
            )
            nar_buf = []
            nar_start_page = None
            nar_end_page = None
            nar_has_seal = False
            nar_has_sig = False

        # Pass 2: Stream through pages and route to Vision, Table, or Narrative handler
        for pno in range(start_page, last_page + 1):
            page = doc[pno - 1]
            lines = [
                ln
                for ln in raw_page_lines.get(pno, [])
                if _compact(ln) not in repeated_noise
                and not PAGE_NUMBER_RE.match(ln)
                and not ONLY_NUMBER_RE.match(ln)
            ]
            clean_text = "\n".join(lines).strip()
            clean_len = len(_compact(clean_text))
            img_count = len(page.get_images(full=True))

            # Update volume if a volume cover is encountered
            if not forced_volume:
                cov = self._detect_volume_cover(clean_text)
                if cov:
                    _flush_narrative_buffer()
                    current_volume = cov
                    headings.clear()

            # Update section heading path from lines on the page
            for ln in lines[:8]:
                lvl = _detect_heading_level(ln)
                if lvl is not None:
                    _flush_narrative_buffer()
                    headings[lvl] = ln
                    for deeper in [k for k in headings if k > lvl]:
                        headings.pop(deeper, None)

            sec_path = " > ".join(headings[k] for k in sorted(headings)) or current_volume

            # Stream A: Scanned page, Certificate page, or Commitment page needing Vision/Seal analysis
            is_scanned_or_cert = (img_count > 0 and clean_len < 160) or any(
                kw in clean_text
                for kw in ("营业执照", "资质证书", "安全生产许可证", "注册证书", "投标函", "授权委托书", "投标保证金")
            )

            if is_scanned_or_cert:
                _flush_narrative_buffer()
                vision_res: PageVisionResult = self.vision_backend.analyze_page(
                    doc=doc,
                    pdf_page=pno,
                    existing_text=clean_text,
                    heading_hint=sec_path,
                )
                effective_text = vision_res.ocr_text.strip() or clean_text or f"[{sec_path} 扫描件 第{pno}页]"
                doc_cat = forced_doc_category or vision_res.doc_category
                c_type = "certificate" if img_count > 0 and clean_len < 160 else "commitment"
                page_type_counts[c_type] += 1

                c_spans = _split_text_into_spans(
                    effective_text,
                    target_chars=self.child_target_chars,
                    overlap_chars=self.child_overlap_chars,
                )
                merged_kv = {}
                _emit_parent_and_children(
                    content_type=c_type,
                    doc_category=doc_cat,
                    section_path=sec_path,
                    p_start=pno,
                    p_end=pno,
                    parent_text=effective_text,
                    child_texts=c_spans,
                    has_seal=vision_res.has_official_seal,
                    seal_name=vision_res.seal_company_name,
                    has_sig=vision_res.has_signature,
                    kv=merged_kv,
                )
                continue

            # Stream B: Structured Table extraction
            md_tables, row_sentences = _extract_tables_as_markdown_and_rows(page, pno, sec_path)
            if md_tables:
                _flush_narrative_buffer()
                page_type_counts["table"] += 1
                doc_cat = forced_doc_category or infer_doc_category_from_text(clean_text, sec_path)
                kv = {}
                parent_tbl_text = "\n\n".join(md_tables)
                if clean_text:
                    parent_tbl_text = f"{clean_text}\n\n{parent_tbl_text}"
                _emit_parent_and_children(
                    content_type="table",
                    doc_category=doc_cat,
                    section_path=sec_path,
                    p_start=pno,
                    p_end=pno,
                    parent_text=parent_tbl_text,
                    child_texts=row_sentences if row_sentences else [clean_text],
                    has_seal=False,
                    has_sig=("签字" in clean_text or "盖章" in clean_text),
                    kv=kv,
                )
                continue

            # Stream C: Narrative paragraphs (accumulate into parent blocks ~900 chars)
            if not clean_text:
                continue

            page_type_counts["narrative"] += 1
            if nar_start_page is None:
                nar_start_page = pno
            nar_end_page = pno
            if "盖章" in clean_text or "公章" in clean_text:
                nar_has_seal = True
            if "签字" in clean_text or "法定代表人" in clean_text:
                nar_has_sig = True
            nar_buf.append(clean_text)

            if len(_compact("\n".join(nar_buf))) >= self.parent_target_chars:
                _flush_narrative_buffer()

        _flush_narrative_buffer()

        stats = {
            "source_pdf": pdf_path.name,
            "document_id": doc_id,
            "processed_pages": last_page - start_page + 1,
            "parent_chunk_count": len(parent_chunks),
            "child_chunk_count": len(child_chunks),
            "page_stream_breakdown": dict(page_type_counts),
        }
        return parent_chunks, child_chunks, stats

    @staticmethod
    def _deduplicate_subchapter_pdfs(sub_pdfs: Sequence[Path]) -> List[Path]:
        """
        Deduplicate subchapter PDFs on Windows:
        1. Case-insensitive path deduplication.
        2. Fullwidth/ASCII parenthesis normalization (e.g. '技术标(终).pdf' vs '技术标（终）.pdf').
        3. Compressed duplicate suffix filtering (e.g. 'xxx120.pdf', 'xxx6.0.pdf', 'xxx10.pdf'
           when 'xxx.pdf' already exists in the same folder).
        """
        seen_resolved = set()
        candidates: List[Path] = []
        for p in sub_pdfs:
            norm = str(p.resolve()).lower()
            if norm not in seen_resolved:
                seen_resolved.add(norm)
                candidates.append(p)

        def _canon_stem(stem: str) -> str:
            return stem.replace("（", "(").replace("）", ")").strip().lower()

        canon_stems = {_canon_stem(p.stem) for p in candidates}
        kept: List[Path] = []
        seen_canon = set()
        for p in candidates:
            c_stem = _canon_stem(p.stem)
            if c_stem in seen_canon:
                continue
            # Check if this is a DPI-compressed duplicate (e.g. stem ends with '120', '6.0', '10')
            m_comp = re.match(r"^(.+?)(?:120|6\.0|10)$", c_stem)
            if m_comp and m_comp.group(1).strip() in canon_stems:
                continue
            seen_canon.add(c_stem)
            kept.append(p)
        return kept

    def _extract_system_pages_from_merged_pdf(
        self,
        merged_pdf: Path,
        doc_id: str,
    ) -> Tuple[List[BidChunk], List[BidChunk]]:
        """
        Extract system-generated pages from the root merged bid PDF that are NOT
        saved as separate files in `分章节文件/` (e.g., electronic Bid Letter `投标函`,
        `投标诚信承诺书`, `项目经理到岗履约承诺函`, `报价文件投标函`, `投标报价汇总表`,
        `工程量清单总说明`, `第100章 总则`, `异常低价评审表`).
        """
        doc = fitz.open(str(merged_pdf))
        total_pages = len(doc)
        target_pages: List[int] = []

        # 1. Front-matter system pages (pages 1..6: Cover, TOC, Bid Letter, Integrity Commitment)
        for pno in range(1, min(7, total_pages + 1)):
            target_pages.append(pno)

        # 2. Scan native text across remaining pages for Commercial/Price volume header & summary pages
        boq_detail_count = 0
        for idx in range(6, total_pages):
            txt = doc[idx].get_text("text") or ""
            if not txt.strip():
                continue
            if any(
                kw in txt
                for kw in (
                    "（报价文件）",
                    "投标总报价",
                    "投标报价汇总表",
                    "建设项目投标报价汇总表",
                    "工程量清单总说明",
                    "异常低价评审表",
                    "投标总价扉页",
                )
            ):
                target_pages.append(idx + 1)
            elif "工程量清单表" in txt and "第100章 总则" in txt and boq_detail_count < 3:
                target_pages.append(idx + 1)
                boq_detail_count += 1
        doc.close()

        # Group contiguous target pages into ranges and parse via self.parse_pdf
        sys_parents: List[BidChunk] = []
        sys_children: List[BidChunk] = []
        if not target_pages:
            return sys_parents, sys_children

        sorted_pages = sorted(set(target_pages))
        ranges: List[Tuple[int, int]] = []
        r_start = sorted_pages[0]
        r_prev = sorted_pages[0]
        for pno in sorted_pages[1:]:
            if pno == r_prev + 1:
                r_prev = pno
            else:
                ranges.append((r_start, r_prev))
                r_start = pno
                r_prev = pno
        ranges.append((r_start, r_prev))

        for r_idx, (sp, ep) in enumerate(ranges, start=1):
            p_chunks, c_chunks, _ = self.parse_pdf(
                merged_pdf,
                document_id=f"{doc_id}_sys{r_idx:02d}",
                start_page=sp,
                end_page=ep,
            )
            sys_parents.extend(p_chunks)
            sys_children.extend(c_chunks)

        return sys_parents, sys_children

    def parse_bid_directory(
        self,
        bid_dir: Path,
        document_id: Optional[str] = None,
        prefer_subchapters: bool = True,
        max_pages_per_pdf: Optional[int] = None,
    ) -> Tuple[List[BidChunk], List[BidChunk], Dict[str, Any]]:
        """
        Smart ingestion for a project's `02_投标文件` directory.

        Automatically detects whether `分章节文件/` exists:
        - If `prefer_subchapters=True` and `分章节文件/*.pdf` exist, parses each
          deduplicated sub-chapter PDF with its exact filename-derived `doc_category`,
          AND supplements with system-generated front-matter & commercial summary
          pages from the top-level merged PDF (`投标函`, `投标报价汇总表`, etc.).
        - Otherwise, parses the merged top-level bid PDF file.
        """
        bid_dir = Path(bid_dir)
        if bid_dir.is_file() and bid_dir.suffix.lower() == ".pdf":
            return self.parse_pdf(
                bid_dir,
                document_id=document_id,
                end_page=max_pages_per_pdf,
            )

        doc_id = document_id or bid_dir.parent.name
        subchapter_dir = bid_dir / "分章节文件"
        top_pdfs = list(bid_dir.glob("*.pdf")) + list(bid_dir.glob("*.PDF"))

        if prefer_subchapters and subchapter_dir.exists():
            raw_sub_pdfs = sorted(subchapter_dir.glob("*.pdf")) + sorted(
                subchapter_dir.glob("*.PDF")
            )
            unique_sub_pdfs = self._deduplicate_subchapter_pdfs(raw_sub_pdfs)

            if unique_sub_pdfs:
                all_parents: List[BidChunk] = []
                all_children: List[BidChunk] = []

                # Supplement with system-generated Bid Letter & Price Summary pages from merged PDF
                if top_pdfs:
                    sys_p, sys_c = self._extract_system_pages_from_merged_pdf(
                        top_pdfs[0], doc_id=doc_id
                    )
                    all_parents.extend(sys_p)
                    all_children.extend(sys_c)

                for idx, sub_pdf in enumerate(unique_sub_pdfs, start=1):
                    sub_cat = infer_doc_category_from_text(sub_pdf.stem)
                    sub_vol = "技术标" if "技术标" in sub_pdf.stem else "商务与资信标"
                    p_chunks, c_chunks, _ = self.parse_pdf(
                        sub_pdf,
                        document_id=f"{doc_id}_sub{idx:02d}",
                        end_page=max_pages_per_pdf,
                        forced_volume=sub_vol,
                        forced_doc_category=sub_cat,
                    )
                    all_parents.extend(p_chunks)
                    all_children.extend(c_chunks)

                stats = {
                    "source_dir": str(bid_dir),
                    "mode": "subchapters_plus_system_pages",
                    "subchapter_files_parsed": len(unique_sub_pdfs),
                    "parent_chunk_count": len(all_parents),
                    "child_chunk_count": len(all_children),
                }
                return all_parents, all_children, stats

        # Fallback: parse top-level merged PDF
        if not top_pdfs:
            raise FileNotFoundError(f"No bid PDF found in {bid_dir}")
        return self.parse_pdf(
            top_pdfs[0],
            document_id=doc_id,
            end_page=max_pages_per_pdf,
        )


def save_chunks_jsonl(chunks: Iterable[BidChunk], output_path: Path) -> Path:
    """Write a sequence of `BidChunk` objects to a UTF-8 JSONL file."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    return output_path


def load_chunks_jsonl(input_path: Path) -> List[BidChunk]:
    """Load a list of `BidChunk` objects from a UTF-8 JSONL file."""
    input_path = Path(input_path)
    chunks: List[BidChunk] = []
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(BidChunk.from_dict(json.loads(line)))
    return chunks

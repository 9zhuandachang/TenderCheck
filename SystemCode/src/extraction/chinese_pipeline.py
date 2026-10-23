"""
Chinese Bid PDF Parsing, RAG Retrieval & Full-Bid Global Context Builder.

Connects:
1. Multi-stream Chinese Bid PDF chunking (`parse_bid_pdfs_for_rag`).
2. Category-aware BM25 / Parent-Child Retriever (`FastChineseBidRetriever`).
3. Project-wide `ProjectGlobalContext` discovery (`build_global_context_with_full_bid_scan`),
   feeding directly into `DeepSeekSlotExtractor`, `ground_extraction`,
   `hydrate_with_global_context`, and `ComplianceRuleEngine`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pymupdf as fitz

from ..evaluation.evidence import compact
from ..rag.indexer import OkapiBM25Index, _tokenize_chinese_for_bm25
from ..rag.parser import (
    BidChunk,
    _detect_heading_level,
    _extract_tables_as_markdown_and_rows,
    _split_text_into_spans,
)
from ..rag.passages import category_search_policy
from ..rag.vision import infer_doc_category_from_text
from .global_context import ProjectGlobalContext, build_project_global_context

logger = logging.getLogger(__name__)


def parse_bid_pdfs_for_rag(
    bid_docs: Sequence[Dict[str, Any]],
    progress: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[List[BidChunk], Dict[str, BidChunk], Dict[str, Dict[str, Any]]]:
    """
    Parse one or more uploaded bid PDFs into Parent and Child `BidChunk` objects
    (preserving both narrative text and PyMuPDF structured tables) and build a
    `doc_sources` map compatible with `real_backend.format_backend_result`.
    """
    parent_chunks: List[BidChunk] = []
    child_chunks: List[BidChunk] = []
    doc_sources: Dict[str, Dict[str, Any]] = {}

    total_docs = len(bid_docs)
    for d_idx, doc_meta in enumerate(bid_docs, start=1):
        pdf_path = Path(doc_meta["path"])
        doc_id = str(doc_meta.get("doc_id") or f"bid_doc_{doc_meta.get('id', d_idx)}")
        label = str(doc_meta.get("label") or pdf_path.name)
        if not pdf_path.exists():
            continue

        pdf = fitz.open(str(pdf_path))
        num_pages = len(pdf)
        doc_sources[doc_id] = {
            "path": str(pdf_path),
            "label": label,
            "filename": pdf_path.name,
            "pages": num_pages,
            "exists": True,
        }

        current_volume = "商务与资信标"
        current_section = label
        table_pages_extracted = 0
        for p_idx in range(num_pages):
            pno = p_idx + 1
            if progress and (pno == 1 or pno % 50 == 0 or pno == num_pages):
                progress(
                    f"正在解析投标文件 {label} 第 {pno}/{num_pages} 页...",
                    d_idx - 1,
                    total_docs,
                )

            page = pdf[p_idx]
            raw_text = (page.get_text("text") or "").strip()
            if not raw_text:
                continue

            compact_head = compact(raw_text[:250])
            if "施工组织设计" in compact_head or "技术文件" in compact_head:
                current_volume = "技术标"
            elif "报价文件" in compact_head or "已标价工程量清单" in compact_head or "投标报价汇总表" in compact_head:
                current_volume = "报价标"
            elif "商务文件" in compact_head or "资格审查" in compact_head:
                current_volume = "商务与资信标"

            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            for ln in lines[:6]:
                if _detect_heading_level(ln) is not None:
                    current_section = ln[:45]
                    break

            doc_cat = infer_doc_category_from_text(raw_text, current_section)

            md_tables: List[str] = []
            row_statements: List[str] = []
            if (
                table_pages_extracted < 35
                and any(
                    k in raw_text
                    for k in (
                        "基本情况表",
                        "人员组成表",
                        "简历表",
                        "信誉情况表",
                        "投标报价汇总表",
                        "类似项目情况表",
                        "拟委任",
                    )
                )
                and "单价分析" not in raw_text
            ):
                md_tables, row_statements = _extract_tables_as_markdown_and_rows(page, pno, current_section)
                if md_tables:
                    table_pages_extracted += 1

            combined_parent_text = raw_text
            if md_tables:
                combined_parent_text = raw_text + "\n" + "\n".join(md_tables)

            parent_id = f"{doc_id}_P{pno:04d}"
            p_chunk = BidChunk(
                chunk_id=parent_id,
                parent_id=parent_id,
                is_parent=True,
                document_id=doc_id,
                volume_type=current_volume,
                doc_category=doc_cat,
                content_type="table" if md_tables else "narrative",
                section_path=current_section,
                page_start=pno,
                page_end=pno,
                printed_page_start=pno,
                printed_page_end=pno,
                text=combined_parent_text,
                embedding_text=f"[{doc_cat} | {current_section}] {combined_parent_text}",
            )
            parent_chunks.append(p_chunk)

            spans = _split_text_into_spans(raw_text, target_chars=320, max_chars=460, overlap_chars=50)
            for s_idx, sp in enumerate(spans, start=1):
                c_id = f"{parent_id}_C{s_idx:02d}"
                child_chunks.append(
                    BidChunk(
                        chunk_id=c_id,
                        parent_id=parent_id,
                        is_parent=False,
                        document_id=doc_id,
                        volume_type=current_volume,
                        doc_category=doc_cat,
                        content_type="narrative",
                        section_path=current_section,
                        page_start=pno,
                        page_end=pno,
                        printed_page_start=pno,
                        printed_page_end=pno,
                        text=sp,
                        embedding_text=f"[{doc_cat} | {current_section}] {sp}",
                    )
                )

            for r_idx, r_stmt in enumerate(row_statements, start=1):
                c_id = f"{parent_id}_T{r_idx:02d}"
                child_chunks.append(
                    BidChunk(
                        chunk_id=c_id,
                        parent_id=parent_id,
                        is_parent=False,
                        document_id=doc_id,
                        volume_type=current_volume,
                        doc_category=doc_cat,
                        content_type="table_row",
                        section_path=current_section,
                        page_start=pno,
                        page_end=pno,
                        printed_page_start=pno,
                        printed_page_end=pno,
                        text=r_stmt,
                        embedding_text=r_stmt,
                    )
                )

        pdf.close()

    parent_map = {p.chunk_id: p for p in parent_chunks}
    return child_chunks, parent_map, doc_sources


class FastChineseBidRetriever:
    """
    Category-aware Chinese BM25 Retriever with inverted index and Parent-Child deduplication.
    """

    def __init__(
        self,
        child_chunks: List[BidChunk],
        parent_map: Dict[str, BidChunk],
        doc_sources: Dict[str, Dict[str, Any]],
    ) -> None:
        self.child_chunks = child_chunks
        self.parent_map = parent_map
        self.doc_sources = doc_sources
        self.bm25 = OkapiBM25Index()
        self.postings: Dict[str, List[Tuple[int, float]]] = {}
        if self.child_chunks:
            self.bm25.fit([c.embedding_text for c in self.child_chunks])
            k1 = self.bm25.k1
            b = self.bm25.b
            avgdl = max(self.bm25.avgdl, 1e-6)
            for doc_idx, tf_map in enumerate(self.bm25.doc_term_freqs):
                dl = float(self.bm25.doc_lens[doc_idx])
                norm_denom = k1 * (1.0 - b + b * (dl / avgdl))
                for term, tf in tf_map.items():
                    idf_val = self.bm25.idf.get(term, 0.0)
                    if idf_val <= 0.0:
                        continue
                    w = idf_val * (tf * (k1 + 1.0)) / (tf + norm_denom)
                    self.postings.setdefault(term, []).append((doc_idx, w))

    def retrieve_passages(
        self,
        requirement_text: str,
        category: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if not self.child_chunks:
            return []

        policy = category_search_policy(requirement_text, category)
        query = policy["query"]
        filter_fn = policy["filter_fn"]

        scores = np.zeros(len(self.child_chunks), dtype=np.float32)
        for term in set(_tokenize_chinese_for_bm25(query)):
            for doc_idx, w in self.postings.get(term, ()):
                scores[doc_idx] += w

        valid_indices = [
            i
            for i, c in enumerate(self.child_chunks)
            if filter_fn is None or bool(filter_fn(c))
        ]
        if not valid_indices:
            valid_indices = list(range(len(self.child_chunks)))

        valid_indices.sort(key=lambda idx: float(scores[idx]), reverse=True)
        seen_parents = set()
        page_to_passage: Dict[Tuple[str, int], Dict[str, Any]] = {}
        passages: List[Dict[str, Any]] = []

        for idx in valid_indices:
            child = self.child_chunks[idx]
            if child.parent_id in seen_parents:
                continue
            seen_parents.add(child.parent_id)
            parent = self.parent_map.get(child.parent_id, child)
            page_key = (parent.document_id, int(parent.page_start))
            if page_key in page_to_passage:
                existing_p = page_to_passage[page_key]
                if parent.text and parent.text not in existing_p["text"]:
                    existing_p["text"] = existing_p["text"] + "\n" + parent.text
                continue
            src_info = self.doc_sources.get(parent.document_id, {})
            eid = f"E{len(passages) + 1}"
            p_entry = {
                "evidence_id": eid,
                "rank": len(passages) + 1,
                "retrieval_score": round(float(scores[idx]), 4),
                "score": round(float(scores[idx]), 4),
                "document_id": parent.document_id,
                "source": src_info.get("path", ""),
                "source_sha256": "",
                "chunk_id": child.chunk_id,
                "parent_id": parent.chunk_id,
                "page_start": parent.page_start,
                "page_end": parent.page_end,
                "doc_category": parent.doc_category,
                "section_path": parent.section_path,
                "text": parent.text,
                "child_text": child.text,
            }
            page_to_passage[page_key] = p_entry
            passages.append(p_entry)
            if len(passages) >= top_k:
                break

        return passages


def build_global_context_with_full_bid_scan(
    cases: List[Dict[str, Any]],
    parent_map: Dict[str, BidChunk],
    doc_sources: Dict[str, Dict[str, Any]],
) -> ProjectGlobalContext:
    """
    Build `ProjectGlobalContext` from `cases` plus a full scan of anchor pages across
    `parent_map` so canonical bid anchors (Commercial/Price Bid Letter, BOQ Summary,
    Integrity Commitment, Deposit Receipt, Business License, Enterprise/PM Certificates,
    Personnel Table) are always discovered regardless of local Top-K retrieval.
    """
    anchor_keywords = (
        "投标函",
        "投标报价汇总表",
        "工程量清单表",
        "工程量清单总说明",
        "投标诚信承诺书",
        "投标保证金",
        "统一延续",
        "统一延期",
        "建筑业企业资质证书",
        "营业执照",
        "全国公路建设市场信用信息管理系统",
        "单位参保证明",
        "社会保险",
        "一级建造师注册证书",
        "安全生产考核合格证书",
        "高级工程师",
        "投标人名称",
        "项目管理机构人员组成表",
        "拟委任的其他管理和技术人员汇总表",
        "拟委任的其他管理和技术人员简历表",
        "第一章总体施工组织布置及规划",
        "第四章工程质量管理体系及保证措施",
        "第八章项目风险预测与防范、事故应急预案",
        "第九章资源配备计划",
        "第四节施工场地布置",
        "第三节施工进度计划",
    )
    extra_passages: List[Dict[str, Any]] = []
    for p_chunk in parent_map.values():
        txt = p_chunk.text
        if any(kw in txt for kw in anchor_keywords):
            src_info = doc_sources.get(p_chunk.document_id, {})
            extra_passages.append(
                {
                    "evidence_id": f"G_{p_chunk.chunk_id}",
                    "document_id": p_chunk.document_id,
                    "source": src_info.get("path", ""),
                    "source_sha256": "",
                    "page_start": p_chunk.page_start,
                    "page_end": p_chunk.page_end,
                    "doc_category": p_chunk.doc_category,
                    "section_path": p_chunk.section_path,
                    "text": txt,
                }
            )

    augmented_cases = list(cases)
    if extra_passages:
        augmented_cases.append(
            {
                "clause_id": "__GLOBAL_ANCHOR_SCAN__",
                "requirement": "",
                "category": "Document",
                "evidence_context": json.dumps(extra_passages, ensure_ascii=False),
            }
        )
    return build_project_global_context(augmented_cases)

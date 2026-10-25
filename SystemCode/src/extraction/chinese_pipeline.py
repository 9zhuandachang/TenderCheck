"""
Chinese Bid PDF Parsing, RAG Retrieval & Full-Bid Global Context Builder.

Connects:
1. Multi-stream Chinese Bid PDF chunking (`parse_bid_pdfs_for_rag`) with automatic
   OCR & red-seal fallback (`LocalHeuristicOCRBackend`) for scanned certificate/contract pages.
2. Category-aware BM25 / Parent-Child Retriever (`FastChineseBidRetriever`).
3. Project-wide `ProjectGlobalContext` discovery (`build_global_context_with_full_bid_scan`),
   feeding directly into `DeepSeekSlotExtractor`, `ground_extraction`,
   `hydrate_with_global_context`, and `ComplianceRuleEngine`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pymupdf as fitz

from ..evaluation.evidence import compact
from ..rag.indexer import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER_MODEL,
    HybridBidIndex,
    OkapiBM25Index,
    _tokenize_chinese_for_bm25,
)
from ..rag.parser import (
    BidChunk,
    _detect_heading_level,
    _extract_tables_as_markdown_and_rows,
    _split_text_into_spans,
    load_chunks_jsonl,
    save_chunks_jsonl,
)
from ..rag.passages import category_search_policy
from ..rag.vision import (
    BaseVisionBackend,
    DiskCachedVisionBackend,
    LocalHeuristicOCRBackend,
    QwenVLBackend,
    infer_doc_category_from_text,
)
from .global_context import ProjectGlobalContext, build_project_global_context

logger = logging.getLogger(__name__)

SYSTEMCODE_DIR = Path(__file__).resolve().parents[2]
LOCAL_BGE_M3_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "bge-m3"
LOCAL_BGE_RERANKER_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "bge-reranker-v2-m3"
LOCAL_QWEN_VL_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "Qwen2.5-VL-7B-Instruct"


def _build_vision_backend(cache_dir: Optional[Path] = None) -> BaseVisionBackend:
    """
    Build a cached vision/OCR backend for scanned bid pages:
    - Uses `QwenVLBackend` (`Qwen2.5-VL-7B-Instruct`) when `TENDERCHECK_USE_QWEN_VL=1`
      and the local pretrained directory or OpenAI-compatible VLM server is available.
    - Otherwise uses `LocalHeuristicOCRBackend` (`RapidOCR` ONNX Runtime + RGB red-seal detector).
    - Always wraps in `DiskCachedVisionBackend` so scanned pages are OCR'd once and persisted.
    """
    import os

    use_qwen_vl = os.environ.get("TENDERCHECK_USE_QWEN_VL", "").strip() in ("1", "true", "True")
    if use_qwen_vl and LOCAL_QWEN_VL_DIR.exists():
        delegate: BaseVisionBackend = QwenVLBackend(
            model_name_or_path=str(LOCAL_QWEN_VL_DIR),
            mode="transformers",
            dpi=150,
        )
    else:
        delegate = LocalHeuristicOCRBackend(dpi=150)

    effective_cache = cache_dir or (SYSTEMCODE_DIR / "data" / "vision_cache")
    return DiskCachedVisionBackend(delegate=delegate, cache_dir=effective_cache)


def parse_bid_pdfs_for_rag(
    bid_docs: Sequence[Dict[str, Any]],
    progress: Optional[Callable[[str, int, int], None]] = None,
    cache_dir: Optional[Path] = None,
) -> Tuple[List[BidChunk], Dict[str, BidChunk], Dict[str, Dict[str, Any]]]:
    """
    Parse one or more uploaded bid PDFs into Parent and Child `BidChunk` objects
    (preserving narrative text, PyMuPDF structured tables, and cached OCR/VLM + red-seal
    recognition on all scanned/certificate pages) and build a `doc_sources` map.
    """
    parent_chunks: List[BidChunk] = []
    child_chunks: List[BidChunk] = []
    doc_sources: Dict[str, Dict[str, Any]] = {}
    vision_backend: Optional[BaseVisionBackend] = None

    total_docs = len(bid_docs)
    for d_idx, doc_meta in enumerate(bid_docs, start=1):
        pdf_path = Path(doc_meta["path"])
        doc_id = str(doc_meta.get("doc_id") or f"bid_doc_{doc_meta.get('id', d_idx)}")
        label = str(doc_meta.get("label") or pdf_path.name)
        if not pdf_path.exists():
            continue

        pdf = fitz.open(str(pdf_path))
        num_pages = len(pdf)
        stat = pdf_path.stat()
        file_sha = str(
            doc_meta.get("sha256")
            or hashlib.sha256(f"{pdf_path.name}:{stat.st_size}".encode("utf-8")).hexdigest()
        )
        doc_sources[doc_id] = {
            "path": str(pdf_path),
            "label": label,
            "filename": pdf_path.name,
            "pages": num_pages,
            "sha256": file_sha,
            "exists": True,
        }

        current_volume = "商务与资信标"
        current_section = label
        for p_idx in range(num_pages):
            pno = p_idx + 1
            if progress and (pno == 1 or pno % 25 == 0 or pno == num_pages):
                progress(
                    f"正在解析投标文件 {label} 第 {pno}/{num_pages} 页...",
                    pno,
                    num_pages,
                )

            page = pdf[p_idx]
            raw_text = (page.get_text("text") or "").strip()
            compact_len = len(compact(raw_text))
            img_count = len(page.get_images(full=True))
            has_seal = False
            seal_company: Optional[str] = None
            has_sig = False

            # Route scanned pages & image-based certificates through Vision/OCR backend
            if compact_len < 60 or (img_count > 0 and compact_len < 140):
                try:
                    if vision_backend is None:
                        vision_backend = _build_vision_backend(
                            cache_dir=(cache_dir / "vision_cache") if cache_dir else None
                        )
                    if progress:
                        progress(
                            f"正在对扫描件/证书页 ({label} 第 {pno}/{num_pages} 页) 执行 OCR 与红章识别...",
                            pno,
                            num_pages,
                        )
                    vis_res = vision_backend.analyze_page(
                        pdf, pno, existing_text=raw_text, heading_hint=current_section
                    )
                    ocr_txt = (vis_res.ocr_text or "").strip()
                    if ocr_txt and ocr_txt not in raw_text:
                        raw_text = f"{raw_text}\n{ocr_txt}".strip() if raw_text else ocr_txt
                    has_seal = bool(vis_res.has_official_seal)
                    seal_company = vis_res.seal_company_name
                    has_sig = bool(vis_res.has_signature)
                except Exception as exc:
                    logger.debug("OCR fallback on %s page %d skipped: %s", pdf_path.name, pno, exc)

            if not raw_text:
                raw_text = f"[{current_section} 扫描页 第{pno}页]"

            if not has_seal and re.search(r"盖(?:单位)?(?:电子)?(?:公|印)?章", raw_text):
                has_seal = True
            if not has_sig and re.search(
                r"(?:法定代表人|授权委托人|委托代理人|项目经理)\s*[：:]\s*\S+|签字|盖电子印章",
                raw_text,
            ):
                has_sig = True

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
                any(
                    k in raw_text
                    for k in (
                        "基本情况表",
                        "人员组成表",
                        "汇总表",
                        "简历表",
                        "信誉情况表",
                        "投标报价汇总表",
                        "工程量清单表",
                        "类似项目情况表",
                        "拟委任",
                        "财务状况",
                    )
                )
                and "单价分析" not in raw_text
            ):
                md_tables, row_statements = _extract_tables_as_markdown_and_rows(page, pno, current_section)

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
                has_official_seal=has_seal,
                seal_company_name=seal_company,
                has_signature=has_sig,
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
                        has_official_seal=has_seal,
                        seal_company_name=seal_company,
                        has_signature=has_sig,
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
                        has_official_seal=has_seal,
                        seal_company_name=seal_company,
                        has_signature=has_sig,
                    )
                )

        pdf.close()

    parent_map = {p.chunk_id: p for p in parent_chunks}
    return child_chunks, parent_map, doc_sources


class FastChineseBidRetriever:
    """
    Unified Hybrid Dense (`bge-m3`) + Sparse (`OkapiBM25`) + RRF + Cross-Encoder
    (`bge-reranker-v2-m3`) Retriever with Parent-Child deduplication and disk caching.
    Matches the exact `HybridBidIndex` retrieval architecture used on S604.
    """

    def __init__(
        self,
        child_chunks: List[BidChunk],
        parent_map: Dict[str, BidChunk],
        doc_sources: Dict[str, Dict[str, Any]],
        index_dir: Optional[Path] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> None:
        self.child_chunks = child_chunks
        self.parent_map = parent_map
        self.doc_sources = doc_sources
        self.index_dir = Path(index_dir) if index_dir else None

        emb_model = str(LOCAL_BGE_M3_DIR) if LOCAL_BGE_M3_DIR.exists() else DEFAULT_EMBEDDING_MODEL
        rerank_model = str(LOCAL_BGE_RERANKER_DIR) if LOCAL_BGE_RERANKER_DIR.exists() else None
        self.hybrid_index = HybridBidIndex(
            embedding_model_name=emb_model,
            reranker_model_name=rerank_model,
        )

        self.bm25 = OkapiBM25Index()
        self.postings: Dict[str, List[Tuple[int, float]]] = {}
        self.dense_embeddings: Optional[np.ndarray] = None

        if self.child_chunks:
            corpus = [c.embedding_text for c in self.child_chunks]
            self.bm25.fit(corpus)
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

            # Load or build Dense Embeddings (bge-m3) & persist in project rag_index directory
            dense_cache_path = (self.index_dir / "dense_embeddings.npy") if self.index_dir else None
            loaded_dense = False
            if dense_cache_path and dense_cache_path.exists():
                try:
                    arr = np.load(dense_cache_path)
                    if arr.shape[0] == len(self.child_chunks):
                        self.dense_embeddings = arr
                        self.hybrid_index.dense_embeddings = arr
                        loaded_dense = True
                except Exception:
                    loaded_dense = False

            if not loaded_dense and LOCAL_BGE_M3_DIR.exists():
                try:
                    if progress:
                        progress(
                            f"正在使用 bge-m3 稠密向量模型构建投标书混合索引 ({len(corpus)} 个文本块)...",
                            0,
                            len(corpus),
                        )
                    self.dense_embeddings = self.hybrid_index._encode_neural(corpus, batch_size=32)
                    self.hybrid_index.dense_embeddings = self.dense_embeddings
                    if self.index_dir:
                        self.index_dir.mkdir(parents=True, exist_ok=True)
                        save_chunks_jsonl(self.parent_map.values(), self.index_dir / "parent_chunks.jsonl")
                        save_chunks_jsonl(self.child_chunks, self.index_dir / "child_chunks.jsonl")
                        np.save(self.index_dir / "dense_embeddings.npy", self.dense_embeddings)
                        (self.index_dir / "document_sources.json").write_text(
                            json.dumps(self.doc_sources, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        (self.index_dir / "index_meta.json").write_text(
                            json.dumps(
                                {
                                    "embedding_model_name": emb_model,
                                    "reranker_model_name": rerank_model,
                                    "num_parents": len(self.parent_map),
                                    "num_children": len(self.child_chunks),
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                except Exception as exc:
                    logger.warning("Dense embedding build skipped (%s); falling back to BM25.", exc)

    def _sparse_scores(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.child_chunks), dtype=np.float32)
        for term in set(_tokenize_chinese_for_bm25(query)):
            for doc_idx, w in self.postings.get(term, ()):
                scores[doc_idx] += w
        return scores

    def _format_ranked_passages(
        self,
        ranked_child_indices: List[int],
        score_map: Dict[int, float],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        seen_parents = set()
        page_to_passage: Dict[Tuple[str, int], Dict[str, Any]] = {}
        passages: List[Dict[str, Any]] = []

        for idx in ranked_child_indices:
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
                    existing_p["parent_text"] = existing_p["text"]
                continue
            src_info = self.doc_sources.get(parent.document_id, {})
            eid = f"E{len(passages) + 1}"
            sc = round(float(score_map.get(idx, 0.0)), 4)
            p_entry = {
                "evidence_id": eid,
                "rank": len(passages) + 1,
                "retrieval_score": sc,
                "score": sc,
                "document_id": parent.document_id,
                "source": src_info.get("path", ""),
                "source_sha256": src_info.get("sha256", ""),
                "chunk_id": child.chunk_id,
                "parent_id": parent.chunk_id,
                "page_start": parent.page_start,
                "page_end": parent.page_end,
                "doc_category": parent.doc_category,
                "section_path": parent.section_path,
                "text": parent.text,
                "parent_text": parent.text,
                "child_text": child.text,
                "has_official_seal": bool(parent.has_official_seal),
                "seal_company_name": parent.seal_company_name,
                "has_signature": bool(parent.has_signature),
            }
            page_to_passage[page_key] = p_entry
            passages.append(p_entry)
            if len(passages) >= top_k:
                break
        return passages

    def retrieve_passages(
        self,
        requirement_text: str,
        category: str,
        top_k: int = 5,
        precomputed_query_vec: Optional[np.ndarray] = None,
    ) -> List[Dict[str, Any]]:
        if not self.child_chunks:
            return []

        policy = category_search_policy(requirement_text, category)
        query = policy["query"]
        filter_fn = policy["filter_fn"]
        dense_w = float(policy.get("dense_weight", 0.5))
        sparse_w = float(policy.get("sparse_weight", 0.5))
        rrf_k = 60

        sparse_all = self._sparse_scores(query)
        valid_indices = [
            i
            for i, c in enumerate(self.child_chunks)
            if filter_fn is None or bool(filter_fn(c))
        ]
        if not valid_indices:
            valid_indices = list(range(len(self.child_chunks)))

        if self.dense_embeddings is not None and dense_w > 0.0:
            if precomputed_query_vec is None:
                precomputed_query_vec = self.hybrid_index._encode_neural([query])[0]
            dense_all = np.dot(self.dense_embeddings, precomputed_query_vec).astype(np.float32)

            v_arr = np.array(valid_indices, dtype=np.int32)
            sub_dense = dense_all[v_arr]
            sub_sparse = sparse_all[v_arr]

            dense_order = np.argsort(-sub_dense)
            sparse_order = np.argsort(-sub_sparse)

            dense_ranks = np.empty_like(dense_order)
            dense_ranks[dense_order] = np.arange(1, len(v_arr) + 1)

            sparse_ranks = np.empty_like(sparse_order)
            sparse_ranks[sparse_order] = np.arange(1, len(v_arr) + 1)

            rrf_scores = (dense_w / (rrf_k + dense_ranks)) + (sparse_w / (rrf_k + sparse_ranks))
            sorted_pos = np.argsort(-rrf_scores)
            ranked_indices = [int(v_arr[pos]) for pos in sorted_pos]
            score_map = {int(v_arr[pos]): float(rrf_scores[pos]) for pos in sorted_pos}
        else:
            valid_indices.sort(key=lambda idx: float(sparse_all[idx]), reverse=True)
            ranked_indices = valid_indices
            score_map = {idx: float(sparse_all[idx]) for idx in valid_indices}

        return self._format_ranked_passages(ranked_indices, score_map, top_k=top_k)

    def retrieve_all_clauses(
        self,
        reqs: Sequence[Dict[str, Any]],
        top_k: int = 5,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        Batch-encode all clause queries with `bge-m3` on GPU and execute Hybrid RRF
        (Dense + Okapi BM25 + Category Policy) across all clauses.
        """
        if not reqs or not self.child_chunks:
            return {}

        queries = []
        for r in reqs:
            r_text = str(r.get("text") or "")
            r_cat = str(r.get("category") or "Document")
            queries.append(category_search_policy(r_text, r_cat)["query"])

        query_vecs: Optional[np.ndarray] = None
        if self.dense_embeddings is not None:
            try:
                if progress:
                    progress(f"正在使用 bge-m3 批量编码 {len(queries)} 条招标条款查询向量...", 0, len(queries))
                query_vecs = self.hybrid_index._encode_neural(queries, batch_size=64)
            except Exception as exc:
                logger.warning("Batched query encoding failed (%s); using BM25.", exc)
                query_vecs = None

        passages_by_req_id: Dict[int, List[Dict[str, Any]]] = {}
        for idx, r in enumerate(reqs):
            rid = int(r["id"])
            r_text = str(r.get("text") or "")
            r_cat = str(r.get("category") or "Document")
            q_vec = query_vecs[idx] if query_vecs is not None else None
            passages_by_req_id[rid] = self.retrieve_passages(
                r_text, r_cat, top_k=top_k, precomputed_query_vec=q_vec
            )
        return passages_by_req_id


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
        "投标人一般情况表",
        "企业类似业绩表",
        "拟投入项目管理机构组成表",
        "投标人承诺",
        "承诺书",
        "报价文件",
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
                    "source_sha256": src_info.get("sha256", ""),
                    "page_start": p_chunk.page_start,
                    "page_end": p_chunk.page_end,
                    "doc_category": p_chunk.doc_category,
                    "section_path": p_chunk.section_path,
                    "text": txt,
                    "parent_text": txt,
                    "has_official_seal": bool(p_chunk.has_official_seal),
                    "seal_company_name": p_chunk.seal_company_name,
                    "has_signature": bool(p_chunk.has_signature),
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

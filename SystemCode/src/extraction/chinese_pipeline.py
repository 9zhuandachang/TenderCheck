"""
Chinese Bid PDF Parsing, Unified RAG Retrieval & Full-Bid Global Context Builder.

Single canonical execution trunk unifying online Web UI and offline `src/rag/` components:
1. Multi-stream Chinese Bid PDF chunking (`parse_bid_pdfs_for_rag`) delegating directly to
   `src.rag.parser.MultimodalBidParser` (preserving narrative text, structured tables, and
   verified Vision/OCR seal & signature metadata without text-only fake seal guesses).
2. Unified Hybrid Retriever (`FastChineseBidRetriever`) delegating directly to
   `src.rag.indexer.HybridBidIndex` (`OkapiBM25` + `bge-m3` + RRF + `bge-reranker-v2-m3`).
3. Project-wide `ProjectGlobalContext` discovery (`build_global_context_with_full_bid_scan`),
   feeding directly into `DeepSeekSlotExtractor`, `ground_extraction`,
   `hydrate_with_global_context`, and `ComplianceRuleEngine`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pymupdf as fitz

from ..rag.indexer import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER_MODEL,
    HybridBidIndex,
    RetrievedChunkHit,
    resolve_local_or_hf_model,
)
from ..rag.parser import BidChunk, BidDocumentParser
from ..rag.passages import category_search_policy
from ..rag.vision import (
    BaseVisionBackend,
    DiskCachedVisionBackend,
    LocalHeuristicOCRBackend,
    QwenVLBackend,
)
from .global_context import ProjectGlobalContext, build_project_global_context

logger = logging.getLogger(__name__)

SYSTEMCODE_DIR = Path(__file__).resolve().parents[2]
LOCAL_BGE_M3_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "bge-m3"
LOCAL_BGE_RERANKER_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "bge-reranker-v2-m3"
LOCAL_QWEN_VL_DIR = SYSTEMCODE_DIR / "models" / "pretrained" / "Qwen2.5-VL-7B-Instruct"


def _build_vision_backend(cache_dir: Optional[Path] = None) -> BaseVisionBackend:
    """
    Build a cached vision/OCR backend for scanned, certificate, and seal-bearing bid pages:
    - Uses `QwenVLBackend` (`Qwen2.5-VL-7B-Instruct`) when `TENDERCHECK_USE_QWEN_VL=1`
      and either the local pretrained directory or an OpenAI-compatible VLM server (`TENDERCHECK_VLM_BASE_URL`) is configured.
    - Otherwise uses `LocalHeuristicOCRBackend` (`RapidOCR` ONNX Runtime + RGB red-seal detector).
    - Always wraps in `DiskCachedVisionBackend` so scanned pages are analyzed once and persisted.
    """
    use_qwen_vl = os.environ.get("TENDERCHECK_USE_QWEN_VL", "").strip() in ("1", "true", "True")
    vlm_base_url = os.environ.get("TENDERCHECK_VLM_BASE_URL", "").strip()
    if use_qwen_vl and vlm_base_url:
        delegate: BaseVisionBackend = QwenVLBackend(
            model_name_or_path=os.environ.get("TENDERCHECK_VLM_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct"),
            mode="api",
            api_base_url=vlm_base_url,
            dpi=150,
        )
    elif use_qwen_vl and LOCAL_QWEN_VL_DIR.exists():
        delegate = QwenVLBackend(
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
    vision_backend: Optional[BaseVisionBackend] = None,
) -> Tuple[List[BidChunk], Dict[str, BidChunk], Dict[str, Dict[str, Any]]]:
    """
    Parse one or more uploaded bid PDFs into Parent and Child `BidChunk` objects
    via the canonical `src.rag.parser.MultimodalBidParser` and build a `doc_sources` map.
    """
    effective_vision = vision_backend or _build_vision_backend(
        cache_dir=(cache_dir / "vision_cache") if cache_dir else None
    )
    parser = BidDocumentParser(vision_backend=effective_vision)

    parent_chunks: List[BidChunk] = []
    child_chunks: List[BidChunk] = []
    doc_sources: Dict[str, Dict[str, Any]] = {}

    for d_idx, doc_meta in enumerate(bid_docs, start=1):
        pdf_path = Path(doc_meta["path"])
        doc_id = str(doc_meta.get("doc_id") or f"bid_doc_{doc_meta.get('id', d_idx)}")
        label = str(doc_meta.get("label") or pdf_path.name)
        if not pdf_path.exists():
            continue

        stat = pdf_path.stat()
        file_sha = str(
            doc_meta.get("sha256")
            or hashlib.sha256(f"{pdf_path.name}:{stat.st_size}".encode("utf-8")).hexdigest()
        )
        with fitz.open(str(pdf_path)) as pdf:
            num_pages = len(pdf)

        doc_sources[doc_id] = {
            "path": str(pdf_path),
            "label": label,
            "filename": pdf_path.name,
            "pages": num_pages,
            "sha256": file_sha,
            "exists": True,
        }

        doc_parents, doc_children, _ = parser.parse_pdf(
            pdf_path=pdf_path,
            document_id=doc_id,
            progress=progress,
            label=label,
        )
        parent_chunks.extend(doc_parents)
        child_chunks.extend(doc_children)

    parent_map = {p.chunk_id: p for p in parent_chunks}
    return child_chunks, parent_map, doc_sources


class FastChineseBidRetriever:
    """
    Unified Hybrid Dense (`bge-m3`) + Sparse (`OkapiBM25`) + RRF + Cross-Encoder
    (`bge-reranker-v2-m3`) Retriever backed directly by `src.rag.indexer.HybridBidIndex`.
    """

    def __init__(
        self,
        child_chunks: List[BidChunk],
        parent_map: Dict[str, BidChunk],
        doc_sources: Dict[str, Dict[str, Any]],
        index_dir: Optional[Path] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
        use_reranker: Optional[bool] = None,
    ) -> None:
        self.child_chunks = child_chunks
        self.parent_map = parent_map
        self.doc_sources = doc_sources
        self.index_dir = Path(index_dir) if index_dir else None

        emb_model = resolve_local_or_hf_model("bge-m3", DEFAULT_EMBEDDING_MODEL)
        rerank_model = (
            resolve_local_or_hf_model("bge-reranker-v2-m3", DEFAULT_RERANKER_MODEL)
            if (use_reranker is True or (use_reranker is None and LOCAL_BGE_RERANKER_DIR.exists()))
            else None
        )
        self.use_reranker = bool(rerank_model)

        self.hybrid_index = HybridBidIndex(
            embedding_model_name=emb_model,
            reranker_model_name=rerank_model,
        )

        if self.child_chunks:
            self.hybrid_index.build(
                parent_chunks=list(self.parent_map.values()),
                child_chunks=self.child_chunks,
                build_dense=False,
            )

            dense_cache_path = (self.index_dir / "dense_embeddings.npy") if self.index_dir else None
            loaded_dense = False
            if dense_cache_path and dense_cache_path.exists():
                try:
                    arr = np.load(dense_cache_path)
                    if arr.shape[0] == len(self.child_chunks):
                        self.hybrid_index.dense_embeddings = arr
                        loaded_dense = True
                except Exception:
                    loaded_dense = False

            if not loaded_dense and LOCAL_BGE_M3_DIR.exists():
                try:
                    corpus = [c.embedding_text for c in self.child_chunks]
                    if progress:
                        progress(
                            f"正在使用 bge-m3 稠密向量模型构建投标书混合索引 ({len(corpus)} 个文本块)...",
                            0,
                            len(corpus),
                        )
                    self.hybrid_index.dense_embeddings = self.hybrid_index._encode_neural(corpus, batch_size=32)
                    if self.index_dir:
                        self.hybrid_index.save(self.index_dir)
                        (self.index_dir / "document_sources.json").write_text(
                            json.dumps(self.doc_sources, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                except Exception as exc:
                    logger.warning("Dense embedding build skipped (%s); falling back to BM25.", exc)

    @property
    def dense_embeddings(self) -> Optional[np.ndarray]:
        return self.hybrid_index.dense_embeddings

    def _format_hits_as_passages(
        self,
        hits: Sequence[RetrievedChunkHit],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        page_to_passage: Dict[Tuple[str, int], Dict[str, Any]] = {}
        passages: List[Dict[str, Any]] = []

        for hit in hits:
            parent = hit.parent_chunk
            child = hit.child_chunk
            page_key = (parent.document_id, int(parent.page_start))
            if page_key in page_to_passage:
                existing_p = page_to_passage[page_key]
                if parent.text and parent.text not in existing_p["text"]:
                    existing_p["text"] = existing_p["text"] + "\n" + parent.text
                    existing_p["parent_text"] = existing_p["text"]
                if parent.visual_seal_verified and not existing_p.get("visual_seal_verified"):
                    existing_p["visual_seal_verified"] = True
                    existing_p["visual_backend"] = parent.visual_backend
                    existing_p["has_official_seal"] = bool(parent.has_official_seal)
                    existing_p["seal_company_name"] = parent.seal_company_name
                    existing_p["has_signature"] = bool(parent.has_signature)
                continue

            src_info = self.doc_sources.get(parent.document_id, {})
            eid = f"E{len(passages) + 1}"
            sc = round(float(hit.score), 4)
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
                "visual_seal_verified": bool(parent.visual_seal_verified),
                "visual_backend": parent.visual_backend,
                "has_official_seal": bool(parent.has_official_seal) if parent.visual_seal_verified else None,
                "seal_company_name": parent.seal_company_name if parent.visual_seal_verified else None,
                "has_signature": bool(parent.has_signature) if parent.visual_seal_verified else None,
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
        hits = self.hybrid_index.search(
            query=policy["query"],
            top_k=max(top_k * 2, top_k),
            filter_fn=policy["filter_fn"],
            dense_weight=float(policy.get("dense_weight", 0.5)),
            sparse_weight=float(policy.get("sparse_weight", 0.5)),
            rrf_k=60,
            use_reranker=self.use_reranker,
            precomputed_query_vec=precomputed_query_vec,
        )
        return self._format_hits_as_passages(hits, top_k=top_k)

    def retrieve_all_clauses(
        self,
        reqs: Sequence[Dict[str, Any]],
        top_k: int = 5,
        progress: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        Batch-encode all clause queries with `bge-m3` on GPU and execute canonical
        `HybridBidIndex` retrieval (Dense + Okapi BM25 + Category Policy + optional Reranker).
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
                query_vecs = self.hybrid_index.encode_queries(queries, batch_size=64)
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
        "联合体协议书",
        "自愿组成联合体",
        "投标报价汇总表",
        "工程量清单表",
        "工程量清单总说明",
        "投标诚信承诺书",
        "投标保证金",
        "统一延续",
        "统一延期",
        "资质证书",
        "营业执照",
        "信用信息",
        "监管服务平台",
        "监管平台",
        "四库一平台",
        "单位参保证明",
        "社会保险",
        "建造师注册证书",
        "安全生产考核合格证书",
        "高级工程师",
        "工程师",
        "投标人名称",
        "项目管理机构",
        "管理和技术人员汇总表",
        "管理和技术人员简历表",
        "主要人员简历表",
        "总体施工组织",
        "施工组织设计",
        "施工方案",
        "质量管理体系",
        "质量保证体系",
        "应急预案",
        "资源配备计划",
        "劳动力计划",
        "平面布置",
        "施工场地布置",
        "施工进度计划",
        "投标人一般情况表",
        "企业类似业绩表",
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
                    "visual_seal_verified": bool(p_chunk.visual_seal_verified),
                    "visual_backend": p_chunk.visual_backend,
                    "has_official_seal": bool(p_chunk.has_official_seal) if p_chunk.visual_seal_verified else None,
                    "seal_company_name": p_chunk.seal_company_name if p_chunk.visual_seal_verified else None,
                    "has_signature": bool(p_chunk.has_signature) if p_chunk.visual_seal_verified else None,
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

"""
Hybrid Dense + Sparse (BM25) Indexer & Cross-Encoder Reranker for TenderCheck RAG.

Supports:
1. Sparse Retrieval: Built-in Okapi BM25 over Chinese character 1-3 grams and
   alphanumeric tokens (exact matching for standard codes like `C40`, `GB50204`).
2. Dense Retrieval: Pretrained Chinese neural embedding model (`BAAI/bge-m3`).
3. Reciprocal Rank Fusion (RRF) + Cross-Encoder Reranker (`BAAI/bge-reranker-v2-m3`).
4. Parent-Child Resolution: Searches fine-grained `Child Chunks` and automatically
   attaches the full `Parent Chunk` context.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np

from .parser import BidChunk, load_chunks_jsonl, save_chunks_jsonl

logger = logging.getLogger(__name__)

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"


@dataclass
class RetrievedChunkHit:
    """Represents a ranked retrieval hit with both Child snippet and Parent context."""

    rank: int
    score: float
    child_chunk: BidChunk
    parent_chunk: BidChunk
    dense_score: float = 0.0
    sparse_score: float = 0.0
    rerank_score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "score": round(float(self.score), 4),
            "dense_score": round(float(self.dense_score), 4),
            "sparse_score": round(float(self.sparse_score), 4),
            "rerank_score": round(float(self.rerank_score), 4) if self.rerank_score is not None else None,
            "chunk_id": self.child_chunk.chunk_id,
            "document_id": self.child_chunk.document_id,
            "parent_id": self.parent_chunk.chunk_id,
            "volume_type": self.child_chunk.volume_type,
            "doc_category": self.child_chunk.doc_category,
            "content_type": self.child_chunk.content_type,
            "section_path": self.child_chunk.section_path,
            "page_start": self.child_chunk.page_start,
            "page_end": self.child_chunk.page_end,
            "child_text": self.child_chunk.text,
            "parent_text": self.parent_chunk.text,
        }


def _tokenize_chinese_for_bm25(text: str) -> List[str]:
    """
    Tokenize Chinese engineering text into character unigrams, bigrams, and
    contiguous alphanumeric/code tokens (e.g., `C40`, `5000`, `GB50204`).
    """
    compact = re.sub(r"\s+", "", text.lower())
    tokens: List[str] = []
    # Alphanumeric words (model codes, standards, numbers)
    tokens.extend(re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", compact))
    # Chinese character unigrams and bigrams
    cjk_runs = re.findall(r"[\u4e00-\u9fa5]+", compact)
    for run in cjk_runs:
        tokens.extend(list(run))
        if len(run) >= 2:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


class OkapiBM25Index:
    """Zero-dependency Okapi BM25 sparse retriever tailored for Chinese tender text."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.doc_freqs: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.doc_term_freqs: List[Counter[str]] = []
        self.doc_lens: np.ndarray = np.empty(0, dtype=np.float32)
        self.avgdl: float = 1.0
        self.num_docs: int = 0

    def fit(self, texts: Sequence[str]) -> "OkapiBM25Index":
        self.num_docs = len(texts)
        self.doc_term_freqs = []
        lengths: List[int] = []
        df_counter: Counter[str] = Counter()

        for text in texts:
            tokens = _tokenize_chinese_for_bm25(text)
            lengths.append(len(tokens))
            tf = Counter(tokens)
            self.doc_term_freqs.append(tf)
            for term in tf:
                df_counter[term] += 1

        self.doc_lens = np.array(lengths, dtype=np.float32)
        self.avgdl = float(self.doc_lens.mean()) if self.num_docs > 0 else 1.0
        self.doc_freqs = dict(df_counter)
        self.idf = {
            term: math.log(1.0 + (self.num_docs - df + 0.5) / (df + 0.5))
            for term, df in self.doc_freqs.items()
        }
        return self

    def score_all(self, query: str) -> np.ndarray:
        if self.num_docs == 0:
            return np.zeros(0, dtype=np.float32)
        q_tokens = _tokenize_chinese_for_bm25(query)
        scores = np.zeros(self.num_docs, dtype=np.float32)

        for idx, tf_map in enumerate(self.doc_term_freqs):
            dl = self.doc_lens[idx]
            norm_denom = self.k1 * (1.0 - self.b + self.b * (dl / max(self.avgdl, 1e-6)))
            doc_score = 0.0
            for term in q_tokens:
                tf = tf_map.get(term, 0)
                if tf == 0:
                    continue
                idf_val = self.idf.get(term, 0.0)
                doc_score += idf_val * (tf * (self.k1 + 1.0)) / (tf + norm_denom)
            scores[idx] = doc_score
        return scores


class HybridBidIndex:
    """
    Hybrid Dense + Sparse (BM25) Index with Parent-Child lookup and optional
    Cross-Encoder Reranking (`bge-reranker-v2-m3`).
    """

    def __init__(
        self,
        embedding_model_name: str = DEFAULT_EMBEDDING_MODEL,
        reranker_model_name: Optional[str] = DEFAULT_RERANKER_MODEL,
        device: Optional[str] = None,
    ) -> None:
        self.embedding_model_name = embedding_model_name
        self.reranker_model_name = reranker_model_name
        self.device = device

        self.parent_map: Dict[str, BidChunk] = {}
        self.child_chunks: List[BidChunk] = []
        self.bm25 = OkapiBM25Index()
        self.dense_embeddings: Optional[np.ndarray] = None

        self._hf_embed_tokenizer = None
        self._hf_embed_model = None
        self._hf_rerank_tokenizer = None
        self._hf_rerank_model = None

    def _encode_neural(self, texts: Sequence[str], batch_size: int = 32) -> np.ndarray:
        import torch
        from transformers import AutoModel, AutoTokenizer

        if self._hf_embed_model is None:
            dev = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
            logger.info("Loading Dense Embedding model '%s' on %s...", self.embedding_model_name, dev)
            self._hf_embed_tokenizer = AutoTokenizer.from_pretrained(self.embedding_model_name)
            self._hf_embed_model = AutoModel.from_pretrained(self.embedding_model_name).to(dev)
            self._hf_embed_model.eval()

        dev = next(self._hf_embed_model.parameters()).device
        all_vecs: List[np.ndarray] = []
        with torch.no_grad():
            for i in range(0, len(texts), batch_size):
                batch_texts = list(texts[i : i + batch_size])
                encoded = self._hf_embed_tokenizer(
                    batch_texts,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(dev)
                out = self._hf_embed_model(**encoded)
                cls_vecs = out.last_hidden_state[:, 0]
                cls_vecs = torch.nn.functional.normalize(cls_vecs, p=2, dim=-1)
                all_vecs.append(cls_vecs.cpu().numpy())

        return np.vstack(all_vecs) if all_vecs else np.zeros((0, 768), dtype=np.float32)

    def build(
        self,
        parent_chunks: Sequence[BidChunk],
        child_chunks: Sequence[BidChunk],
    ) -> "HybridBidIndex":
        """Build BM25 and neural Dense vector indices over `child_chunks`."""
        self.parent_map = {p.chunk_id: p for p in parent_chunks}
        self.child_chunks = list(child_chunks)
        corpus = [c.embedding_text for c in self.child_chunks]

        logger.info("Building Okapi BM25 sparse index over %d child chunks...", len(corpus))
        self.bm25.fit(corpus)

        if corpus:
            self.dense_embeddings = self._encode_neural(corpus)

        return self

    def _compute_dense_scores(self, query: str) -> np.ndarray:
        if not self.child_chunks or self.dense_embeddings is None:
            return np.zeros(len(self.child_chunks), dtype=np.float32)
        q_vec = self._encode_neural([query])[0]
        return np.dot(self.dense_embeddings, q_vec).astype(np.float32)

    def _rerank_candidates(
        self,
        query: str,
        hits: List[RetrievedChunkHit],
        top_k: int,
    ) -> List[RetrievedChunkHit]:
        if not self.reranker_model_name or not hits:
            return hits[:top_k]

        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            if self._hf_rerank_model is None:
                dev = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
                self._hf_rerank_tokenizer = AutoTokenizer.from_pretrained(self.reranker_model_name)
                self._hf_rerank_model = AutoModelForSequenceClassification.from_pretrained(
                    self.reranker_model_name
                ).to(dev)
                self._hf_rerank_model.eval()

            dev = next(self._hf_rerank_model.parameters()).device
            pairs = [[query, h.child_chunk.embedding_text] for h in hits]
            with torch.no_grad():
                inputs = self._hf_rerank_tokenizer(
                    pairs,
                    padding=True,
                    truncation=True,
                    max_length=512,
                    return_tensors="pt",
                ).to(dev)
                logits = self._hf_rerank_model(**inputs).logits.view(-1).cpu().numpy()

            for h, score in zip(hits, logits):
                h.rerank_score = float(score)
                h.score = float(score)

            hits.sort(key=lambda item: item.score, reverse=True)
            for idx, h in enumerate(hits[:top_k], start=1):
                h.rank = idx
            return hits[:top_k]
        except Exception as exc:
            logger.warning("Reranker '%s' failed (%s); keeping RRF ranking.", self.reranker_model_name, exc)
            return hits[:top_k]

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_fn: Optional[Callable[[BidChunk], bool]] = None,
        dense_weight: float = 0.5,
        sparse_weight: float = 0.5,
        rrf_k: int = 60,
        use_reranker: bool = True,
        candidate_pool_size: Optional[int] = None,
    ) -> List[RetrievedChunkHit]:
        """
        Execute Hybrid Dense + Sparse search with optional metadata filter and
        parent-chunk deduplication.
        """
        if candidate_pool_size is not None and candidate_pool_size < top_k:
            raise ValueError("candidate_pool_size must be at least top_k")
        if not self.child_chunks:
            return []

        valid_mask = np.array(
            [True if filter_fn is None else bool(filter_fn(c)) for c in self.child_chunks],
            dtype=bool,
        )
        valid_indices = np.nonzero(valid_mask)[0]
        if len(valid_indices) == 0:
            valid_indices = np.arange(len(self.child_chunks))

        dense_all = (
            self._compute_dense_scores(query)
            if dense_weight > 0.0
            else np.zeros(len(self.child_chunks), dtype=np.float32)
        )
        sparse_all = (
            self.bm25.score_all(query)
            if sparse_weight > 0.0
            else np.zeros(len(self.child_chunks), dtype=np.float32)
        )

        sub_dense = dense_all[valid_indices]
        sub_sparse = sparse_all[valid_indices]

        dense_order = np.argsort(-sub_dense)
        sparse_order = np.argsort(-sub_sparse)

        dense_ranks = np.empty_like(dense_order)
        dense_ranks[dense_order] = np.arange(1, len(valid_indices) + 1)

        sparse_ranks = np.empty_like(sparse_order)
        sparse_ranks[sparse_order] = np.arange(1, len(valid_indices) + 1)

        rrf_scores = (
            dense_weight / (rrf_k + dense_ranks)
            + sparse_weight / (rrf_k + sparse_ranks)
        )

        sorted_sub_pos = np.argsort(-rrf_scores)
        seen_parents = set()
        candidates: List[RetrievedChunkHit] = []
        pool_limit = (candidate_pool_size or max(top_k * 3, 15)) if (use_reranker and self.reranker_model_name) else top_k

        for sub_pos in sorted_sub_pos:
            global_idx = int(valid_indices[sub_pos])
            child = self.child_chunks[global_idx]
            if child.parent_id in seen_parents:
                continue
            seen_parents.add(child.parent_id)
            parent = self.parent_map.get(child.parent_id, child)

            candidates.append(
                RetrievedChunkHit(
                    rank=len(candidates) + 1,
                    score=float(rrf_scores[sub_pos]),
                    child_chunk=child,
                    parent_chunk=parent,
                    dense_score=float(dense_all[global_idx]),
                    sparse_score=float(sparse_all[global_idx]),
                )
            )
            if len(candidates) >= pool_limit:
                break

        if not use_reranker:
            return candidates[:top_k]
        return self._rerank_candidates(query, candidates, top_k=top_k)

    def save(self, index_dir: Path) -> Path:
        """Persist parent chunks, child chunks, BM25, and neural dense embeddings to disk."""
        index_dir = Path(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)

        save_chunks_jsonl(self.parent_map.values(), index_dir / "parent_chunks.jsonl")
        save_chunks_jsonl(self.child_chunks, index_dir / "child_chunks.jsonl")
        joblib.dump(self.bm25, index_dir / "bm25.joblib")

        if self.dense_embeddings is not None:
            np.save(index_dir / "dense_embeddings.npy", self.dense_embeddings)

        meta = {
            "embedding_model_name": self.embedding_model_name,
            "reranker_model_name": self.reranker_model_name,
            "num_parents": len(self.parent_map),
            "num_children": len(self.child_chunks),
        }
        with open(index_dir / "index_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return index_dir

    @classmethod
    def load(
        cls,
        index_dir: Path,
        device: Optional[str] = None,
        reranker_model_name: Optional[str] = DEFAULT_RERANKER_MODEL,
    ) -> "HybridBidIndex":
        """Reload a persisted `HybridBidIndex` from `index_dir`."""
        index_dir = Path(index_dir)
        with open(index_dir / "index_meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)

        instance = cls(
            embedding_model_name=str(meta.get("embedding_model_name", DEFAULT_EMBEDDING_MODEL)),
            reranker_model_name=reranker_model_name or meta.get("reranker_model_name", DEFAULT_RERANKER_MODEL),
            device=device,
        )
        parents = load_chunks_jsonl(index_dir / "parent_chunks.jsonl")
        instance.parent_map = {p.chunk_id: p for p in parents}
        instance.child_chunks = load_chunks_jsonl(index_dir / "child_chunks.jsonl")
        instance.bm25 = joblib.load(index_dir / "bm25.joblib")

        dense_path = index_dir / "dense_embeddings.npy"
        if dense_path.exists():
            instance.dense_embeddings = np.load(dense_path)

        return instance

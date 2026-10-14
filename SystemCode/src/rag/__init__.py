"""
TenderCheck Dual-Index & Category-Routed RAG Package.

Core Modules:
1. `vision`: Unified `PageVisionResult` protocol, `QwenVLBackend` (for RTX 5080),
   `LocalHeuristicOCRBackend` (zero-GPU fallback), and `DiskCachedVisionBackend`.
2. `parser`: `BidDocumentParser` supporting 3-stream multimodal ingestion
   (Narrative Parent-Child chunks, Markdown+Row Table chunks, Scanned Certificate chunks)
   and smart sub-chapter folder (`分章节文件/`) parsing.
3. `ledger`: `EntityLedgerBuilder` & `BidEntityLedger` (Company, Personnel,
   Engineering Performance, Commercial/Schedule Summary + Cross-Page Consistency check).
4. `indexer`: `HybridBidIndex` combining Okapi BM25 sparse search, Dense vector search
   (`BAAI/bge-m3`), Reciprocal Rank Fusion (RRF), and optional `bge-reranker-v2-m3`.
5. `retriever`: `CategoryRouterRetriever` routing queries according to the 8 official
   `Requirement Type` categories.
"""

from .indexer import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER_MODEL,
    HybridBidIndex,
    OkapiBM25Index,
    RetrievedChunkHit,
)
from .ledger import BidEntityLedger, EntityLedgerBuilder, FieldMention
from .parser import (
    BidChunk,
    BidDocumentParser,
    load_chunks_jsonl,
    save_chunks_jsonl,
)
from .retriever import CategoryRouterRetriever, RetrievedEvidenceBundle
from .vision import (
    DOC_CATEGORIES,
    BaseVisionBackend,
    DiskCachedVisionBackend,
    LocalHeuristicOCRBackend,
    PageVisionResult,
    QwenVLBackend,
)

__all__ = [
    "DOC_CATEGORIES",
    "PageVisionResult",
    "BaseVisionBackend",
    "LocalHeuristicOCRBackend",
    "QwenVLBackend",
    "DiskCachedVisionBackend",
    "BidChunk",
    "BidDocumentParser",
    "save_chunks_jsonl",
    "load_chunks_jsonl",
    "FieldMention",
    "BidEntityLedger",
    "EntityLedgerBuilder",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_RERANKER_MODEL",
    "OkapiBM25Index",
    "RetrievedChunkHit",
    "HybridBidIndex",
    "RetrievedEvidenceBundle",
    "CategoryRouterRetriever",
]

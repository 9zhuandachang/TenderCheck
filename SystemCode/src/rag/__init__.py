"""Multimodal passage parsing, indexing and retrieval."""
from .indexer import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER_MODEL,
    HybridBidIndex,
    OkapiBM25Index,
    RetrievedChunkHit,
)
from .parser import (
    BidChunk,
    BidDocumentParser,
    load_chunks_jsonl,
    save_chunks_jsonl,
)
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
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_RERANKER_MODEL",
    "OkapiBM25Index",
    "RetrievedChunkHit",
    "HybridBidIndex",
]

from .passages import PassageRetriever
__all__.append("PassageRetriever")

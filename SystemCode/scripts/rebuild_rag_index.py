"""Build a separate index from completed vision caches, reusing unchanged vectors."""
import argparse
import time
from pathlib import Path

from _rag_experiment_utils import (directory_hashes, read_json, require_separate_directories,
                                   set_offline, write_json)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bid_input", "cache_dir", "base_index", "output_index"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--embedding_model", help="Changing the model disables vector reuse.")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    for source in (args.bid_input, args.cache_dir, args.base_index):
        require_separate_directories(source, args.output_index)
    if args.output_index.exists() and any(args.output_index.iterdir()):
        parser.error("output_index must be new or empty")
    set_offline(args.offline)
    import numpy as np
    from src.rag import (BaseVisionBackend, BidDocumentParser, DiskCachedVisionBackend,
                         HybridBidIndex, load_chunks_jsonl)

    class CacheOnly(BaseVisionBackend):
        def analyze_page(self, doc, pdf_page, **kwargs):
            raise RuntimeError(f"Vision cache miss: {doc.name}:{pdf_page}")

    class CompletedCache(DiskCachedVisionBackend):
        def analyze_page(self, *args, **kwargs):
            result = super().analyze_page(*args, **kwargs)
            if result.backend_name == "qwen2.5_vl_raw":
                raise RuntimeError("Unrecovered raw page in vision cache")
            return result

    started = time.time()
    before_cache = directory_hashes(args.cache_dir)
    before_index = directory_hashes(args.base_index)
    meta = read_json(args.base_index / "index_meta.json")
    model = args.embedding_model or meta["embedding_model_name"]
    bid_parser = BidDocumentParser(vision_backend=CompletedCache(CacheOnly(), args.cache_dir))
    parents, children, stats = bid_parser.parse_bid_directory(args.bid_input, prefer_subchapters=True)
    if not parents or not children:
        raise ValueError("No chunks parsed")
    index = HybridBidIndex(embedding_model_name=model, reranker_model_name=meta.get("reranker_model_name"))
    index.parent_map = {c.chunk_id: c for c in parents}
    index.child_chunks = children
    if not all(c.parent_id in index.parent_map for c in children):
        raise ValueError("Missing parent chunk")
    texts = [c.embedding_text for c in children]
    index.bm25.fit(texts)
    lookup = {}
    if model == meta["embedding_model_name"]:
        old_children = load_chunks_jsonl(args.base_index / "child_chunks.jsonl")
        old_vectors = np.load(args.base_index / "dense_embeddings.npy", allow_pickle=False)
        if old_vectors.ndim != 2 or old_vectors.shape[0] != len(old_children):
            raise ValueError("Base vectors and chunks do not match")
        if not np.isfinite(old_vectors).all() or not np.allclose(np.linalg.norm(old_vectors, axis=1), 1, atol=1e-3):
            raise ValueError("Invalid base vectors")
        lookup = {c.embedding_text: v for c, v in zip(old_children, old_vectors)}
    reused = sum(text in lookup for text in texts)
    new_texts = list(dict.fromkeys(text for text in texts if text not in lookup))
    print(f"VECTORS reused={reused} new_unique={len(new_texts)}", flush=True)
    if new_texts:
        lookup.update(zip(new_texts, index._encode_neural(new_texts)))
    index.dense_embeddings = np.vstack([lookup[text] for text in texts])
    if not np.isfinite(index.dense_embeddings).all() or not np.allclose(np.linalg.norm(index.dense_embeddings, axis=1), 1, atol=1e-3):
        raise ValueError("Invalid rebuilt vectors")
    if before_cache != directory_hashes(args.cache_dir) or before_index != directory_hashes(args.base_index):
        raise RuntimeError("Source cache or base index changed")
    index.save(args.output_index)
    write_json(args.output_index / "ingest_stats.json", stats)
    write_json(args.output_index / "document_sources.json", bid_parser.document_sources)
    summary = dict(status="complete", parents=len(parents), children=len(children),
                   reused_vectors=reused, newly_encoded_unique_texts=len(new_texts),
                   cache_only=True, source_cache_unchanged=True, base_index_unchanged=True,
                   embedding_model=model, seconds=round(time.time() - started, 3),
                   source_cache_sha256=before_cache, base_index_sha256=before_index)
    write_json(args.output_index / "rebuild_summary.json", summary)
    print(f"INDEX_COMPLETE parents={len(parents)} children={len(children)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

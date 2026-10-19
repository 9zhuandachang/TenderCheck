"""
Command-Line Entrypoint for TenderCheck passage retrieval.

Supports:
1. `ingest`: Parse a bid PDF or `02_投标文件` directory (automatically leveraging
   `分章节文件/` when present), and construct the Hybrid Dense + BM25 Index.
   The historical structured ledger is optional via --include_ledger.
2. `query`: Retrieve passages for a requirement and its supplied category,
   with original PDF identity and physical pages for downstream extraction.

Usage Examples:
    # 1. Ingest a project's 02_投标文件 directory (CPU fast mode or RTX 5080 VLM mode)
    python SystemCode/run_rag_pipeline.py --mode ingest \\
        --bid_input "data_extracted/2022年投标项目/11.11-S604.../02_投标文件" \\
        --index_dir "SystemCode/data/rag_index/S604"

    # 2. Ingest on teammate's RTX 5080 with Qwen2.5-VL-7B + BGE-M3 + Reranker
    python SystemCode/run_rag_pipeline.py --mode ingest \\
        --bid_input "path/to/02_投标文件" \\
        --index_dir "SystemCode/data/rag_index/S604" \\
        --vision_backend qwen_vl

    # 3. Query passages by Requirement Type
    python SystemCode/run_rag_pipeline.py --mode query \\
        --index_dir "SystemCode/data/rag_index/S604" \\
        --req_type Consistency \\
        --query "投标函与开标一览表中的项目经理姓名及投标总价应保持一致。"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.rag import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_RERANKER_MODEL,
    BidDocumentParser,
    CategoryRouterRetriever,
    DiskCachedVisionBackend,
    EntityLedgerBuilder,
    HybridBidIndex,
    LocalHeuristicOCRBackend,
    QwenVLBackend,
)
from src.rag.passages import PassageRetriever, passage_to_dict, source_registry

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_rag_pipeline")

BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TenderCheck passage retrieval with PDF provenance"
    )
    parser.add_argument(
        "--mode",
        choices=["ingest", "query"],
        default="ingest",
        help="Execution mode: 'ingest' to parse & index bid files, 'query' to retrieve evidence.",
    )
    parser.add_argument(
        "--bid_input",
        type=Path,
        default=None,
        help="Path to a bid PDF file or a project's '02_投标文件' directory.",
    )
    parser.add_argument(
        "--index_dir",
        type=Path,
        default=BASE_DIR / "data" / "rag_index" / "default_bid",
        help="Directory to save/load the passage index and document source mapping.",
    )
    parser.add_argument(
        "--vision_cache_dir",
        "--cache_dir",
        dest="vision_cache_dir",
        type=Path,
        default=BASE_DIR / "data" / "vision_cache",
        help="Shared directory for caching page-level VLM/OCR PageVisionResult JSONs.",
    )
    parser.add_argument(
        "--vision_backend",
        choices=["qwen_vl", "heuristic"],
        default="qwen_vl",
        help="Vision backend for scanned/certificate pages (default: 'qwen_vl' on RTX 5080).",
    )
    parser.add_argument(
        "--vlm_mode",
        choices=["transformers", "openai_compatible"],
        default="transformers",
        help="How Qwen2.5-VL is served when --vision_backend=qwen_vl.",
    )
    parser.add_argument(
        "--vlm_model",
        type=str,
        default="Qwen/Qwen2.5-VL-7B-Instruct",
        help="HuggingFace model ID or path for VLM.",
    )
    parser.add_argument(
        "--vlm_api_base",
        type=str,
        default="http://localhost:8000/v1",
        help="API base URL when --vlm_mode=openai_compatible.",
    )
    parser.add_argument(
        "--vlm_quantization",
        choices=["none", "int8", "nf4"],
        default="none",
        help="Optional Qwen language-layer int8/NF4 loading for 16 GB GPUs (requires bitsandbytes).",
    )
    parser.add_argument(
        "--embedding_model",
        "--dense_model",
        dest="embedding_model",
        type=str,
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Dense embedding model name (default: {DEFAULT_EMBEDDING_MODEL}).",
    )
    parser.add_argument(
        "--reranker_model",
        type=str,
        default=DEFAULT_RERANKER_MODEL,
        help=f"Cross-encoder reranker model (default: {DEFAULT_RERANKER_MODEL}).",
    )
    parser.add_argument(
        "--max_pages",
        type=int,
        default=None,
        help="Optional limit on pages parsed per PDF for rapid testing.",
    )

    # Query mode options
    parser.add_argument("--include_ledger", action="store_true",
                        help="Opt into the legacy ledger build/dual-index query experiment.")
    parser.add_argument("--source_map", type=Path, help="Existing document_sources.json for older indices.")
    parser.add_argument("--candidate_pool_size", type=int, default=50)
    parser.add_argument(
        "--query",
        type=str,
        default="",
        help="Requirement clause text; required for passage query mode.",
    )
    parser.add_argument(
        "--section",
        type=str,
        default="",
        help="Optional section header of the requirement clause.",
    )
    parser.add_argument(
        "--req_type",
        type=str,
        default="Technical",
        help="One of the 8 Requirement Types (Qualification, Document, Technical, Commercial, Schedule, Consistency, Integrity, Scoring).",
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=4,
        help="Number of top evidence chunks to retrieve.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.mode == "ingest":
        if args.bid_input is None or not args.bid_input.exists():
            raise FileNotFoundError(
                f"Please specify a valid --bid_input path (got: {args.bid_input})"
            )

        if args.vision_backend == "qwen_vl":
            raw_vision = QwenVLBackend(
                model_name_or_path=args.vlm_model,
                mode=args.vlm_mode,
                api_base_url=args.vlm_api_base,
                quantization=args.vlm_quantization,
            )
        else:
            raw_vision = LocalHeuristicOCRBackend()

        cached_vision = DiskCachedVisionBackend(
            delegate=raw_vision,
            cache_dir=args.vision_cache_dir,
        )
        parser = BidDocumentParser(vision_backend=cached_vision)

        logger.info("Parsing bid input: %s ...", args.bid_input)
        parent_chunks, child_chunks, parse_stats = parser.parse_bid_directory(
            bid_dir=args.bid_input,
            prefer_subchapters=True,
            max_pages_per_pdf=args.max_pages,
        )
        if hasattr(raw_vision, "unload"):
            raw_vision.unload()
        logger.info(
            "Parsed %d Parent Chunks and %d Child Chunks.",
            len(parent_chunks),
            len(child_chunks),
        )

        if args.include_ledger:
            doc_id = parent_chunks[0].document_id if parent_chunks else args.bid_input.stem
            ledger = EntityLedgerBuilder.build_from_chunks(doc_id, parent_chunks, child_chunks)
            EntityLedgerBuilder.save_ledger(ledger, args.index_dir)

        # Build and save Hybrid Dense + BM25 Index
        index = HybridBidIndex(
            embedding_model_name=args.embedding_model,
            reranker_model_name=args.reranker_model,
        )
        index.build(parent_chunks=parent_chunks, child_chunks=child_chunks)
        index.save(args.index_dir)
        with open(args.index_dir / "document_sources.json", "w", encoding="utf-8") as f:
            json.dump(parser.document_sources, f, ensure_ascii=False, indent=2)

        with open(args.index_dir / "ingest_stats.json", "w", encoding="utf-8") as f:
            json.dump(parse_stats, f, ensure_ascii=False, indent=2)

        print(json.dumps(parse_stats, ensure_ascii=False, indent=2))
        return

    # Mode: query
    index = HybridBidIndex.load(args.index_dir, reranker_model_name=args.reranker_model)
    if not args.include_ledger:
        if not args.query:
            raise ValueError("Passage query mode requires --query")
        mapping_path = args.source_map or args.index_dir / "document_sources.json"
        if not mapping_path.exists():
            raise FileNotFoundError("Source mapping missing; supply --source_map or rebuild the index.")
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        sources = source_registry(mapping, index.parent_map)
        hits = PassageRetriever(index).retrieve(args.query, args.req_type, args.top_k, args.candidate_pool_size)
        print(json.dumps(dict(requirement=args.query, requirement_type=args.req_type,
                              passages=[passage_to_dict(h, sources) for h in hits]),ensure_ascii=False,indent=2))
        return
    ledger = EntityLedgerBuilder.load_ledger(args.index_dir / "ledger.json")
    retriever = CategoryRouterRetriever(index=index, ledger=ledger)

    if args.query:
        bundle = retriever.retrieve(
            requirement_text=args.query,
            section_text=args.section,
            requirement_type=args.req_type,
            top_k=args.top_k,
        )
        print(json.dumps(bundle.to_dict(), ensure_ascii=False, indent=2))
        return

    # Default 8-category smoke test when --query is omitted
    demo_queries = [
        ("Qualification", "第一章 招标公告", "投标人须具备公路工程施工总承包三级及以上资质，项目经理须具备公路工程专业二级及以上注册建造师执业资格。"),
        ("Commercial", "第二章 投标人须知", "投标总价不得超过最高投标限价，并按招标文件要求足额缴纳投标保证金。"),
        ("Schedule", "第二章 投标人须知", "计划工期不超过日历天要求，投标有效期为90日历天。"),
        ("Consistency", "第三章 评标办法", "投标函与开标一览表中的投标总价大小写、工期及项目经理姓名必须完全一致。"),
    ]
    results = []
    for req_type, sec, q_text in demo_queries:
        b = retriever.retrieve(
            requirement_text=q_text,
            section_text=sec,
            requirement_type=req_type,
            top_k=args.top_k,
        )
        results.append(b.to_dict())
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

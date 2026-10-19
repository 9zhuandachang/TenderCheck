"""Collect passage-only comparisons, then score explicit reviewed evidence references."""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from _rag_experiment_utils import directory_hashes, read_json, set_offline, sha256, write_json
from src.evaluation.evidence import compact, score_evidence
from src.rag.passages import source_registry

METHODS = ("bm25", "hybrid", "hybrid_rerank", "predicted_category_rerank")


def verify_source_files(sources):
    checked = set()
    for source in sources.values():
        if source["path"] not in checked:
            if sha256(source["path"]) != source["sha256"]:
                raise ValueError(f"Source PDF changed: {source['path']}")
            checked.add(source["path"])


def render_review(runs):
    lines = ["# 招标要求—投标证据核对表", "",
             "所有结果都是检索候选；出现于此表不表示相关或合规。请核对原 PDF，再填写 annotations.json。",
             "人工确认前不计入 human_verified 指标。缺少证据保持 pending/unknown，不能自动标记材料不存在。", ""]
    for q in runs["queries"]:
        lines += [f"## {q['clause_id']}", "", q["requirement"], "",
                  f"分类器预测：{q['predicted_category']}。标注状态见 annotations.json。", ""]
        unique = {}
        for method, hits in q["methods"].items():
            for h in hits:
                row = unique.setdefault(h["parent_id"], dict(hit=h, ranks=[]))
                row["ranks"].append(f"{method} #{h['rank']}")
        for row in unique.values():
            h = row["hit"]
            lines += [f"### {h['parent_id']}", "", ", ".join(row["ranks"]), "",
                      f"来源：{h['source']}；PDF 物理页 {h['page_start']}–{h['page_end']}", "",
                      f"章节：{h['section_path']}", "", "```text", h["parent_text"].replace("```", "'''"), "```", ""]
    return "\n".join(lines)


def collect(args):
    import numpy as np
    from src.classification import load_classifier
    from src.rag import HybridBidIndex
    from src.rag.passages import PassageRetriever, passage_to_dict

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise ValueError("Use a new or empty output_dir")
    if args.candidate_pool_size < args.top_k:
        raise ValueError("candidate_pool_size must be >= top_k")
    raw = read_json(args.queries)
    queries = raw.get("queries", raw.get("gold_retrieval_queries", []))
    ids = [q["clause_id"] for q in queries]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Expected nonempty queries with unique clause_id")
    before = directory_hashes(args.index_dir)
    index = HybridBidIndex.load(args.index_dir, reranker_model_name=str(args.reranker_model))
    if (index.dense_embeddings is None or index.dense_embeddings.ndim != 2
            or len(index.dense_embeddings) != len(index.child_chunks)
            or not np.isfinite(index.dense_embeddings).all()):
        raise ValueError("Invalid neural index")
    sources = source_registry(read_json(args.source_map), index.parent_map)
    classifier = load_classifier(args.model_dir)
    original_rerank = index._rerank_candidates

    def strict_rerank(query, hits, top_k):
        result = original_rerank(query, hits, top_k)
        if result and any(h.rerank_score is None or not math.isfinite(h.rerank_score) for h in result):
            raise RuntimeError("Reranker failed; refusing RRF fallback")
        return result

    index._rerank_candidates = strict_rerank
    retriever = PassageRetriever(index)
    run = dict(schema_version=2, project_id=raw.get("project_id"), methods=list(METHODS),
               top_k=args.top_k, candidate_pool_size=args.candidate_pool_size,
               corpus_sha256=before["parent_chunks.jsonl"], index_sha256=before,
               sources=sources, queries=[], status="running", started_at=time.time(),
               inputs_sha256={"queries":sha256(args.queries), "source_map":sha256(args.source_map)},
               classifier=str(args.model_dir), classifier_sha256=directory_hashes(args.model_dir),
               embedding_model=index.embedding_model_name, reranker_model=str(args.reranker_model),
               returned_context="Full parent passage for each unique parent, no text truncation",
               category_policy="Existing query expansion + filter + category fusion weights; not a filter-only ablation")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def serialize(hits):
        result = []
        for rank, hit in enumerate(hits[:args.top_k], 1):
            parent = hit.parent_chunk
            if not 1 <= parent.page_start <= parent.page_end <= sources[parent.document_id]["page_count"]:
                raise ValueError("Invalid physical page citation")
            row = passage_to_dict(hit, sources)
            row["rank"] = rank
            result.append(row)
        return result

    try:
        for q in queries:
            requirement = q["requirement"]
            predicted = classifier.predict_single(requirement_text=requirement, section_text=q.get("section", ""))["label"]
            methods, seconds = {}, {}
            start = time.perf_counter()
            methods["bm25"] = serialize(index.search(requirement, top_k=args.top_k, dense_weight=0,
                                                       sparse_weight=1, use_reranker=False))
            seconds["bm25"] = time.perf_counter()-start
            start = time.perf_counter()
            pool = index.search(requirement, top_k=args.candidate_pool_size, dense_weight=.55,
                                sparse_weight=.45, use_reranker=False)
            methods["hybrid"] = serialize(pool)
            seconds["hybrid"] = time.perf_counter()-start
            start = time.perf_counter()
            methods["hybrid_rerank"] = serialize(strict_rerank(requirement, pool, args.top_k))
            seconds["hybrid_rerank_incremental"] = time.perf_counter()-start
            start = time.perf_counter()
            methods["predicted_category_rerank"] = serialize(retriever.retrieve(requirement, predicted,
                                                            args.top_k, args.candidate_pool_size))
            seconds["predicted_category_rerank"] = time.perf_counter()-start
            run["queries"].append(dict(clause_id=q["clause_id"], requirement=requirement,
                                        predicted_category=predicted, methods=methods, seconds=seconds))
            write_json(args.output_dir/"retrieval.json", run)
            print(f"COLLECT {len(run['queries'])}/{len(queries)} {q['clause_id']}", flush=True)
        if before != directory_hashes(args.index_dir):
            raise RuntimeError("Source index changed")
        verify_source_files(sources)
        run.update(status="complete", index_unchanged=True, source_files_unchanged=True,
                   citation_resolvability=dict(valid=sum(len(h) for q in run["queries"] for h in q["methods"].values()),
                                              note="File hash and page bounds verified; not a semantic relevance score"))
    except Exception as exc:
        run.update(status="failed", error=str(exc))
        raise
    finally:
        run["updated_at"] = time.time()
        write_json(args.output_dir/"retrieval.json", run)
    annotations = dict(schema_version=2, project_id=run["project_id"], corpus_sha256=run["corpus_sha256"],
        queries=[dict(clause_id=q["clause_id"], requirement=q["requirement"], review_status="pending",
                      evidence_status="unknown", annotation_complete=False, reviewer="", review_note="",
                      evidence_groups=[]) for q in run["queries"]])
    write_json(args.output_dir/"annotations.json", annotations)
    (args.output_dir/"review.md").write_text(render_review(run), encoding="utf-8")


def score(args):
    from src.rag.parser import fitz
    runs, annotations = read_json(args.run_dir/"retrieval.json"), read_json(args.annotations)
    if runs["status"] != "complete":
        raise ValueError("Cannot score an incomplete collection")
    verify_source_files(runs["sources"])
    # Check native-PDF quotes independently of the retrieved text or keyword benchmark.
    from src.evaluation.evidence import validate_annotations
    validate_annotations(annotations, runs)
    for q in annotations["queries"]:
        if q["review_status"] == "pending":
            continue
        for group in q["evidence_groups"]:
            for ref in group["alternatives"]:
                if ref["verification_basis"] == "native_pdf":
                    with fitz.open(runs["sources"][ref["document_id"]]["path"]) as pdf:
                        text = "\n".join(pdf[i].get_text() for i in range(ref["page_start"]-1,ref["page_end"]))
                    if compact(ref.get("source_quote",ref["quote"])) not in compact(text):
                        raise ValueError(f"Reference quote not found in original PDF: {q['clause_id']}")
    report = score_evidence(annotations, runs, ks=tuple(k for k in (1,3,5) if k <= runs["top_k"]))
    report["annotations_sha256"] = sha256(args.annotations)
    report["retrieval_sha256"] = sha256(args.run_dir/"retrieval.json")
    write_json(args.output, report)
    print(json.dumps({s:{k:v for k,v in row.items() if k!='methods'} for s,row in report['strata'].items()},ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("collect", help="Collect 4 passage-only rankings; initialize pending annotations")
    for name in ("queries", "index_dir", "source_map", "model_dir", "reranker_model", "output_dir"):
        p.add_argument(f"--{name}", type=Path, required=True)
    p.add_argument("--top_k", type=int, default=5)
    p.add_argument("--candidate_pool_size", type=int, default=50)
    p.add_argument("--offline", action="store_true")
    p = commands.add_parser("score", help="Score reviewed source/page/quote references without model inference")
    for name in ("run_dir", "annotations", "output"):
        p.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        if args.top_k < 1:
            parser.error("top_k must be positive")
        set_offline(args.offline)
        collect(args)
    else:
        score(args)


if __name__ == "__main__":
    main()

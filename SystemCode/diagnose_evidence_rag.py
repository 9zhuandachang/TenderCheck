"""Diagnose frozen retrieval misses without changing queries or reference labels."""
import argparse
from collections import Counter
from pathlib import Path

from scripts._rag_experiment_utils import read_json, sha256, write_json
from src.evaluation.evidence import contains_reference_text, matches_location, validate_annotations


def diagnose(annotations, retrieval, parents):
    validate_annotations(annotations, retrieval)
    corpus = []
    for p in parents:
        corpus.append(dict(p, source_sha256=retrieval["sources"][p["document_id"]]["sha256"],
                           parent_text=p["text"]))
    queries = {q["clause_id"]: q for q in retrieval["queries"]}
    methods = {}
    for method in retrieval["methods"]:
        details = []
        for q in annotations["queries"]:
            if not q.get("annotation_complete") or q["review_status"] == "pending":
                continue
            hits = queries[q["clause_id"]]["methods"][method]
            for group in q["evidence_groups"]:
                refs = group["alternatives"]
                matching = [p for p in corpus if any(matches_location(p, ref) for ref in refs)]
                found = [h["rank"] for h in hits if any(matches_location(h, ref) for ref in refs)]
                if found:
                    reason = "retrieved"
                elif matching:
                    reason = "indexed_but_not_top_k"
                else:
                    overlaps = [p for p in corpus if any(
                        p["source_sha256"] == ref["source_sha256"]
                        and p["page_start"] <= ref["page_end"] and p["page_end"] >= ref["page_start"]
                        for ref in refs)]
                    split = any(contains_reference_text("\n".join(
                        p["parent_text"] for p in sorted(overlaps, key=lambda p: p["page_start"])
                        if p["source_sha256"] == ref["source_sha256"]
                        and p["page_start"] <= ref["page_end"] and p["page_end"] >= ref["page_start"]), ref)
                        for ref in refs)
                    reason = ("split_across_parents" if split else
                              "indexed_page_text_mismatch" if overlaps else "source_page_not_indexed")
                details.append(dict(clause_id=q["clause_id"], group_id=group["group_id"],
                                    reason=reason, first_rank=min(found) if found else None,
                                    matching_parent_ids=[p["parent_id"] for p in matching]))
        methods[method] = dict(group_counts=dict(Counter(d["reason"] for d in details)), details=details)
    return dict(methods=methods, note=(
        "Literal reference coverage diagnosis, not a semantic relevance judgment. "
        "Text mismatch can reflect OCR, serialization or annotation wording; it does not prove absent evidence. "
        "The frozen run contains only Top-K: candidate recall and reranker errors cannot be separated."))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("retrieval", "annotations", "parents", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    retrieval = read_json(args.retrieval)
    if sha256(args.parents) != retrieval["corpus_sha256"]:
        raise ValueError("Diagnosis requires the exact frozen parent corpus")
    import json
    parents = [json.loads(line) for line in args.parents.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = diagnose(read_json(args.annotations), retrieval, parents)
    report["input_sha256"] = {n: sha256(getattr(args, n)) for n in ("retrieval", "annotations", "parents")}
    write_json(args.output, report)
    print(json.dumps({m: row["group_counts"] for m, row in report["methods"].items()}))


if __name__ == "__main__":
    main()

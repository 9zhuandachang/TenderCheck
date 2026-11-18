"""CLI entrypoint for standalone LLM slot extraction evaluation (Step 5)."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.evaluation.extraction_eval import (
    DEFAULT_BENCHMARK_PATH,
    evaluate_extraction_benchmark,
    load_extraction_benchmark,
)
from src.extraction.global_context import (
    ProjectGlobalContext,
    build_project_global_context,
    hydrate_with_global_context,
)
from src.extraction.llm_extractor import DeepSeekSlotExtractor
from src.extraction.schema import StructuredExtractionResult


def make_extractor_predictor(
    extractor: DeepSeekSlotExtractor,
    review_reference_date: str,
    global_ctx: ProjectGlobalContext | None = None,
):
    def _predict(item: Dict[str, Any]) -> StructuredExtractionResult:
        ctx = json.dumps(item["evidence_context"], ensure_ascii=False)
        ref_date = item.get("review_reference_date") or review_reference_date
        res = extractor.extract(
            item["clause_id"],
            item["requirement_text"],
            item["category"],
            ctx,
            review_reference_date=ref_date,
        )
        if global_ctx is not None:
            res, _ = hydrate_with_global_context(
                res, ctx, global_ctx, normalizer=extractor
            )
        return res

    return _predict


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate LLM slot extraction against the S604 extraction benchmark."
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=DEFAULT_BENCHMARK_PATH,
        help="Path to s604_extraction_benchmark.json",
    )
    parser.add_argument(
        "--split",
        choices=("all", "dev", "test"),
        default="all",
        help="Benchmark split to evaluate (default: all)",
    )
    parser.add_argument(
        "--hydrate-global-context",
        "--hydrate_global_context",
        dest="hydrate_global_context",
        action="store_true",
        help="Apply project-level global context hydration after verbatim grounding",
    )
    parser.add_argument(
        "--cache-only",
        "--cache_only",
        dest="cache_only",
        action="store_true",
        help="Only read from cache without network calls",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save full JSON evaluation report",
    )
    args = parser.parse_args()

    benchmark = load_extraction_benchmark(args.benchmark)
    ref_date = benchmark.get("review_reference_date", "2022-11-11")

    extractor = DeepSeekSlotExtractor(
        cache_only=args.cache_only, review_reference_date=ref_date
    )
    global_ctx = None
    if args.hydrate_global_context:
        cases = [
            {
                "clause_id": c["clause_id"],
                "requirement": c["requirement_text"],
                "category": c["category"],
                "evidence_context": json.dumps(c["evidence_context"], ensure_ascii=False),
            }
            for c in benchmark.get("clauses", [])
        ]
        global_ctx = build_project_global_context(cases)
    predictor = make_extractor_predictor(extractor, ref_date, global_ctx=global_ctx)

    report = evaluate_extraction_benchmark(benchmark, predictor, split=args.split)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary = {k: v for k, v in report.items() if k != "clause_reports"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

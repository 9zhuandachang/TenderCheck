"""Compatibility entry point. Use run_reasoning_diagnostics.py for pipeline checks."""
import sys

from run_reasoning_diagnostics import build_cases, fresh_directory, main
from src.evaluation.pipeline_diagnostics import summarize_pipeline as compute_metrics


if __name__ == '__main__':
    print('Moved to run_reasoning_diagnostics.py: output counts are pipeline diagnostics, '
          'not RAG, extraction or rule accuracy.', file=sys.stderr)
    raise SystemExit(main())

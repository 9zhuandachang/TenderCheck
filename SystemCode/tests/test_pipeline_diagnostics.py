"""Regression checks for the separation of RAG examples and pipeline diagnostics."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_reasoning_diagnostics import prepare, run
from src.evaluation.pipeline_diagnostics import summarize_pipeline, compare_human_end_to_end


class PipelineDiagnosticsTests(unittest.TestCase):
    def test_unlabelled_pipeline_statistics_do_not_imply_a_new_benchmark(self):
        report = summarize_pipeline([dict(clause_id='q')], [dict(clause_id='q',verdict='INSUFFICIENT_EVIDENCE')])
        self.assertEqual(report['evaluation_scope'], 'pipeline_diagnostics')
        self.assertEqual(report['decision_coverage'], 0)
        self.assertFalse(report['accuracy_computed'])
        for key in ['verdict_accuracy','pending_labels','assistant_reference_verdict_agreement']:
            self.assertNotIn(key, report)

    def test_only_explicit_human_end_to_end_labels_can_be_scored(self):
        cases, results = [dict(clause_id='q')], [dict(clause_id='q',verdict='COMPLIANT')]
        label = dict(clause_id='q',review_status='human_verified',reviewer='test',
                     review_note='synthetic fixture',expected_verdict='COMPLIANT')
        labels = dict(schema_version=1,cases_sha256='hash',queries=[label])
        # Old templates or retrieval labels cannot silently become verdict gold.
        with self.assertRaises(ValueError):
            compare_human_end_to_end(cases,results,labels,'hash')
        labels['evaluation_scope'] = 'end_to_end_compliance'
        report = compare_human_end_to_end(cases,results,labels,'hash')
        self.assertEqual(report['end_to_end_verdict_accuracy'], 1)
        label['review_status'] = 'assistant_verified'
        with self.assertRaises(ValueError):
            compare_human_end_to_end(cases,results,labels,'hash')

    def test_duplicate_cases_and_unknown_outputs_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize_pipeline([dict(clause_id='q')]*2, [dict(clause_id='q',verdict='COMPLIANT')])
        with self.assertRaises(ValueError):
            summarize_pipeline([dict(clause_id='q')], [dict(clause_id='q',verdict='made_up')])

    def test_prepare_and_run_create_diagnostics_without_implicit_labels_or_accuracy(self):
        # The Windows sandbox temp volume may disallow atomic replace; exercise
        # the real writer on the workspace volume used by experiment outputs.
        workspace = Path(__file__).resolve().parents[2]
        scratch = workspace/'scratch'
        scratch.resolve().relative_to(workspace)
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, prefix='pipeline_test_') as tmp:
            root = Path(tmp)
            root.resolve().relative_to(workspace)  # Bound automatic cleanup.
            retrieval = root/'retrieval.json'
            retrieval.write_text(json.dumps(dict(schema_version=2,status='complete',methods=['m'],top_k=5,
                sources={},queries=[dict(clause_id='q',requirement='计划工期为100天。',
                    predicted_category='Schedule',methods={'m':[]})])), encoding='utf-8')
            inputs = root/'inputs'
            with patch('run_reasoning_diagnostics.verify_source_files'), contextlib.redirect_stdout(io.StringIO()):
                prepare(SimpleNamespace(retrieval=retrieval,method='m',output_dir=inputs))
                self.assertEqual({p.name for p in inputs.iterdir()}, {'cases.json'})
                self.assertEqual(json.loads((inputs/'cases.json').read_text('utf-8'))['evaluation_scope'], 'pipeline_diagnostics')
                output = root/'output'
                args = SimpleNamespace(cases=inputs/'cases.json',labels=None,reference_labels=None,
                    backend='heuristic',output_dir=output,model='unused')
                with patch('urllib.request.urlopen',side_effect=AssertionError('No network')):
                    self.assertEqual(run(args), 0)
            report = json.loads((output/'metrics.json').read_text('utf-8'))
            self.assertEqual(report['evaluation_scope'], 'pipeline_diagnostics')
            self.assertNotIn('verdict_accuracy', report)
            self.assertNotIn('pending_labels', report)
            self.assertFalse((output/'human_end_to_end_comparison.json').exists())
            self.assertFalse((output/'reference_comparison.json').exists())


if __name__ == '__main__':
    unittest.main()

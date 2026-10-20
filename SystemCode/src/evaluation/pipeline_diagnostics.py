"""Keep pipeline output statistics separate from any reviewed end-to-end score."""
from collections import Counter

from .compliance_reference import VERDICTS


def summarize_pipeline(cases, results):
    ids = [case['clause_id'] for case in cases]
    result_ids = [row['clause_id'] for row in results]
    if len(set(ids)) != len(ids) or len(result_ids) != len(set(result_ids)) or set(result_ids) != set(ids):
        raise ValueError('Predictions must match unique cases exactly')
    if any(row['verdict'] not in VERDICTS | {'ERROR'} for row in results):
        raise ValueError('Unknown pipeline output status')
    counts = Counter(row['verdict'] for row in results)
    decided = sum(counts[key] for key in ('COMPLIANT', 'NON_COMPLIANT'))
    return dict(evaluation_scope='pipeline_diagnostics', total_cases=len(cases),
                verdict_counts=dict(counts), decision_coverage=decided/len(cases) if cases else None,
                accuracy_computed=False,
                note='Output counts and decision coverage only. Not RAG, extraction or rule accuracy. '
                     'INSUFFICIENT_EVIDENCE does not identify the failing stage by itself.')


def compare_human_end_to_end(cases, results, labels, cases_sha256):
    """Explicitly reviewed end-to-end labels cannot measure an isolated component."""
    summarize_pipeline(cases, results)
    if (labels.get('schema_version') != 1 or labels.get('cases_sha256') != cases_sha256
            or labels.get('evaluation_scope') != 'end_to_end_compliance'):
        raise ValueError('Human labels require this case hash and explicit end_to_end_compliance scope')
    ids = {case['clause_id'] for case in cases}
    label_ids = [row['clause_id'] for row in labels['queries']]
    if len(label_ids) != len(set(label_ids)) or set(label_ids) != ids:
        raise ValueError('Label IDs must match cases')
    reviewed = {}
    for row in labels['queries']:
        if row['review_status'] == 'pending':
            continue
        if (row['review_status'] != 'human_verified' or not row.get('reviewer')
                or not row.get('review_note') or row.get('expected_verdict') not in VERDICTS):
            raise ValueError('Only documented human-reviewed verdict labels may be scored')
        reviewed[row['clause_id']] = row['expected_verdict']
    correct = sum(row['verdict'] == reviewed[row['clause_id']] for row in results if row['clause_id'] in reviewed)
    return dict(evaluation_scope='end_to_end_compliance', human_labelled_cases=len(reviewed),
                pending_labels=len(cases)-len(reviewed),
                end_to_end_verdict_accuracy=correct/len(reviewed) if reviewed else None,
                note='Reviewed subset only; not standalone extraction or rule accuracy. '
                     'Score values need separate score labels.')

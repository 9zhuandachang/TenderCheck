"""Re-ground saved raw model responses offline; never reuse grounded cache issues.

This measures code changes on old outputs, not a new-model/prompt experiment.
"""
import argparse
import json
from pathlib import Path

from run_reasoning_diagnostics import build_cases, fresh_directory
from src.evaluation.pipeline_diagnostics import summarize_pipeline
from scripts._rag_experiment_utils import sha256, read_json, write_json
from src.evaluation.compliance_reference import compare_assistant_reference
from src.extraction.schema import StructuredExtractionResult, TenderConstraintSlots, BidEvidenceSlots, VALID_CONSTRAINT_TYPES
from src.extraction.evidence import ground_extraction
from src.extraction.llm_extractor import CONTRACT_VERSION
from src.rules import ComplianceRuleEngine


def load_raw_responses(directory, cases):
    events = read_json(directory/'api_calls.json')
    expected = {case['clause_id'] for case in cases}
    ids = [event['clause_id'] for event in events]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError('Replay requires exactly one recorded response per case; select retry responses explicitly')
    payloads, hashes = {}, {}
    for index, event in enumerate(events, 1):
        # Construct filenames only after checking IDs against the frozen case set.
        clause_id = event['clause_id']
        if not clause_id.isalnum():
            raise ValueError('Invalid clause ID')
        path = directory/f'raw_{clause_id}_{index:03d}.json'
        raw = read_json(path)
        if (raw.get('id') != event['response_id'] or raw.get('model') != event['model']
                or raw['choices'][0].get('finish_reason') != 'stop'):
            raise ValueError('Response manifest mismatch or truncated response')
        payload = json.loads(raw['choices'][0]['message']['content'])
        if (not isinstance(payload, dict) or not isinstance(payload.get('tender_slots'), dict)
                or not isinstance(payload.get('bid_slots'), dict)):
            raise ValueError('Missing extraction slot objects')
        payloads[clause_id] = (payload, raw.get('model'))
        hashes[path.name] = sha256(path)
    return payloads, hashes


def replay(args):
    bundle = read_json(args.cases)
    baseline = read_json(args.raw_dir/'metrics.json')
    retrieval_path = Path(bundle['source_retrieval'])
    if (bundle.get('schema_version') != 1 or sha256(args.cases) != baseline['cases_sha256']
            or sha256(retrieval_path) != bundle['retrieval_sha256']
            or bundle['retrieval_sha256'] != baseline['retrieval_sha256']):
        raise ValueError('Frozen case/retrieval hash mismatch')
    cases = bundle['cases']
    if build_cases(read_json(retrieval_path), bundle['method']) != cases:
        raise ValueError('Cases do not match frozen retrieved passages')
    old_predictions = read_json(args.raw_dir/'predictions.json')
    summarize_pipeline(cases, old_predictions)
    old = {row['clause_id']: row for row in old_predictions}
    payloads, raw_hashes = load_raw_responses(args.raw_dir, cases)
    extractions, predictions = [], []
    for case in cases:
        payload, model = payloads[case['clause_id']]
        tender = TenderConstraintSlots.from_dict(payload['tender_slots'])
        if tender.constraint_type not in VALID_CONSTRAINT_TYPES:
            raise ValueError('Unknown constraint type')
        result = StructuredExtractionResult(case['clause_id'], case['requirement'], case['category'],
            tender, BidEvidenceSlots.from_dict(payload['bid_slots']), extractor_backend=model)
        result = ground_extraction(result, case['evidence_context'])
        extractions.append(result.to_dict())
        predictions.append(ComplianceRuleEngine().evaluate(result).to_dict())
    changes = [dict(clause_id=r['clause_id'], before=old[r['clause_id']]['verdict'], after=r['verdict'],
                    before_score=old[r['clause_id']].get('awarded_score'), after_score=r.get('awarded_score'),
                    explanation=r['explanation']) for r in predictions
               if (r['verdict'], r.get('awarded_score')) !=
                  (old[r['clause_id']]['verdict'], old[r['clause_id']].get('awarded_score'))]
    metrics = summarize_pipeline(cases, predictions)
    metrics.update(mode='offline_raw_response_replay', new_api_calls=0, new_api_tokens=0,
        original_extraction_contract=baseline['extraction_contract'],
        current_code_contract=CONTRACT_VERSION,
        prompt_note='Old model outputs were revalidated with new code. The new prompt was NOT tested online.',
        cases_sha256=sha256(args.cases), retrieval_sha256=bundle['retrieval_sha256'],
        raw_response_sha256=raw_hashes, api_manifest_sha256=sha256(args.raw_dir/'api_calls.json'),
        baseline_predictions_sha256=sha256(args.raw_dir/'predictions.json'),
        baseline_metrics_sha256=sha256(args.raw_dir/'metrics.json'),
        cases_with_grounding_issues=sum(bool(e['extraction_issues']) for e in extractions),
        changed_cases=changes)
    base = Path(__file__).resolve().parent
    metrics['code_sha256'] = {name:sha256(base/name) for name in (
        'replay_compliance_responses.py', 'src/extraction/evidence.py', 'src/extraction/semantics.py',
        'src/extraction/conflicts.py', 'src/extraction/schema.py', 'src/extraction/llm_extractor.py',
        'src/rules/rule_engine.py', 'run_reasoning_diagnostics.py', 'src/evaluation/pipeline_diagnostics.py', 'src/evaluation/compliance_reference.py')}
    reference = compare_assistant_reference(cases, predictions, read_json(args.reference_labels)) if args.reference_labels else None
    if reference:
        metrics['reference_labels_sha256'] = sha256(args.reference_labels)
        metrics['assistant_reference_comparison_file'] = 'reference_comparison.json'
    fresh_directory(args.output_dir, [args.cases, args.raw_dir, retrieval_path]
                    + ([args.reference_labels] if args.reference_labels else []))
    for name, data in [('extractions',extractions), ('predictions',predictions), ('metrics',metrics)]:
        write_json(args.output_dir/f'{name}.json', data)
    if reference:
        write_json(args.output_dir/'reference_comparison.json', reference)
    print(json.dumps({key:value for key,value in metrics.items() if key not in {'raw_response_sha256','code_sha256'}},
                     ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--raw_dir', type=Path, required=True)
    parser.add_argument('--output_dir', type=Path, required=True)
    parser.add_argument('--reference_labels', type=Path)
    return replay(parser.parse_args())


if __name__ == '__main__':
    raise SystemExit(main())

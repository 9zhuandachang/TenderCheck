"""Diagnose the downstream pipeline on frozen RAG outputs; not a component benchmark."""
import argparse
import json
import time
from pathlib import Path

from src.extraction import DeepSeekSlotExtractor, HeuristicSlotExtractor
from src.extraction.evidence import evidence_context
from src.extraction.llm_extractor import CONTRACT_VERSION
from src.rules import ComplianceRuleEngine
from src.evaluation.compliance_reference import compare_assistant_reference
from src.evaluation.pipeline_diagnostics import summarize_pipeline, compare_human_end_to_end
from evaluate_evidence_rag import verify_source_files
from scripts._rag_experiment_utils import sha256, read_json, write_json, require_separate_directories


def build_cases(retrieval, method):
    if retrieval.get('status') != 'complete' or retrieval.get('schema_version') != 2:
        raise ValueError('A completed passage retrieval run (schema 2) is required')
    if method not in retrieval['methods']:
        raise ValueError('Unknown retrieval method')
    cases=[];seen=set()
    for q in retrieval['queries']:
        if q['clause_id'] in seen or not q['requirement'].strip():
            raise ValueError('Duplicate ID or empty requirement')
        seen.add(q['clause_id'])
        passages=q['methods'][method]
        if len(passages)>retrieval['top_k']:
            raise ValueError('Retrieval depth exceeded')
        for i,p in enumerate(passages,1):
            source=retrieval['sources'].get(p['document_id'])
            if (not source or source['sha256']!=p['source_sha256'] or p['rank']!=i
                    or not 1<=p['page_start']<=p['page_end']<=source['page_count']
                    or not isinstance(p['parent_text'],str) or not p['parent_text'].strip()):
                raise ValueError('Invalid source, rank, pages or empty passage')
        cases.append(dict(clause_id=q['clause_id'],requirement=q['requirement'],
                          category=q['predicted_category'],evidence_context=evidence_context(passages)))
    if not cases:
        raise ValueError('No retrieval queries')
    return cases


def fresh_directory(path, inputs):
    for source in inputs:
        require_separate_directories(source,path)
    if path.exists() and any(path.iterdir()):
        raise ValueError('Use a new or empty output directory')
    path.mkdir(parents=True,exist_ok=True)


def prepare(args):
    raw=read_json(args.retrieval)
    verify_source_files(raw['sources'])
    cases=build_cases(raw,args.method)
    fresh_directory(args.output_dir,[args.retrieval])
    target=args.output_dir/'cases.json'
    write_json(target,dict(schema_version=1,evaluation_scope='pipeline_diagnostics',source_retrieval=str(args.retrieval.resolve()),
                          retrieval_sha256=sha256(args.retrieval),method=args.method,cases=cases))
    print(json.dumps(dict(prepared_cases=len(cases),empty_retrievals=sum(not json.loads(c['evidence_context']) for c in cases),
                         evaluation_scope='pipeline_diagnostics',
                         note='No verdict labels created. RAG examples remain retrieval examples.')))
    return 0


def run(args):
    bundle=read_json(args.cases)
    if bundle.get('schema_version')!=1:
        raise ValueError('Unsupported case schema')
    original=Path(bundle['source_retrieval'])
    if sha256(original)!=bundle['retrieval_sha256']:
        raise ValueError('Frozen retrieval changed')
    retrieval=read_json(original)
    verify_source_files(retrieval['sources'])
    cases=build_cases(retrieval,bundle['method'])
    if cases!=bundle['cases']:
        raise ValueError('Case evidence changed after preparation')
    labels=read_json(args.labels) if args.labels else None
    reference=read_json(args.reference_labels) if args.reference_labels else None
    placeholders=[dict(clause_id=c['clause_id'],verdict='ERROR') for c in cases]
    # Validate labels before any paid call. Neither reference set enters extraction.
    summarize_pipeline(cases,placeholders)
    if labels is not None:
        compare_human_end_to_end(cases,placeholders,labels,sha256(args.cases))
    if reference:
        compare_assistant_reference(cases,placeholders,reference)
    fresh_directory(args.output_dir,[args.cases,original]+([args.labels] if args.labels else [])
                    +([args.reference_labels] if args.reference_labels else []))
    api_calls=[];active={}

    def record_response(response):
        event=dict(clause_id=active['clause_id'],model=response.get('model'),
                   usage=response.get('usage'),response_id=response.get('id'),
                   finish_reason=response.get('choices',[{}])[0].get('finish_reason'))
        api_calls.append(event)
        write_json(args.output_dir/f"raw_{active['clause_id']}_{len(api_calls):03d}.json",response)
        write_json(args.output_dir/'api_calls.json',api_calls)

    extractor=(HeuristicSlotExtractor() if args.backend=='heuristic' else
               DeepSeekSlotExtractor(model=args.model,base_url=args.base_url,cache_path=args.cache,
                                     cache_only=args.cache_only,response_hook=record_response,
                                     max_retries=args.max_retries,timeout=args.timeout))
    engine=ComplianceRuleEngine();results=[];extractions=[]
    for case in cases:
        active['clause_id']=case['clause_id']
        started=time.perf_counter()
        try:
            extracted=extractor.extract(clause_id=case['clause_id'],requirement_text=case['requirement'],
                category=case['category'],evidence_context=case['evidence_context'])
            extractions.append(extracted.to_dict())
            results.append(engine.evaluate(extracted).to_dict())
        except Exception as exc:
            results.append(dict(clause_id=case['clause_id'],verdict='ERROR',error=str(exc)))
        write_json(args.output_dir/'extractions.json',extractions)
        write_json(args.output_dir/'predictions.json',results)
        print(json.dumps(dict(progress=f'{len(results)}/{len(cases)}',clause_id=case['clause_id'],
                              verdict=results[-1]['verdict'],
                              seconds=round(time.perf_counter()-started,2))),flush=True)
    report=summarize_pipeline(cases,results)
    if labels is not None:
        write_json(args.output_dir/'human_end_to_end_comparison.json',
                   compare_human_end_to_end(cases,results,labels,sha256(args.cases)))
    report.update(status='failed' if any(r['verdict']=='ERROR' for r in results) else 'complete',
                  backend=args.backend,model=args.model if args.backend=='deepseek' else None,
                  method=bundle['method'],cases_sha256=sha256(args.cases),retrieval_sha256=bundle['retrieval_sha256'],
                  labels_sha256=sha256(args.labels) if args.labels else None)
    report.update(received_api_responses=len(api_calls),
                  actual_response_models=sorted({e['model'] for e in api_calls if e.get('model')}),
                  usage_totals={key:sum((e.get('usage') or {}).get(key,0) or 0 for e in api_calls)
                                for key in ('prompt_tokens','completion_tokens','total_tokens')},
                  billing_cost=None,
                  usage_note='Received API responses only; cached cases consume no new API tokens. Actual billing is unavailable.')
    if reference:
        agreement=compare_assistant_reference(cases,results,reference)
        write_json(args.output_dir/'reference_comparison.json',agreement)
        report['reference_labels_sha256']=sha256(args.reference_labels)
        report['assistant_reference_comparison_file']='reference_comparison.json'
    base=Path(__file__).resolve().parent
    report['extraction_contract']=CONTRACT_VERSION
    report['code_sha256']={name:sha256(base/name) for name in (
        'run_reasoning_diagnostics.py','src/evaluation/pipeline_diagnostics.py','src/extraction/llm_extractor.py','src/extraction/evidence.py',
        'src/extraction/schema.py','src/extraction/semantics.py','src/extraction/conflicts.py','src/rules/rule_engine.py',
        'src/evaluation/compliance_reference.py')}
    write_json(args.output_dir/'metrics.json',report)
    (args.output_dir/'report.md').write_text('# 下游流程诊断（非模块准确率评测）\n\n'
        +f"后端：{args.backend}；状态：{report['status']}；需求数：{len(cases)}。\n\n"
        +'仅统计流程输出与判断覆盖率，不表示 RAG、抽取或规则准确率。证据不足不能自动归因于检索。\n\n'
        +'```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 2 if report['status']=='failed' else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('prepare',help='Use actual ranked passages, preserving empty retrievals')
    p.add_argument('--retrieval',type=Path,required=True)
    p.add_argument('--method',default='predicted_category_rerank')
    p.add_argument('--output_dir',type=Path,required=True)
    p=commands.add_parser('run',help='Run downstream pipeline diagnostics; no component accuracy')
    p.add_argument('--cases',type=Path,required=True)
    p.add_argument('--labels',type=Path,help='Optional human end-to-end labels with explicit scope; scored in a separate file')
    p.add_argument('--reference_labels',type=Path,help='Calibrated assistant references; exploratory agreement only')
    p.add_argument('--backend',choices=['heuristic','deepseek'],required=True)
    p.add_argument('--model',default='deepseek-chat')
    p.add_argument('--base_url',default='https://api.deepseek.com')
    p.add_argument('--cache',type=Path)
    p.add_argument('--cache_only',action='store_true')
    p.add_argument('--max_retries',type=int,default=1)
    p.add_argument('--timeout',type=float,default=90)
    p.add_argument('--output_dir',type=Path,required=True)
    args=parser.parse_args()
    return prepare(args) if args.command=='prepare' else run(args)


if __name__=='__main__':
    raise SystemExit(main())

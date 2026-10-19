"""Evaluate extraction and rules on frozen, actual passage retrieval outputs."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from src.extraction import DeepSeekSlotExtractor, HeuristicSlotExtractor
from src.extraction.evidence import evidence_context
from src.extraction.llm_extractor import CONTRACT_VERSION
from src.rules import ComplianceRuleEngine
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


def compute_metrics(cases, results, labels=None, cases_sha256=None):
    ids={c['clause_id'] for c in cases}
    if len(results)!=len(cases) or {r['clause_id'] for r in results}!=ids:
        raise ValueError('Predictions must match cases exactly')
    reviewed={}
    if labels is not None:
        if labels.get('schema_version')!=1 or labels.get('cases_sha256')!=cases_sha256:
            raise ValueError('Labels belong to a different case set')
        label_ids=[q['clause_id'] for q in labels['queries']]
        if len(set(label_ids))!=len(label_ids) or set(label_ids)!=ids:
            raise ValueError('Label IDs must match cases')
        for q in labels['queries']:
            if q['review_status']=='pending':continue
            if q['review_status']!='human_verified' or not q.get('reviewer') or not q.get('review_note'):
                raise ValueError('Only documented human-reviewed verdict labels may be scored')
            if q['expected_verdict'] not in {'COMPLIANT','NON_COMPLIANT','INSUFFICIENT_EVIDENCE','CONFLICTING_EVIDENCE','SCORED','UNSUPPORTED_RULE'}:
                raise ValueError('Invalid verdict label')
            reviewed[q['clause_id']]=q['expected_verdict']
    counts=Counter(r['verdict'] for r in results)
    correct=sum(r['verdict']==reviewed[r['clause_id']] for r in results if r['clause_id'] in reviewed)
    decided=sum(r['verdict'] in {'COMPLIANT','NON_COMPLIANT'} for r in results)
    return dict(total_cases=len(cases),human_labelled_cases=len(reviewed),pending_labels=len(cases)-len(reviewed),
                verdict_counts=dict(counts),decision_coverage=decided/len(cases) if cases else None,
                verdict_accuracy=correct/len(reviewed) if reviewed else None,
                note='Coverage is not accuracy. Missing evidence is not proof of noncompliance. Scoring accuracy requires separate score labels.')


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
    write_json(target,dict(schema_version=1,source_retrieval=str(args.retrieval.resolve()),
                          retrieval_sha256=sha256(args.retrieval),method=args.method,cases=cases))
    write_json(args.output_dir/'labels.json',dict(schema_version=1,cases_sha256=sha256(target),queries=[
        dict(clause_id=c['clause_id'],review_status='pending',reviewer='',review_note='',expected_verdict=None)
        for c in cases]))
    print(json.dumps(dict(prepared_cases=len(cases),empty_retrievals=sum(not json.loads(c['evidence_context']) for c in cases),
                         labels='All pending; no automatic positive or synthetic negative labels')))
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
    fresh_directory(args.output_dir,[args.cases,original]+([args.labels] if args.labels else []))
    extractor=(HeuristicSlotExtractor() if args.backend=='heuristic' else
               DeepSeekSlotExtractor(model=args.model,base_url=args.base_url,cache_path=args.cache,
                                     cache_only=args.cache_only))
    engine=ComplianceRuleEngine();results=[];extractions=[]
    for case in cases:
        try:
            extracted=extractor.extract(clause_id=case['clause_id'],requirement_text=case['requirement'],
                category=case['category'],evidence_context=case['evidence_context'])
            extractions.append(extracted.to_dict())
            results.append(engine.evaluate(extracted).to_dict())
        except Exception as exc:
            results.append(dict(clause_id=case['clause_id'],verdict='ERROR',error=str(exc)))
        write_json(args.output_dir/'extractions.json',extractions)
        write_json(args.output_dir/'predictions.json',results)
    report=compute_metrics(cases,results,labels,sha256(args.cases))
    report.update(status='failed' if any(r['verdict']=='ERROR' for r in results) else 'complete',
                  backend=args.backend,model=args.model if args.backend=='deepseek' else None,
                  method=bundle['method'],cases_sha256=sha256(args.cases),retrieval_sha256=bundle['retrieval_sha256'],
                  labels_sha256=sha256(args.labels) if args.labels else None)
    base=Path(__file__).resolve().parent
    report['extraction_contract']=CONTRACT_VERSION
    report['code_sha256']={name:sha256(base/name) for name in (
        'evaluate_compliance_reasoning.py','src/extraction/llm_extractor.py','src/extraction/evidence.py',
        'src/extraction/schema.py','src/rules/rule_engine.py')}
    write_json(args.output_dir/'metrics.json',report)
    (args.output_dir/'report.md').write_text('# 真实段落输入的提取与规则检查\n\n'
        +f"后端：{args.backend}；状态：{report['status']}；需求数：{len(cases)}。\n\n"
        +f"人工标签：{report['human_labelled_cases']}；准确率：{report['verdict_accuracy']}。无人工标签时不生成准确率。\n\n"
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
    p=commands.add_parser('run',help='Extract facts and evaluate rules; no presumed verdict labels')
    p.add_argument('--cases',type=Path,required=True)
    p.add_argument('--labels',type=Path)
    p.add_argument('--backend',choices=['heuristic','deepseek'],required=True)
    p.add_argument('--model',default='deepseek-chat')
    p.add_argument('--base_url',default='https://api.deepseek.com')
    p.add_argument('--cache',type=Path)
    p.add_argument('--cache_only',action='store_true')
    p.add_argument('--output_dir',type=Path,required=True)
    args=parser.parse_args()
    return prepare(args) if args.command=='prepare' else run(args)


if __name__=='__main__':
    raise SystemExit(main())

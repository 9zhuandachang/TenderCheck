"""Evidence-only input and mechanically checked provenance for slot extraction."""
import json
import math
import re
import unicodedata
from decimal import Decimal
from src.evaluation.evidence import compact
from .semantics import (
    explicit_numeric_operator,
    required_qualification_role,
    role_names,
    has_document_body,
    qualification_owned_by,
    text_fact_supported,
    manager_performance_linked,
)
from .conflicts import verify_conflict_pairs


def evidence_context(passages):
    """Only serialized passages cross this boundary; no cached business facts."""
    return json.dumps([
        dict(evidence_id=f'E{i}', document_id=p['document_id'], source_sha256=p['source_sha256'],
             page_start=p['page_start'], page_end=p['page_end'], section=p.get('section_path',''),
             text=p['parent_text'])
        for i, p in enumerate(passages, 1)
    ], ensure_ascii=False)


def numeric_in_quote(value, quote):
    # Whitespace separates table cells; removing it joins adjacent numbers.
    text = unicodedata.normalize('NFKC', quote)
    numbers = re.findall(r'(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])', text)
    return any(Decimal(n.replace(',', '')) == Decimal(str(value)) for n in numbers)


def unit_in_quote(unit, quote):
    text=compact(quote)
    if unit == '元':
        return bool(re.search(r'(?<!万)(?<!亿)元',text)) or ('投标报价汇总表' in text and '金额' in text) or ('工程量清单表' in text and '合价' in text)
    return bool(compact(unit)) and compact(unit) in text


def original_quote_span(text, quote):
    """Recover source whitespace so a model cannot join distinct numeric cells."""
    text = unicodedata.normalize('NFKC',text)
    positions = [i for i,c in enumerate(text) if not c.isspace()]
    needle = compact(quote)
    start = ''.join(text[i] for i in positions).find(needle)
    if not needle or start < 0:
        return None
    return text[positions[start]:positions[start+len(needle)-1]+1]


def ground_extraction(result, context):
    """Check references and literal values; this does not certify semantic entailment."""
    evidence = json.loads(context)
    by_id = {p['evidence_id']: p for p in evidence}
    b, t = result.bid_slots, result.tender_slots
    issues = []
    verified = []
    by_field = {}
    for ref in b.evidence_references:
        if not isinstance(ref, dict):
            issues.append('Invalid evidence reference'); continue
        passage = by_id.get(ref.get('evidence_id'))
        quote, field = ref.get('quote'), ref.get('field')
        if (passage is None or not isinstance(quote, str) or not compact(quote)
                or compact(quote) not in compact(passage['text']) or field not in b.__dataclass_fields__):
            issues.append('Reference is not an exact quote from the supplied passage'); continue
        quote = original_quote_span(passage['text'],quote)
        verified.append(dict(field=field, evidence_id=ref['evidence_id'], quote=quote,
                             document_id=passage['document_id'], source_sha256=passage['source_sha256'],
                             page_start=passage['page_start'], page_end=passage['page_end']))
        by_field.setdefault(field, []).append(quote)
    b.evidence_references = verified
    b.evidence_pages = sorted({page for r in verified for page in range(r['page_start'], r['page_end']+1)})
    # Text alone cannot certify the actual image's stamp or signature.
    b.has_official_seal = b.has_signature = None
    role_support_refs = set()
    for field in ('expected_numeric','expected_unit','secondary_numeric','expected_text','required_domain',
                  'scoring_max_points','scoring_bands','requires_official_seal','requires_signature'):
        value = getattr(t, field)
        if value is None or value == '' or value == [] or value is False or (field == 'expected_unit' and t.constraint_type == 'FORMULA_BOUND' and not t.source_quotes.get(field)):
            continue
        quote = t.source_quotes.get(field)
        if not isinstance(quote, str) or not compact(quote) or compact(quote) not in compact(result.requirement_text):
            issues.append(f'Missing tender quotation: {field}')
        elif type(value) in (float,int) and not numeric_in_quote(value, quote):
            issues.append(f'Tender numeric value not stated in quote: {field}')
        elif field == 'expected_unit' and not unit_in_quote(value,quote):
            issues.append('Tender unit is not stated in quote')
    if t.constraint_type == 'NUMERIC_BOUND' and t.expected_numeric is not None:
        expected_op = explicit_numeric_operator(result.requirement_text, t.expected_numeric)
        if expected_op is None:
            issues.append('Numeric comparison operator is not explicit or is ambiguous in the requirement')
        elif t.operator != expected_op:
            issues.append(f'Numeric comparison operator contradicts requirement: expected {expected_op}, got {t.operator}')
        if t.secondary_numeric is not None and explicit_numeric_operator(result.requirement_text,t.secondary_numeric) != '==':
            issues.append('Secondary numeric constraint is not an explicit equality')

    # Numeric amounts and their units must be linked in the same cited source.
    if b.actual_numeric is not None and b.actual_unit:
        linked = False
        for nr in verified:
            if nr['field'] != 'actual_numeric' or not numeric_in_quote(b.actual_numeric,nr['quote']):
                continue
            for ur in verified:
                if ur['field'] != 'actual_unit' or ur['evidence_id'] != nr['evidence_id']:
                    continue
                if unit_in_quote(b.actual_unit,ur['quote']) and (
                    numeric_in_quote(b.actual_numeric,ur['quote']) or
                    re.search(r'(?:单位|金额|报价|总价)\s*[:：|(（]',ur['quote'])):
                    linked = True
        if not linked:
            issues.append('Numeric value and unit lack a linked source quotation')
    if b.secondary_actual_numeric is not None:
        secondary_refs = [r for r in verified if r['field']=='secondary_actual_numeric']
        if not secondary_refs or not all(unit_in_quote(b.actual_unit,r['quote']) or any(
                u['field']=='actual_unit' and u['evidence_id']==r['evidence_id'] and unit_in_quote(b.actual_unit,u['quote'])
                and re.search(r'(?:单位|金额|报价|总价)\s*[:：|(（]',u['quote']) for u in verified) for r in secondary_refs):
            issues.append('Secondary numeric value has no explicit same-unit source citation')

    if b.document_present is True and not any(has_document_body(q,result.requirement_text) for q in by_field.get('document_present',[])):
        issues.append('Document presence requires substantive body quotation, not a heading or template')
    if b.document_present is False and not any(re.search(r'未提供|未提交|缺少|缺失',q) for q in by_field.get('document_present',[])):
        issues.append('Document absence requires explicit absence evidence')
    if b.document_present is True and '项目经理业绩' in result.requirement_text:
        current_names = set().union(*(role_names(p['text'],'project_manager') for p in evidence
            if re.search(r'拟派|拟任|拟委任|项目管理机构',p['text'])))
        documents = [by_id[r['evidence_id']]['text'] for r in verified if r['field']=='document_present']
        if len(current_names) == 1 and not manager_performance_linked(documents, next(iter(current_names))):
            issues.append('Performance document lacks a link to the currently proposed project manager')

    if b.conflict_detected:
        b.conflict_pairs, conflict_issues = verify_conflict_pairs(b.conflict_pairs,by_id,result.requirement_text)
        issues.extend(conflict_issues)
    else:
        b.conflict_pairs = []

    if t.constraint_type == 'QUALIFICATION_LEVEL' and b.evidence_found:
        role = required_qualification_role(result.requirement_text)
        if role is None:
            issues.append('Qualification subject role is not supported; review required')
        identities = set().union(*(role_names(p['text'],role) for p in evidence))
        if len(identities) != 1:
            issues.append('Qualification subject is missing or ambiguous in explicit role statements')
        required_fields = [f for f in ('actual_text','actual_domain','certificate_valid') if getattr(b,f) not in (None,'')]
        supported = set()
        supported_references = set()
        verified_bindings = []
        for link in b.subject_bindings:
            if not isinstance(link,dict):
                issues.append('Invalid qualification subject binding'); continue
            field, eid = link.get('field'),link.get('evidence_id')
            # Bindings for unset facts make no factual claim and are not retained.
            if field in ('actual_text','actual_domain','certificate_valid') and getattr(b,field) in (None,''):
                continue
            name, quote, role_quote = link.get('subject_name'),link.get('quote'),link.get('role_quote')
            proof, anchor = by_id.get(eid),by_id.get(link.get('role_evidence_id'))
            if (field not in required_fields or link.get('role') != role or not all(isinstance(x,str) and compact(x) for x in (name,quote,role_quote))
                    or proof is None or anchor is None or compact(quote) not in compact(proof['text'])
                    or compact(role_quote) not in compact(anchor['text']) or compact(name) not in compact(quote)
                    or compact(name) not in role_names(role_quote,role) or identities != {compact(name)}):
                issues.append('Qualification subject binding is not supported by role and source quotations'); continue
            quote = original_quote_span(proof['text'],quote)
            role_quote = original_quote_span(anchor['text'],role_quote)
            refs = [r for r in verified if r['field']==field and r['evidence_id']==eid]
            if not refs or not any(compact(r['quote']) in compact(quote) for r in refs):
                issues.append('Qualification fact is not contained in its subject quotation'); continue
            if not any(qualification_owned_by(quote,name,r['quote'],role) for r in refs):
                issues.append('Qualification quotation does not link the fact to the labelled owner'); continue
            supported.add(field)
            supported_references.update((r['field'],r['evidence_id'],r['quote']) for r in refs
                if compact(r['quote']) in compact(quote) and qualification_owned_by(quote,name,r['quote'],role))
            for ref in refs:
                if ref['evidence_id']==link['role_evidence_id'] and compact(ref['quote'])==compact(role_quote):
                    # Identity quotations support attribution, not the qualification value.
                    key=(ref['field'],ref['evidence_id'],ref['quote'])
                    role_support_refs.add(key)
                    supported_references.add(key)
            verified_bindings.append(dict(field=field,evidence_id=eid,subject_name=name,role=role,quote=quote,
                document_id=proof['document_id'],source_sha256=proof['source_sha256'],
                page_start=proof['page_start'],page_end=proof['page_end'],role_evidence_id=link['role_evidence_id'],
                role_quote=role_quote,role_document_id=anchor['document_id'],role_source_sha256=anchor['source_sha256'],
                role_page_start=anchor['page_start'],role_page_end=anchor['page_end']))
        b.subject_bindings = verified_bindings
        for field in required_fields:
            if field not in supported:
                issues.append(f'Missing verified qualification subject binding: {field}')
        for ref in verified:
            if ref['field'] in required_fields and (ref['field'],ref['evidence_id'],ref['quote']) not in supported_references:
                issues.append(f'Qualification citation lacks subject attribution: {ref["field"]}/{ref["evidence_id"]}')
        # A self-filled basic info table (投标人基本情况表) without certificate number/validity cannot certify statutory license validity.
        if not b.evidence_source_type:
            qual_passages = [by_id[r['evidence_id']]['text'] for r in verified if r['field'] == 'actual_text' and r['evidence_id'] in by_id]
            if qual_passages and all(
                '基本情况表' in p and not re.search(r'有效期|证书编号|许可证编号|发证机关', p)
                for p in qual_passages
            ):
                b.evidence_source_type = 'SELF_DECLARED_TABLE'

    facts = ('document_present','actual_numeric','actual_unit','secondary_actual_numeric','actual_text','actual_domain','upper_amount_text',
             'entity_primary','entity_secondary','certificate_valid','prohibition_violated',
             'commitment_fulfilled','qualitative_match','estimated_score')
    for field in facts:
        value = getattr(b, field)
        if value is None or value == '':
            continue
        quotes = [r['quote'] for r in verified if r['field']==field and
                  (r['field'],r['evidence_id'],r['quote']) not in role_support_refs]
        if not quotes:
            issues.append(f'Missing source citation: {field}')
        elif type(value) in (float, int):
            if not math.isfinite(value) or not all(numeric_in_quote(value, q) for q in quotes):
                issues.append(f'Numeric fact is not stated in cited text: {field}')
        elif field == 'actual_unit':
            if not all(unit_in_quote(value,q) for q in quotes):
                issues.append('Actual unit is not stated in cited text')
    

        elif isinstance(value, str) and not all(
                text_fact_supported(value,q) if field=='actual_text' else compact(value) in compact(q) for q in quotes):
            issues.append(f'Text fact is not stated in cited text: {field}')
    for structured_field in ('personnel_records', 'performance_records', 'component_amounts'):
        if getattr(b, structured_field) and not by_field.get(structured_field):
            issues.append(f'Missing source citation: {structured_field}')
    if b.entity_primary and b.entity_secondary:
        def locations(field):
            return {(r['source_sha256'],r['page_start'],r['page_end'],compact(r['quote']))
                    for r in verified if r['field']==field}
        primary,secondary=locations('entity_primary'),locations('entity_secondary')
        if not primary or not secondary or not any(a!=c for a in primary for c in secondary):
            issues.append('Consistency comparison needs two separately identified source statements')
    if b.evidence_found and not verified:
        issues.append('Relevant evidence claimed without any valid citation')
    if not evidence:
        b.evidence_found = False
    result.extraction_issues = sorted(set(result.extraction_issues + issues))
    return result

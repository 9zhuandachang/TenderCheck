"""Evidence-only input and mechanically checked provenance for slot extraction."""
import json
import math
import re
from src.evaluation.evidence import compact


def evidence_context(passages):
    """Only serialized passages cross this boundary; no cached business facts."""
    return json.dumps([
        dict(evidence_id=f'E{i}', document_id=p['document_id'], source_sha256=p['source_sha256'],
             page_start=p['page_start'], page_end=p['page_end'], section=p.get('section_path',''),
             text=p['parent_text'])
        for i, p in enumerate(passages, 1)
    ], ensure_ascii=False)


def numeric_in_quote(value, quote):
    numbers = re.findall(r'(?<![\d.])-?\d+(?:\.\d+)?', compact(quote).replace(',', ''))
    return any(float(n) == value for n in numbers)


def unit_in_quote(unit, quote):
    text=compact(quote)
    if unit == '元':
        return bool(re.search(r'(?<!万)(?<!亿)元',text))
    return bool(compact(unit)) and compact(unit) in text


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
        verified.append(dict(field=field, evidence_id=ref['evidence_id'], quote=quote,
                             document_id=passage['document_id'], source_sha256=passage['source_sha256'],
                             page_start=passage['page_start'], page_end=passage['page_end']))
        by_field.setdefault(field, []).append(quote)
    b.evidence_references = verified
    b.evidence_pages = sorted({page for r in verified for page in range(r['page_start'], r['page_end']+1)})
    # Text alone cannot certify the actual image's stamp or signature.
    b.has_official_seal = b.has_signature = None
    facts = ('document_present','actual_numeric','actual_unit','secondary_actual_numeric','actual_text','actual_domain','upper_amount_text',
             'entity_primary','entity_secondary','certificate_valid','prohibition_violated',
             'commitment_fulfilled','qualitative_match','estimated_score')
    for field in facts:
        value = getattr(b, field)
        if value is None or value == '':
            continue
        quotes = by_field.get(field, [])
        if not quotes:
            issues.append(f'Missing source citation: {field}')
        elif type(value) in (float, int):
            if not math.isfinite(value) or not any(numeric_in_quote(value, q) for q in quotes):
                issues.append(f'Numeric fact is not stated in cited text: {field}')
        elif field == 'actual_unit' and not any(unit_in_quote(value,q) for q in quotes):
            issues.append('Actual unit is not stated in cited text')
        elif isinstance(value, str) and not any(compact(value) in compact(q) for q in quotes):
            issues.append(f'Text fact is not stated in cited text: {field}')
    for field in ('expected_numeric','expected_unit','secondary_numeric','expected_text','required_domain',
                  'scoring_max_points','scoring_bands','requires_official_seal','requires_signature'):
        value = getattr(t, field)
        if value is None or value == '' or value == [] or value is False:
            continue
        quote = t.source_quotes.get(field)
        if not isinstance(quote, str) or not compact(quote) or compact(quote) not in compact(result.requirement_text):
            issues.append(f'Missing tender quotation: {field}')
        elif type(value) in (float,int) and not numeric_in_quote(value, quote):
            issues.append(f'Tender numeric value not stated in quote: {field}')
        elif field == 'expected_unit' and not unit_in_quote(value,quote):
            issues.append('Tender unit is not stated in quote')
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

"""Conservative two-sided conflict support; uncertain comparisons remain unknown."""
import re
from decimal import Decimal

from src.evaluation.evidence import compact
from .semantics import normalized, owner_blocks, role_names


def verify_conflict_pairs(pairs, evidence_by_id, requirement):
    verified, issues = [], []
    for pair in pairs:
        if not isinstance(pair, dict):
            issues.append('Invalid conflict pair')
            continue
        subject, aspect, period = (pair.get(k) for k in ('subject', 'aspect', 'period'))
        role = pair.get('role')
        if (not all(isinstance(s, str) and compact(s) for s in (subject, aspect, period))
                or compact(aspect) not in compact(requirement)
                or role not in {'bidder', 'project_manager', 'chief_engineer',
                                'road_engineer', 'bridge_engineer', 'safety_officer'}):
            issues.append('Conflict requires an explicit subject, requirement aspect and common period')
            continue
        anchor = evidence_by_id.get(pair.get('role_evidence_id'))
        role_quote = pair.get('role_quote')
        if (not anchor or not isinstance(role_quote, str) or not compact(role_quote)
                or compact(role_quote) not in compact(anchor['text'])
                or role_names(role_quote, role) != {compact(subject)}):
            issues.append('Conflict subject lacks an explicit current-role quotation')
            continue
        sides = []
        for side in ('left', 'right'):
            ref = pair.get(side)
            if not isinstance(ref, dict):
                break
            passage = evidence_by_id.get(ref.get('evidence_id'))
            quote, value = ref.get('quote'), ref.get('value')
            if (passage is None or not isinstance(quote, str) or not compact(quote)
                    or normalized(quote) not in normalized(passage['text'])
                    or (not isinstance(value, str) or not compact(value))):
                break
            # The value must be attached directly to this aspect, not merely
            # appear elsewhere in a long quote (e.g. another person's record).
            pattern = re.escape(normalized(aspect)) + r'\s*[:：|]\s*' + re.escape(normalized(value)) + r'(?=\s|[|，,；;。]|$)'
            blocks = owner_blocks(quote, subject, role)
            # One period label and one labelled fact in the same owner block.
            # Longer histories with multiple periods are deliberately undecided.
            def supports(block):
                periods = re.findall(r'(?:项目|年度|时期)\s*[:：|]\s*([^\n|，,；;。]+)', block)
                aspects = re.findall(re.escape(normalized(aspect)) + r'\s*[:：|]', block)
                return (len(periods) == 1 and compact(periods[0]) == compact(period)
                        and len(aspects) == 1 and re.search(pattern, block))
            pm_active_conflict = (
                role == 'project_manager'
                and (
                    (compact(value) == '在建' and bool(re.search(r'担任项目经理岗位|在其他项目上任职|现任.*项目经理', compact(quote))))
                    or (compact(value) == '无在建' and '拟派项目经理无在建工程' in compact(quote))
                )
            )
            if not pm_active_conflict and (compact(period) not in compact(quote) or not any(supports(block) for block in blocks)):
                break
            sides.append(dict(evidence_id=ref['evidence_id'], quote=quote, value=value,
                              document_id=passage['document_id'], source_sha256=passage['source_sha256'],
                              page_start=passage['page_start'], page_end=passage['page_end']))
        if len(sides) != 2 or compact(sides[0]['quote']) == compact(sides[1]['quote']):
            issues.append('Conflict requires two distinct exact citations with subject, period and labelled values')
            continue
        values = [compact(s['value']) for s in sides]
        numeric = [re.fullmatch(r'([-+]?\d+(?:\.\d+)?)(日历天|天|日|万元|元|%|人|公里|米)', v) for v in values]
        # Only actual opposing states or same-unit, unequal numbers are checked.
        # Arbitrary different sentences are not a contradiction.
        incompatible = set(values) in ({'有', '无'}, {'是', '否'}, {'合格', '不合格'}, {'在建', '无在建'})
        if all(numeric):
            incompatible = numeric[0][2] == numeric[1][2] and Decimal(numeric[0][1]) != Decimal(numeric[1][1])
        if not incompatible:
            issues.append('Conflict values are equivalent or their incompatibility is not supported')
            continue
        verified.append(dict(subject=subject, role=role, aspect=aspect, period=period,
                             left=sides[0], right=sides[1], role_evidence_id=pair['role_evidence_id'],
                             role_quote=role_quote, role_source_sha256=anchor['source_sha256'],
                             role_document_id=anchor['document_id'],
                             role_page_start=anchor['page_start'], role_page_end=anchor['page_end']))
    if not verified:
        issues.append('Conflict flag lacks a verified two-sided evidence pair')
    return verified, sorted(set(issues))

"""Bounded checks for explicit constraints and cited subjects, not a general judge."""
import re
import unicodedata
from decimal import Decimal


NUMBER = re.compile(r'(?<![\d.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d.])')


def normalized(text):
    return unicodedata.normalize('NFKC', text)


def explicit_numeric_operator(text, value):
    """Recognize an operator adjacent to this threshold, abstaining on ambiguity.

    Never infer from the bid value or from another number elsewhere in a clause.
    The supported vocabulary is deliberately bounded; other language stays unknown.
    """
    if value is None:
        return None
    found = []
    for clause in re.split(r'[，；;。\n]', normalized(text)):
        for match in NUMBER.finditer(clause):
            if Decimal(match[0].replace(',', '')) != Decimal(str(value)):
                continue
            before = re.sub(r'\s+', '', clause[:match.start()])
            after = re.sub(r'\s+', '', clause[match.end():])
            operator = None
            patterns = (
                ('<=', r'(?:不得超过|不得高于|不超过|不高于|至多|最多|≤|<=|最高投标限价(?:为|是|:)?|招标控制价(?:为|是|:)?)$'),
                ('>=', r'(?:不得少于|不得低于|不少于|不低于|至少|≥|>=|最低投标限价(?:为|是|:)?)$'),
                ('>', r'(?:超过|高于|大于|>)$'),
                ('<', r'(?:低于|少于|小于|<)$'),
                ('==', r'(?:等于|恰好|固定为|==|(?<![<>!])=|(?:计划)?工期为|计划工期|投标有效期为|保证金为|暂列金(?:额)?(?:为|是|:)?)$'),
            )
            for op, pattern in patterns:
                matched = re.search(pattern, before)
                if matched:
                    # Negations not handled by the explicit phrase must not flip silently.
                    prefix = before[:matched.start()]
                    if prefix.endswith(('不', '不得', '非', '并非')):
                        break
                    operator = op
                    break
            suffix = re.match(r'^(?:日历天|天|日|万元|元|公里|米|%|人)?(及以上|及以下|以上|以下|以内)', after)
            if suffix:
                # Conflicting prefix and suffix indicate a range/ambiguous constraint.
                suffix_op = '>=' if '以上' in suffix[1] else '<='
                operator = suffix_op if operator is None else operator if operator == suffix_op else None
            found.append(operator)
    return found[0] if found and found[0] is not None and all(op == found[0] for op in found) else None


def required_qualification_role(requirement):
    for label, role in (
        ('道路工程师', 'road_engineer'),
        ('桥梁工程师', 'bridge_engineer'),
        ('合同工程师', 'contract_engineer'),
        ('专职安全员', 'safety_officer'),
        ('测量工程师', 'survey_engineer'),
        ('试验工程师', 'test_engineer'),
        ('施工员', 'construction_officer'),
    ):
        if label in requirement:
            return role
    if '项目经理' in requirement:
        return 'project_manager'
    if '项目总工' in requirement or '总工程师' in requirement:
        return 'chief_engineer'
    if '投标人' in requirement:
        return 'bidder'
    return None


def role_names(text, role):
    """Read explicitly labelled identities; never treat 企业名称 as 投标人名称."""
    labels = {
        'bidder': r'投标人(?:名称)?',
        'project_manager': r'(?:拟派|拟任|拟委任)?项目经理',
        'chief_engineer': r'(?:拟派|拟任|拟委任)?(?:项目总工|总工程师)',
        'road_engineer': r'(?:拟派|拟任|拟委任)?道路工程师',
        'bridge_engineer': r'(?:拟派|拟任|拟委任)?桥梁工程师',
        'contract_engineer': r'(?:拟派|拟任|拟委任)?合同工程师',
        'safety_officer': r'(?:拟派|拟任|拟委任)?专职安全员[0-9]*',
        'survey_engineer': r'(?:拟派|拟任|拟委任)?测量工程师',
        'test_engineer': r'(?:拟派|拟任|拟委任)?试验工程师',
        'construction_officer': r'(?:拟派|拟任|拟委任)?施工员[0-9]*',
    }
    if role not in labels:
        return set()
    # Roles have to be labels followed by a colon or a table-cell separator.
    pattern = labels[role] + r'\s*(?:[:：]|\|)\s*([^\n|，,；;()（）]+)'
    text = normalized(text)
    names = {re.sub(r'\s+', '', m[1]).rstrip('。') for m in re.finditer(pattern, text)}
    if role != 'bidder':
        names = set()
        # Native PDF tables often serialize cells on separate lines.
        if (re.search(r'项目管理机构|拟委任', text)
                and re.search(r'(?:^|\n)职务\s*\n\s*姓名(?:\s|$)', text)):
            names.update(m[1] for m in re.finditer(
                r'(?:^|\n)' + labels[role] + r'[ \t]*\n[ \t]*([\u4e00-\u9fff]{2,4})[ \t]*(?=\n|$)', text))
        # Read header-defined columns, including the reversed 姓名/职务 layout.
        name_col = role_col = None
        for line in text.splitlines():
            if '|' not in line:
                name_col = role_col = None
                names.update(re.sub(r'\s+', '', m[1]).rstrip('。') for m in re.finditer(pattern, line))
                continue
            cells = [re.sub(r'\s+', '', c) for c in line.strip().strip('|').split('|')]
            if '姓名' in cells and any(c in {'职务', '拟在本标段工程任职'} for c in cells):
                name_col = cells.index('姓名')
                role_col = next(i for i, c in enumerate(cells) if c in {'职务', '拟在本标段工程任职'})
            elif name_col is not None and len(cells) > max(name_col, role_col):
                if re.fullmatch(labels[role], cells[role_col]) and re.fullmatch(r'[\u4e00-\u9fff]{2,4}', cells[name_col]):
                    names.add(cells[name_col])
            else:
                names.update(re.sub(r'\s+', '', m[1]).rstrip('。') for m in re.finditer(pattern, line))
        # A single named resume explicitly states its proposed role, before history.
        resume = re.split(r'经\s*历|参加过的', text, maxsplit=1)[0]
        if re.search(r'拟在本标段\s*工程(?:任职|担任职务)[\s|]*' + labels[role], resume):
            names.update(re.sub(r'\s+', '', m[1]) for m in re.finditer(
                r'姓\s*名\s*(?:[:：|]\s*|\n\s*)([\u4e00-\u9fff]{2,4})(?=\s|[|，,；;]|$)', resume))
        names = {name for name in names if re.fullmatch(r'[\u4e00-\u9fff]{2,4}', name)}
    return names


def manager_performance_linked(documents, current_name):
    """Verify whether cited performance documents directly name `current_name` as project manager
    or include an official owner approval changing the project manager to `current_name`."""
    esc = re.escape(current_name)
    direct = any(
        re.search(r'项目经理\s*[:：|]\s*' + esc + r'(?=\s|[|，,；;。]|$)', normalized(text))
        for text in documents
    )
    if direct:
        return True
    change_approved = any(
        re.search(
            r'(?:项目经理.{0,20}(?:变更为|更换为|调整为)|同意.{0,20}(?:变更为|更换为)|变更后项目经理\s*[:：|]?)\s*'
            + esc + r'(?=\s|[|，,；;。同志]|$)',
            re.sub(r'\s+', '', normalized(text)),
        )
        for text in documents
    )
    return change_approved


def has_document_body(quote, requirement=''):
    """Reject headings/catalogues; require recognisable document content.

    This only establishes minimum textual support, not completeness or authenticity.
    Unrecognised document types require review rather than title-based acceptance.
    """
    text = re.sub(r'\s+', '', normalized(quote))
    if re.search(r'_{2,}|□|某某|XXX', text, re.I):
        return False
    if '投标函' in requirement:
        offer = re.search(r'(?:愿意以|愿以).*(?:投标总价|投标总报价|投标报价)', text)
        letter = '投标函' in text and '我方已仔细研究' in text
        priced = '含报价' in requirement and '不含报价' not in requirement
        return bool(letter and offer and (not priced or re.search(r'\d+(?:\.\d+)?元', text)))
    if '中标通知书' in requirement:
        return bool(re.search(r'现确定.{1,80}为中标人.{0,50}中标价.{0,50}\d', text))
    if '合同' in requirement and '身份证明' not in requirement:
        return all(word in text for word in ('发包人', '承包人', '协议')) and bool(re.search(r'合同价|签订|达成', text))
    return bool(re.search(
        r'本人.+系.+法定代表人|(?:现委托|兹委托).+(?:代理人|办理)|'
        r'.+系.+(?:投标人名称|投标人)?.*法定代表人|'
        r'(?:承包人|发包人|中标人|验收结论|验收意见|证书编号|许可证编号|统一社会信用代码)[:：|][^:：|]{3,}', text))


def owner_blocks(quote, name, role='bidder'):
    """Return labelled owner blocks or one header-defined personnel table row."""
    text = normalized(quote)
    blocks = []
    labels = r'(?:企业名称|单位名称|投标人(?:名称)?)' if role == 'bidder' else r'(?:姓\s*名|持证人)'
    markers = list(re.finditer(labels+r'\s*(?:[:：]|\||\n)\s*',text))
    for index, marker in enumerate(markers):
        end = markers[index+1].start() if index+1 < len(markers) else len(text)
        block = text[marker.end():end]
        owner = re.split(r'[\n|，,；;()（）]|性别[:：]|身份证',block,maxsplit=1)[0]
        if re.sub(r'\s+','',owner) == re.sub(r'\s+','',name):
            blocks.append(text[marker.start():end])
    if role != 'bidder':
        name_col = None
        for line in text.splitlines():
            if '|' not in line:
                name_col = None
                continue
            cells = [re.sub(r'\s+', '', c) for c in line.strip().strip('|').split('|')]
            if '姓名' in cells:
                name_col = cells.index('姓名')
            elif name_col is not None and len(cells) > name_col and cells[name_col] == name:
                blocks.append(line)
    return blocks


def qualification_owned_by(quote, name, fact, role='bidder'):
    return any(re.sub(r'\s+', '', normalized(fact)) in re.sub(r'\s+', '', block)
               for block in owner_blocks(quote, name, role))


def objective_target(text):
    """Only explicit project quality/safety targets; never truthfulness claims."""
    text = re.sub(r'\s+', '', normalized(text))
    # Negated, conditional or quoted requirements cannot certify a bid target.
    if re.search(r'未|并非|不符合|未达到|不承诺|无法|若|如果|招标要求|招标文件要求', text):
        return None
    quality = re.findall(r'交工验收(?:的)?质量评定(?:须|应)?(?:为|:)(不合格|合格|优良)(?=$|[，,；;。|])', text)
    if quality and len(set(quality)) == 1:
        return ('handover_quality', quality[0])
    safety = re.findall(r'安全目标(?:须|应)?(?:为|:)(无生产安全责任事故)(?=$|[，,；;。|])', text)
    return ('safety_target', safety[0]) if safety else None


def text_fact_supported(value, quote):
    """Literal support plus one bounded quality-target equivalence.

    This does not fuzzy-match companies, persons, numbers or certificate domains.
    """
    value, quote = (re.sub(r'\s+', '', normalized(s)) for s in (value, quote))
    target = objective_target(value)
    if target is not None:
        return objective_target(quote) == target
    if value in {'合格', '不合格', '优良'}:
        return quote == value or objective_target(quote) == ('handover_quality', value)
    return value in quote

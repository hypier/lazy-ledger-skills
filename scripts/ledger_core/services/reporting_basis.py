"""Opt-in, profile-owned income adjustments; never mutate cash transactions."""
from collections import defaultdict
from copy import deepcopy
from ..domain.money import decimal_amount, money_sum
from ..domain.metrics import summarize, pct_change
from ..domain.semantics import counts_as

FIELDS = {'type', 'category', 'account_id', 'to_account_id', 'excluded_from_totals'}

def validate(config):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('Unsupported reporting basis version')
    if not isinstance(config.get('income_label'), str) or not config['income_label'].strip():
        raise ValueError('Reporting basis requires an income label')
    rules = config.get('income_adjustments')
    if not isinstance(rules, list):
        raise ValueError('Income adjustments must be a list')
    ids = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('id'), str) or not rule['id'] or rule['id'] in ids:
            raise ValueError('Income adjustment IDs must be unique')
        ids.add(rule['id'])
        if type(rule.get('sign')) is not int or rule['sign'] not in {-1, 1}:
            raise ValueError('Income adjustment sign must be +1 or -1')
        for field in ('label', 'member'):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                raise ValueError('Income adjustment requires label and member')
        match = rule.get('match')
        if not isinstance(match, dict) or match.get('type') not in {'transfer', 'income', 'expense', 'refund'} or not match.get('category'):
            raise ValueError('Income adjustments require an explicit type and category')
        for key in ('match', 'not_match'):
            values = rule.get(key, {})
            if not isinstance(values, dict) or set(values) - FIELDS:
                raise ValueError('Unsupported income adjustment condition')
            for field, value in values.items():
                if (field == 'excluded_from_totals' and type(value) is not bool) or (field != 'excluded_from_totals' and not isinstance(value, str)):
                    raise ValueError('Invalid income adjustment condition value')
        if match['type'] != 'transfer' and match.get('excluded_from_totals') is not True:
            raise ValueError('Non-transfer adjustments must be explicitly excluded from ordinary totals')
        if rule['sign'] != (-1 if match['type'] == 'expense' else 1):
            raise ValueError('Income adjustment sign disagrees with cash direction')
    return config


def matching_rule(row, config):
    if not config:
        return None
    matches = [r for r in config['income_adjustments']
               if all(row.get(k) == v for k, v in r['match'].items())
               and not any(row.get(k) == v for k, v in r.get('not_match', {}).items())]
    if len(matches) > 1:
        raise ValueError('Overlapping income adjustments for ' + str(row.get('id')))
    if matches and any(counts_as(row, kind) for kind in ('income', 'expense', 'refund')):
        raise ValueError('Income adjustment would double count an ordinary event')
    return matches[0] if matches else None


def attributed_rows(rows, profile):
    """Member attribution for report adjustments is explicit in profile rules."""
    config = profile.get('reporting')
    if not config:
        return rows
    validate(config)
    result = []
    for row in rows:
        rule = matching_rule(row, config)
        if rule:
            if row.get('x_member') and row['x_member'] != rule['member']:
                raise ValueError('Income adjustment member conflicts with transaction')
            row = {**row, 'x_member': rule['member']}
        result.append(row)
    return result


def apply(data, rows, profile, previous_rows=None):
    config = profile.get('reporting')
    if not config:
        return data
    validate(config)
    if 'raw_totals' in data:
        raise ValueError('Reporting basis already applied')
    rows = attributed_rows(rows, profile)
    data['raw_totals'] = deepcopy(data['totals'])
    evidence = []
    groups = {key: defaultdict(list) for key in ('member', 'merchant', 'day', 'category')}
    for row in rows:
        rule = matching_rule(row, config)
        if not counts_as(row, 'income') and not rule:
            continue
        amount = decimal_amount(row['amount']) * (rule['sign'] if rule else 1)
        member = rule['member'] if rule else row.get('x_member') or '未标注'
        label = rule['label'] if rule else '原流水收入'
        evidence.append({'transaction_id': row['id'], 'contribution': float(amount),
                         'rule_id': rule['id'] if rule else None, 'label': label, 'member': member})
        for key, value in [('member', member), ('merchant', row.get('merchant') or '未填来源'),
                           ('day', row.get('occurred_at', '')[:10] or '未知'),
                           ('category', label if rule else row.get('category') or '其他收入')]:
            groups[key][value].append(amount)
    data['income_basis'] = {'version': 1, 'label': config['income_label'], 'coverage': 'recorded events only'}
    data['income_evidence'] = evidence
    data['totals']['income'] = money_sum(e['contribution'] for e in evidence)
    data['totals']['net'] = money_sum([data['totals']['income'], data['totals']['refund'], -decimal_amount(data['totals']['expense'])])
    for key, grouped in groups.items():
        data['by_income_' + key] = {name: money_sum(values) for name, values in sorted(grouped.items())}
    data['income_evidence_count'] = len(evidence)
    if previous_rows is not None and data.get('comparison'):
        previous = apply(summarize(previous_rows), previous_rows, profile)
        c = data['comparison']
        c['previous_raw_totals'] = previous['raw_totals']
        c['previous_totals'] = previous['totals']
        for key in ('income', 'net'):
            c[key + '_change_pct'] = pct_change(data['totals'][key], previous['totals'][key])
    return data

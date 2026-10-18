"""Conservative three-way merge. Financial records are indivisible conflict units."""
import copy

MISSING = object()
ROW_LISTS = {'transactions', 'accounts', 'budgets', 'categories', 'bills', 'review_queue', 'imports', 'changes'}


def merge(base, local, remote, *, choices=None):
    conflicts = []
    choices = choices or {}
    used = set()

    def conflict(b, l, r, path):
        entry = {'path': path}
        for name, value in (('base', b), ('local', l), ('remote', r)):
            entry[name] = None if value is MISSING else copy.deepcopy(value)
            entry[name+'_absent'] = value is MISSING
        if path in choices:
            if choices[path] not in ('local', 'remote'):
                raise ValueError('Conflict choices must be local or remote')
            used.add(path)
            return l if choices[path] == 'local' else r
        conflicts.append(entry)
        return l

    def walk(b, l, r, path, *, atomic=False):
        if l == r: return l
        if l == b: return r
        if r == b: return l
        if atomic: return conflict(b, l, r, path)
        if isinstance(l, dict) and isinstance(r, dict) and (b is MISSING or isinstance(b, dict)):
            b = {} if b is MISSING else b
            output = {}
            for key in sorted(b.keys() | l.keys() | r.keys()):
                v = walk(b.get(key,MISSING),l.get(key,MISSING),r.get(key,MISSING),path+'/'+key.replace('~','~0').replace('/','~1'))
                if v is not MISSING: output[key] = v
            return output
        name = path.rsplit('/',1)[-1]
        if isinstance(l,list) and isinstance(r,list) and (b is MISSING or isinstance(b,list)) and name in ROW_LISTS | {'habits','extensions'}:
            def identity(row):
                if name == 'extensions': return row['id']+'@'+row['version']
                if name == 'habits': return row['phrase']
                if name == 'categories': return row if isinstance(row,str) else row.get('id') or row['name']
                if name == 'review_queue': return row.get('id') or row['key']
                return row['id']
            def index(rows):
                try: result = {identity(x):x for x in rows}
                except (KeyError,TypeError): raise ValueError('Invalid record identities at '+path) from None
                if len(result) != len(rows): raise ValueError('Duplicate record identities at '+path)
                return result
            bi,li,ri = index([] if b is MISSING else b),index(l),index(r)
            output=[]
            for key in sorted(bi.keys() | li.keys() | ri.keys()):
                value=walk(bi.get(key,MISSING),li.get(key,MISSING),ri.get(key,MISSING),path+'/'+key.replace('~','~0').replace('/','~1'),atomic=True)
                if value is not MISSING: output.append(value)
            return output
        return conflict(b,l,r,path)

    value=walk(base,local,remote,'')
    if set(choices)-used: raise ValueError('Resolution contains stale or unknown conflict paths')
    return copy.deepcopy(value),conflicts


def align_source_identities(inputs):
    """Unify authoritative source identities before merging record contents.

    Audit receipts remain immutable; aliases preserve their historical IDs.
    Differing financial/classification values still reach the normal conflict UI.
    """
    values = copy.deepcopy(inputs)
    parent = {}
    sources = {}

    def root(identity):
        parent.setdefault(identity, identity)
        if parent[identity] != identity:
            parent[identity] = root(parent[identity])
        return parent[identity]

    def unite(left, right):
        left, right = root(left), root(right)
        parent[max(left, right)] = min(left, right)

    for value in values:
        ledger = value['ledger']
        for old, new in ledger.get('identity_aliases', {}).items():
            unite(old, new)
        for row in ledger['transactions']:
            root(row['id'])
            evidence = list(row.get('sources', []))
            for field, provider in [('wechat_transaction_id','wechat'),('bank_statement_ref','bank'),('credit_statement_ref','credit')]:
                if row.get(field): evidence.append({'provider':provider,'transaction_id':row[field]})
            for source in evidence:
                if source.get('provider') and source.get('transaction_id'):
                    key = ('reference', source['provider'], source['transaction_id'])
                elif source.get('file_hash') and source.get('row') is not None:
                    key = ('file', source['file_hash'], source['row'])
                else:
                    continue
                if key in sources: unite(row['id'], sources[key])
                sources[key] = row['id']
    groups = {}
    for identity in parent:
        groups.setdefault(root(identity), []).append(identity)
    base_ids = {r['id'] for r in values[0]['ledger']['transactions']}
    aliases = {}
    for ids in groups.values():
        canonical = min(set(ids) & base_ids or ids)
        aliases.update({identity: canonical for identity in ids if identity != canonical})

    for value in values:
        ledger = value['ledger']
        ledger['identity_aliases'] = aliases
        for row in ledger['transactions']:
            row['id'] = aliases.get(row['id'], row['id'])
            for field in ('related_transaction_id', 'writeoff_transaction_id'):
                if row.get(field): row[field] = aliases.get(row[field], row[field])
            for allocation in row.get('advance_settlements', []):
                allocation['transaction_id'] = aliases.get(allocation['transaction_id'], allocation['transaction_id'])
        for item in ledger.get('review_queue', []):
            if item.get('transaction_id') in aliases:
                old = item['transaction_id']; item['transaction_id'] = aliases[old]
                if item.get('key') == item.get('code', '') + ':' + old:
                    item['key'] = item['code'] + ':' + item['transaction_id']
                    # Derivation gives this finding its current stable ID on refresh.
                    item.pop('id', None)

    # Identical new economic events can differ in provenance and wall-clock fields.
    import json
    def fingerprint(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    base = {r['id'] for r in values[0]['ledger']['transactions']}
    left = {r['id']: r for r in values[1]['ledger']['transactions']}
    right = {r['id']: r for r in values[2]['ledger']['transactions']}
    ignored = {'created_at','updated_at','sources','field_evidence'}
    for identity in (left.keys() & right.keys()) - base:
        l, r = left[identity], right[identity]
        if {k:v for k,v in l.items() if k not in ignored} != {k:v for k,v in r.items() if k not in ignored}:
            continue
        combined = copy.deepcopy(l)
        evidence = {fingerprint(s):s for row in (l,r) for s in row.get('sources', [])}
        combined['sources'] = [evidence[key] for key in sorted(evidence)]
        for key in ('created_at', 'updated_at'):
            dates = [row[key] for row in (l,r) if row.get(key)]
            if dates: combined[key] = (min if key == 'created_at' else max)(dates)
        fields = set(l.get('field_evidence', {})) | set(r.get('field_evidence', {}))
        combined['field_evidence'] = {}
        for field in fields:
            evidence = {fingerprint(row['field_evidence'][field]):row['field_evidence'][field] for row in (l,r) if field in row.get('field_evidence', {})}
            items = [evidence[key] for key in sorted(evidence)]
            combined['field_evidence'][field] = items[0] if len(items) == 1 else {'kind':'combined_sources','evidence':items}
        l.clear(); l.update(copy.deepcopy(combined))
        r.clear(); r.update(copy.deepcopy(combined))
    return values

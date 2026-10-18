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

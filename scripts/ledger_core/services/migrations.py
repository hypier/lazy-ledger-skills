"""Declarative, version-bound migrations for optional transaction metadata only."""
import copy
import json
import math
import uuid
from pathlib import Path
from ..domain.metrics import summarize, account_balances
from ..domain.validation import validate
from ..storage import repository
from ..storage.documents import ledger_lock, atomic_write_json, write_text_secure
from . import profile, runtime, proposals


def _convert(value, kind):
    if kind == 'string':
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if kind == 'number':
        if type(value) is bool: raise ValueError('Boolean is not a numeric metadata value')
        number=float(value)
        if not math.isfinite(number): raise ValueError('Metadata number must be finite')
        return number
    if kind == 'boolean':
        if type(value) is bool: return value
        if value in ('true','false'): return value == 'true'
        raise ValueError('Only true/false strings can convert to boolean')
    if kind == 'object' and isinstance(value,dict) or kind == 'array' and isinstance(value,list): return value
    raise ValueError('Unsupported metadata conversion')


def candidate(ledger, request):
    result=copy.deepcopy(ledger)
    operations=request.get('operations')
    if not isinstance(operations,list) or not operations: raise ValueError('Migration needs operations')
    touched=set()
    for operation in operations:
        name=operation.get('field','')
        if name in touched or name not in result.runtime['custom_fields']:
            raise ValueError('Migration needs a registered, unique source field')
        touched.add(name)
        spec=result.runtime['custom_fields'][name]
        target=operation.get('rename',name)
        kind=operation.get('type',spec['type'])
        if not isinstance(target,str) or not target.startswith('x_') or target != name and target in result.runtime['custom_fields']:
            raise ValueError('Metadata rename would overwrite a field or core semantics')
        for row in result['transactions']:
            if name in row:
                if target != name and target in row: raise ValueError('Target field already exists on a transaction')
                row[target]=_convert(row.pop(name),kind)
        del result.runtime['custom_fields'][name]
        result.runtime['custom_fields'][target]={**spec,'type':kind}
    runtime.validate(result.runtime,result['schema_version']); validate(result)
    result.runtime['metadata_revision']=result.runtime.get('metadata_revision',0)+1
    return result


def financial_snapshot(ledger):
    return {'count':len(ledger['transactions']), 'totals':summarize(ledger['transactions'])['totals'],
            'accounts':account_balances(ledger)}


def prepare(path, request):
    if not isinstance(request.get('reason'),str) or not request['reason'].strip(): raise ValueError('Migration needs a reason')
    ledger=repository.load(path,create=False)
    after=candidate(ledger,request)
    before_metrics,after_metrics=financial_snapshot(ledger),financial_snapshot(after)
    if before_metrics != after_metrics: raise ValueError('Optional metadata migration changed financial results')
    plan={'version':1,'id':'migration_'+uuid.uuid4().hex,'context':proposals.context_token(ledger),
          'request':request,'before':before_metrics,'after':after_metrics,'capabilities_after':after.runtime}
    plan['digest']=repository.fingerprint(plan)
    atomic_write_json(profile.runtime_dir(path)/'work'/'migrations'/(plan['id']+'.json'),plan)
    return plan


def apply(path, plan):
    if plan.get('version') != 1 or plan.get('digest') != repository.fingerprint({k:v for k,v in plan.items() if k != 'digest'}):
        raise ValueError('Migration plan changed; prepare again')
    with ledger_lock(path):
        ledger=repository.load(path,create=False)
        old=ledger.get('operations',{}).get(plan['id'])
        if old:
            if old.get('request_hash') != plan['digest']: raise ValueError('Migration identity reused')
            return old['result']
        if proposals.context_token(ledger) != plan['context']: raise ValueError('Migration context is stale')
        after=candidate(ledger,plan['request'])
        if financial_snapshot(after) != plan['before']: raise ValueError('Financial validation differs from preview')
        backup=profile.runtime_dir(path)/'backups'/plan['id']
        for name,source in {'ledger.json':Path(path),'user-profile.md':profile.profile_path(path),'runtime.json':profile.runtime_dir(path)/'runtime.json'}.items():
            if source.exists(): write_text_secure(backup/name,source.read_text())
        receipt={'applied':True,'operation_id':plan['id'],'revision':ledger['revision']+1,'backup':str(backup)}
        after.setdefault('operations',{})[plan['id']]={'request_hash':plan['digest'],'result':receipt}
        after.operation={'id':plan['id'],'reason':plan['request']['reason'],'kind':'metadata_migration'}
        repository.save(path,after)
        return receipt

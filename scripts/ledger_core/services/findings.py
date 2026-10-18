"""Evidence-aware review lifecycle. Queries do not acknowledge notifications."""
import copy
from datetime import date
from ..storage.repository import fingerprint
from ..domain.model import now_iso

STATES={'open','waiting','resolved','dismissed','snoozed'}


def reconcile(ledger, candidates):
    previous={row['key']:row for row in ledger.get('review_queue',[]) if isinstance(row,dict) and row.get('key')}
    seen=set()
    for candidate in candidates:
        item=copy.deepcopy(candidate)
        evidence={key:item.get(key) for key in ('code','transaction_id','occurred_at','amount','merchant','note','detail')}
        token=fingerprint(evidence)
        item.update(id='finding_'+fingerprint(item['key'])[:24], evidence=token)
        seen.add(item['key'])
        old=previous.get(item['key'])
        if old:
            # Legacy decisions without fingerprints remain respected until explicitly refreshed.
            changed=old.get('evidence') is not None and old['evidence'] != token
            item={**old,**item,'status':'open' if changed else old.get('status','open')}
            if changed:
                item['previous_decision']={k:old.get(k) for k in ('status','resolution','resolved_at','evidence')}
                item.pop('snooze_until',None)
            if item['status']=='snoozed' and item.get('snooze_until','9999') <= date.today().isoformat():
                item['status']='open'
        previous[item['key']]=item
    for key,item in previous.items():
        if key not in seen and item.get('status') in {'open','waiting','snoozed'}:
            item.update(status='resolved',resolution='The finding is no longer present',resolved_at=now_iso())
    return list(previous.values())


def transition(item, state, note='', until=None):
    if state not in STATES: raise ValueError('Unsupported finding state')
    if state=='snoozed' and (not until or date.fromisoformat(until) <= date.today()):
        raise ValueError('Snoozing needs a future date')
    item.update(status=state,resolution=note,resolved_at=now_iso())
    if state=='snoozed': item['snooze_until']=until
    else: item.pop('snooze_until',None)
    return item


def notifications(path, candidates, *, acknowledge=None):
    """A delivery adapter acknowledges a receipt only after successful delivery."""
    import json
    from .profile import runtime_dir
    from ..storage.documents import ledger_lock, atomic_write_json
    with ledger_lock(path):
        target=runtime_dir(path)/'notifications-local.json'
        delivered=json.loads(target.read_text()) if target.exists() else {}
        if acknowledge:
            expected=fingerprint({k:v for k,v in acknowledge.items() if k != 'digest'})
            if acknowledge.get('digest') != expected: raise ValueError('Notification receipt changed')
            for item in acknowledge['items']:
                delivered[item['key']]=item['evidence']
            atomic_write_json(target,delivered)
        items=[item for item in candidates if item.get('status')=='open' and delivered.get(item['key']) != item.get('evidence')]
        receipt={'version':1,'items':items}
        receipt['digest']=fingerprint(receipt)
        return receipt

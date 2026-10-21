"""Opt-in Git snapshots in an isolated per-ledger repository.

Only a three-file consistent snapshot is versioned. Local permissions and source
attachments are deliberately outside the snapshot. Conflicts require resolution.
"""
import json
import os
import re
import subprocess
import copy
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import profile, runtime
from ..storage.documents import atomic_write_json, write_text_secure, ledger_lock
from ..domain.model import normalize_ledger
from ..domain.metrics import check_currencies
from ..domain.money import decimal_amount

FILES = ("ledger.json", "user-profile.md", "runtime.json")


def location(path):
    return profile.runtime_dir(path) / "history"


def settings(path):
    target = profile.runtime_dir(path) / "git-local.json"
    value = json.loads(target.read_text()) if target.exists() else {"mode": "off"}
    if not isinstance(value, dict) or value.get("mode") not in {"off", "local", "remote"}:
        raise ValueError("Invalid local Git settings")
    return value


def git(path, *args, check=True, input=None):
    env = {k: v for k, v in os.environ.items() if k not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}}
    result = subprocess.run(["git", "-c", "core.hooksPath=" + os.devnull, "-c", "commit.gpgsign=false",
                             "-c", "user.name=Lazy Ledger", "-c", "user.email=ledger@localhost",
                             "-C", str(location(path)), *args], text=True, capture_output=True,
                            timeout=30, input=input, env={**env, "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode:
        # Do not echo a credential-bearing subprocess command or connection error.
        raise ValueError("Git operation failed: " + args[0] + "; inspect repository status and authentication locally")
    return result


def configure(path, mode, remote=None, private_confirmed=False, sync_mode="manual"):
    if sync_mode not in {"manual", "after-write"} or sync_mode == "after-write" and mode != "remote":
        raise ValueError("Automatic synchronization requires explicit remote mode")
    if mode not in {"off", "local", "remote"}:
        raise ValueError("Unsupported Git mode")
    if mode == "remote":
        if not remote or not private_confirmed:
            raise ValueError("Remote mode needs an explicit remote and confirmed private visibility")
        parts = urlsplit(remote)
        if parts.scheme == "https":
            valid = bool(parts.hostname) and not parts.username and not parts.password and not parts.query and not parts.fragment
        else:
            valid = bool(re.fullmatch(r"git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", remote))
        if not valid:
            raise ValueError("Use a credential-free HTTPS or git@host:path SSH remote")
    root = location(path)
    if mode != "off":
        if not (root / ".git").exists():
            if root.exists() and any(root.iterdir()):
                raise ValueError("History directory is not empty; refusing to take it over")
            root.mkdir(parents=True, exist_ok=True)
            git(path, "init", "-b", "main")
        write_text_secure(root / ".git" / "info" / "exclude", "*\n!ledger.json\n!user-profile.md\n!runtime.json\n")
        top = Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        if top != root.resolve():
            raise ValueError("History must have its own repository")
        tracked = set(git(path, "ls-files").stdout.splitlines())
        if tracked - set(FILES):
            raise ValueError("History repository contains unrelated tracked files")
        if mode == "remote":
            existing = git(path, "remote", "get-url", "origin", check=False)
            git(path, "remote", "set-url" if existing.returncode == 0 else "add", "origin", remote)
    atomic_write_json(profile.runtime_dir(path) / "git-local.json", {"mode": mode, "sync": sync_mode})
    return {"mode": mode, "sync": sync_mode, "uploaded": False}


def record(path):
    if settings(path)["mode"] == "off":
        return {"state": "disabled"}
    root = location(path)
    if not (root / ".git").is_dir():
        raise ValueError("Configured history repository is missing; reconfigure explicitly")
    if Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != root.resolve():
        raise ValueError("History must have its own repository")
    prior = git(path, "show", "HEAD:ledger.json", check=False)
    if prior.returncode == 0 and json.loads(prior.stdout).get("ledger_id") != json.loads(Path(path).read_text()).get("ledger_id"):
        raise ValueError("History repository belongs to another ledger")
    targets = {"ledger.json": Path(path), "user-profile.md": profile.profile_path(path),
               "runtime.json": profile.runtime_dir(path) / "runtime.json"}
    for name, source in targets.items():
        if not source.is_file():
            raise ValueError("Snapshot incomplete: " + name)
        write_text_secure(root / name, source.read_text(encoding="utf-8"))
    tracked = set(git(path, "ls-files").stdout.splitlines())
    if tracked - set(FILES):
        raise ValueError("Unrelated files in history; refusing commit")
    git(path, "add", "--", *FILES)
    if git(path, "diff", "--cached", "--quiet", check=False).returncode:
        ledger = json.loads(Path(path).read_text())
        git(path, "commit", "-m", "Ledger revision " + str(ledger["revision"]))
    return {"state": "recorded", "commit": git(path, "rev-parse", "HEAD").stdout.strip(), "remote_synced": False}


def list_history(path):
    if settings(path)["mode"] == "off":
        return {"mode": "off", "versions": []}
    result = git(path, "log", "-30", "--format=%H%x09%cI%x09%s", check=False)
    return {"mode": settings(path)["mode"], "versions": [dict(zip(("commit", "date", "summary"), line.split("\t", 2))) for line in result.stdout.splitlines()]}


def snapshot(path, revision):
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision):
        raise ValueError("Use a hexadecimal commit ID from history list")
    values = {name: git(path, "show", revision + ":" + name).stdout for name in FILES}
    ledger = normalize_ledger(json.loads(values["ledger.json"]))
    check_currencies(ledger["transactions"], ledger["currency"])
    ids = set()
    for row in ledger["transactions"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in ids:
            raise ValueError("Invalid transaction identity in snapshot")
        ids.add(row["id"])
        if row.get("type") not in {"income", "expense", "refund", "transfer"} or decimal_amount(row.get("amount")) <= 0:
            raise ValueError("Invalid financial row in snapshot")
        datetime.fromisoformat(row["occurred_at"])
    matches = list(profile.BLOCK.finditer(values["user-profile.md"]))
    if len(matches) != 1:
        raise ValueError("Invalid snapshot profile")
    rules = profile.validate(json.loads(matches[0].group(1)))
    capabilities = runtime.validate(json.loads(values["runtime.json"]), ledger["schema_version"])
    from ..domain.validation import validate
    validate(ledger)
    default_account = rules["defaults"].get("account_id")
    if default_account and not any(a["id"] == default_account and not a.get("archived") for a in ledger["accounts"]):
        raise ValueError("Snapshot default account is missing or archived")
    return ledger, rules, capabilities, values["user-profile.md"], values


def restore(path, revision, apply=False):
    from ..storage import repository
    with ledger_lock(path):
        current = repository.load(path, create=False)
        old, rules, capabilities, text, _values = snapshot(path, revision)
        if old.get("ledger_id") != current.get("ledger_id"):
            raise ValueError("Snapshot belongs to another ledger")
        result = {"applied": False, "source": revision, "current_revision": current["revision"],
                  "transactions_before": len(current["transactions"]), "transactions_after": len(old["transactions"])}
        from .migrations import financial_snapshot
        result.update(before=financial_snapshot(current), after=financial_snapshot(old))
        if not apply:
            return result
        record(path)  # preserve the current state before restoration
        revision_now = current["revision"]
        current.clear()
        current.update(old)
        current["revision"] = revision_now
        current.profile_override = rules
        current.profile_text = text
        current.runtime = capabilities
        repository.save(path, current)
        result.update(applied=True, revision=current["revision"], history=getattr(current, "history_status", None))
        return result


def sync_status(path):
    target = profile.runtime_dir(path) / 'sync-local.json'
    return json.loads(target.read_text()) if target.exists() else {'state':'never_synced'}


def _status(path, value):
    from ..storage.repository import fingerprint
    previous=sync_status(path)
    key=fingerprint(value)
    result={**value, 'evidence':key, 'changed':previous.get('evidence') != key}
    atomic_write_json(profile.runtime_dir(path)/'sync-local.json',result)
    return result


def _merge_bundle(path, base_sha, local_sha, remote_sha, resolution):
    from ..domain.merge import merge, align_source_identities
    from ..domain.validation import validate
    from ..domain.model import now_iso
    triples = [snapshot(path, sha) for sha in (base_sha,local_sha,remote_sha)]
    if len({(x[0].get('ledger_id'),x[0]['schema_version'],x[0]['currency']) for x in triples}) != 1:
        raise ValueError('Ledger identity, schema or currency migration conflicts; migrate explicitly before merging')
    inputs=[]
    for ledger,rules,capabilities,text,_ in triples:
        ledger=copy.deepcopy(ledger)
        for key in ('revision','updated_at','habits'): ledger.pop(key,None)
        for key in ('default_account_id','merchant_categories','merchant_aliases','communication','usage_profile'):
            ledger['preferences'].pop(key,None)
        # The prose and confirmed rules have separate conflict units.
        prose=profile.BLOCK.sub('__PROFILE_RULES__',text)
        inputs.append({'ledger':ledger,'profile':rules,'prose':prose,'runtime':capabilities})
    inputs = align_source_identities(inputs)
    merged,conflicts=merge(*inputs,choices=(resolution or {}).get('choices'))
    if conflicts: return None, conflicts
    data,rules,capabilities=merged['ledger'],merged['profile'],merged['runtime']
    profile.validate(rules); runtime.validate(capabilities,data['schema_version'])
    account_id=rules['defaults'].get('account_id')
    if account_id and not any(a['id']==account_id and not a.get('archived') for a in data['accounts']):
        raise ValueError('Merged default account is missing or archived')
    data['revision']=max(x[0]['revision'] for x in triples)+1
    data['updated_at']=now_iso()
    data['habits']=[{**rule,'source':'manual'} for rule in rules['habits']]
    data['preferences'].update(default_account_id=account_id,merchant_categories=rules['merchant_categories'],merchant_aliases=rules['merchant_aliases'],communication=rules['communication'])
    data.setdefault('changes',[]).append({'id':'sync_'+uuid.uuid4().hex,'revision':data['revision'],
        'at':data['updated_at'],'reason':'Validated three-way synchronization','parents':[local_sha,remote_sha],'changes':[]})
    validate(data)
    text=merged['prose'].replace('__PROFILE_RULES__',profile.BLOCK.search(profile.render(rules)).group(0),1)
    return {'ledger.json':json.dumps(data,ensure_ascii=False,indent=2)+'\n','user-profile.md':text,
            'runtime.json':json.dumps(capabilities,ensure_ascii=False,indent=2)+'\n'}, []


def sync(path, resolution=None):
    """Network outside the write lock; stable IDs and explicit row conflict choices."""
    from ..storage import repository, transaction
    from .proposals import context_token
    if settings(path)['mode'] != 'remote':
        raise ValueError('Remote sync is not configured on this device')
    # Serialize sync jobs only. Ordinary writes use a different lock.
    with ledger_lock(profile.runtime_dir(path)/'sync'):
        try:
            with ledger_lock(path):
                current=repository.load(path,create=False)
                record(path)
                token=context_token(current)
                synced_token=token
                local_sha=git(path,'rev-parse','HEAD').stdout.strip()
            git(path,'fetch','origin')
            ref=git(path,'rev-parse','--verify','refs/remotes/origin/main',check=False)
            remote_sha=ref.stdout.strip() if ref.returncode == 0 else None
            values=None; merged=False
            if resolution and (resolution.get('local_head'),resolution.get('remote_head')) != (local_sha,remote_sha):
                raise ValueError('Conflict resolution is stale; sync again to refresh it')
            if remote_sha and remote_sha != local_sha:
                if git(path,'merge-base','--is-ancestor',remote_sha,local_sha,check=False).returncode != 0:
                    if git(path,'merge-base','--is-ancestor',local_sha,remote_sha,check=False).returncode == 0:
                        old,_rules,_capabilities,_text,values=snapshot(path,remote_sha)
                        from ..domain.validation import validate
                        validate(old)
                        if old.get('ledger_id') != current.get('ledger_id'): raise ValueError('Remote is a different ledger')
                    else:
                        base=git(path,'merge-base',local_sha,remote_sha,check=False)
                        if base.returncode: raise ValueError('Remote has no common ledger history')
                        values,conflicts=_merge_bundle(path,base.stdout.strip(),local_sha,remote_sha,resolution)
                        if conflicts:
                            plan={'local_head':local_sha,'remote_head':remote_sha,'conflicts':conflicts,'choices':{}}
                            target=profile.runtime_dir(path)/'work'/'sync-conflicts.json'
                            atomic_write_json(target,plan)
                            return _status(path,{'state':'conflict','local_head':local_sha,'remote_head':remote_sha,'plan':str(target),'count':len(conflicts)})
                        merged=True
            elif not remote_sha:
                branches=git(path,'for-each-ref','--format=%(refname)','refs/remotes/origin').stdout.strip()
                if branches: raise ValueError('Remote has another branch but no ledger main branch')
            with ledger_lock(path):
                latest=repository.load(path,create=False)
                if token != context_token(latest) or local_sha != git(path,'rev-parse','HEAD').stdout.strip():
                    raise ValueError('Local context changed during synchronization; retry')
                if values:
                    if merged:
                        for name,value in values.items(): write_text_secure(location(path)/name,value)
                        git(path,'add','--',*FILES)
                        tree=git(path,'write-tree').stdout.strip()
                        new_sha=git(path,'commit-tree',tree,'-p',local_sha,'-p',remote_sha,input='Merge validated ledger records\n').stdout.strip()
                    else:
                        new_sha=remote_sha
                    # Journal makes the three authoritative files recover together.
                    transaction.commit(path,{'ledger':values['ledger.json'],'profile':values['user-profile.md'],'runtime':values['runtime.json']})
                    git(path,'update-ref','refs/heads/main',new_sha,local_sha)
                    # Align the isolated checkout without touching user attachments.
                    git(path,'read-tree','--reset','-u',new_sha)
                    local_sha=new_sha
                    synced_token=context_token(repository.load(path,create=False))
            # Pin the outgoing revision even if another local write occurs now.
            git(path,'push','origin',local_sha+':refs/heads/main')
            observed=git(path,'ls-remote','origin','refs/heads/main').stdout.split()
            if not observed or observed[0] != local_sha:
                raise ValueError('Remote version changed or push outcome is unknown; retry synchronization')
            with ledger_lock(path):
                latest=repository.load(path,create=False)
                pending=git(path,'rev-parse','HEAD').stdout.strip() != local_sha
                pending = pending or context_token(latest) != synced_token
                return _status(path,{'state':'pending' if pending else 'synced','commit':local_sha,'merged':merged})
        except (ValueError,OSError,subprocess.TimeoutExpired) as exc:
            _status(path,{'state':'pending','message':str(exc)})
            raise


def queue_auto_sync(path):
    """Persist retry intent; never wait for a network under the business lock."""
    config=settings(path)
    if config['mode']=='remote' and config.get('sync')=='after-write':
        atomic_write_json(profile.runtime_dir(path)/'sync-request-local.json', {'pending':True})


def flush_auto_sync(path):
    target=profile.runtime_dir(path)/'sync-request-local.json'
    config=settings(path)
    if config['mode'] != 'remote' or config.get('sync') != 'after-write' or not target.exists():
        return None
    from ..storage.documents import ledger_lock_held
    if ledger_lock_held(path):
        raise ValueError('Automatic synchronization must run after releasing the business lock')
    try:
        result=sync(path)
    except (ValueError,OSError,subprocess.TimeoutExpired):
        return sync_status(path)
    if result.get('state')=='synced':
        with ledger_lock(path):
            # A write between push verification and this cleanup must retain retry intent.
            actual=git(path,'rev-parse','HEAD').stdout.strip()
            if actual == result.get('commit'):
                target.unlink(missing_ok=True)
    return result

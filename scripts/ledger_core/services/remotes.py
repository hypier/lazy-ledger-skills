"""Private GitHub provisioning and validated new-device installation.

External creation is explicit; authentication stays with the host's gh/git tools.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from . import history, profile
from ..storage import repository, transaction
from ..storage.documents import atomic_write_json, ledger_lock


def source_url(value, *, local=False):
    text = str(value)
    if local and Path(text).is_dir():
        return str(Path(text).resolve())
    parsed = urlsplit(text)
    if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment:
        return text
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', text):
        return text
    raise ValueError('Use a credential-free HTTPS/SSH source or an explicit existing local repository')


def _clone(source, folder):
    env = {k:v for k,v in os.environ.items() if k not in {'GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR'}}
    result = subprocess.run(['git','-c','core.hooksPath='+os.devnull,'clone','--no-checkout','--single-branch','--branch','main','--',source,str(folder)],
                            capture_output=True,text=True,timeout=60,env={**env,'GIT_TERMINAL_PROMPT':'0'})
    if result.returncode:
        raise ValueError('Cannot clone ledger main branch; check source and host authentication')


def _inspect(staged, expected_id=None, expected_commit=None):
    sha = history.git(staged,'rev-parse','HEAD').stdout.strip()
    if expected_commit and sha != expected_commit:
        raise ValueError('Remote changed since preview; inspect it again')
    files = set(history.git(staged,'ls-tree','-r','--name-only',sha).stdout.splitlines())
    if files != set(history.FILES):
        raise ValueError('Remote snapshot must contain exactly the portable ledger files')
    data, rules, capabilities, text, values = history.snapshot(staged,sha)
    if not data.get('ledger_id') or expected_id and data['ledger_id'] != expected_id:
        raise ValueError('Unexpected ledger identity')
    return {'ledger_id':data['ledger_id'],'commit':sha,'revision':data['revision'],
            'transactions':len(data['transactions']),'extensions_require_local_validation':len(capabilities['extensions']),
            'not_restored':['source attachments','archived report artifacts','device permissions','notification receipts']}, values


def inspect(source, *, expected_id=None):
    source = source_url(source,local=True)
    with tempfile.TemporaryDirectory(prefix='ledger-inspect-') as folder:
        staged = Path(folder)/'book.json'
        history.location(staged).parent.mkdir(parents=True)
        _clone(source,history.location(staged))
        info, _ = _inspect(staged,expected_id)
        return info


def restore(path, source, *, expected_id=None, expected_commit=None, mode='local', private_confirmed=False):
    path = Path(path)
    source = source_url(source,local=True)
    if mode not in {'local','off','remote'}:
        raise ValueError('Invalid new-device Git mode')
    if mode == 'remote' and not private_confirmed:
        raise ValueError('Remote mode needs a verified private target on this device')
    with ledger_lock(path):
        if path.exists() or profile.runtime_dir(path).exists():
            raise ValueError('Restore requires a new ledger path and runtime directory; existing data is never overwritten')
    path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ledger-restore-',dir=path.parent) as folder:
        staged = Path(folder)/path.name
        history.location(staged).parent.mkdir(parents=True)
        _clone(source,history.location(staged))
        info, values = _inspect(staged,expected_id,expected_commit)
        history.git(staged,'read-tree','--reset','-u',info['commit'])
        atomic_write_json(profile.runtime_dir(staged)/'git-local.json',{'mode':mode,'sync':'manual'})
        atomic_write_json(profile.runtime_dir(staged)/'restore-local.json',info)
        atomic_write_json(profile.runtime_dir(staged)/'pending.json', {'ledger':values['ledger.json'],'profile':values['user-profile.md'],'runtime':values['runtime.json']})
        with ledger_lock(path):
            if path.exists() or profile.runtime_dir(path).exists():
                raise ValueError('Destination changed during restore')
            os.replace(profile.runtime_dir(staged),profile.runtime_dir(path))
            # Journal recovery finishes all authoritative files if installation is interrupted.
            transaction.recover(path)
            repository.load(path,create=False)
        return {**info,'restored':True,'mode':mode,'uploaded':False,'ledger':str(path.resolve())}


def _gh(*args, check=True):
    result = subprocess.run(['gh',*args],text=True,capture_output=True,timeout=45,
                            env={**os.environ,'GH_PROMPT_DISABLED':'1'})
    if check and result.returncode:
        raise ValueError('GitHub operation failed; check gh authentication and repository permissions')
    return result


def provision(path, name, *, create=False, apply=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',name):
        raise ValueError('Specify an explicit GitHub OWNER/REPOSITORY')
    ledger = repository.load(path,create=False)
    token = (ledger.read_token,ledger.profile_token,ledger.runtime_token)
    query = ('repo','view',name,'--json','nameWithOwner,isPrivate,isArchived,isEmpty,sshUrl,defaultBranchRef')
    result = _gh(*query,check=False)
    if result.returncode:
        if not create or not apply:
            return {'ready':False,'repository':name,'action':'create-private' if create else 'inspect',
                    'message':'Repository cannot be inspected; creation requires --create --apply and host authentication'}
        # Explicit creation only; a timeout never triggers a blind second create.
        try:
            _gh('repo','create',name,'--private',check=False)
        except subprocess.TimeoutExpired:
            pass
        result = _gh(*query)
    info = json.loads(result.stdout)
    if not info.get('isPrivate') or info.get('isArchived') or info.get('nameWithOwner','').lower() != name.lower():
        raise ValueError('Target must be the requested writable private repository')
    source = source_url(info['sshUrl'])
    if not info.get('isEmpty'):
        inspect(source,expected_id=ledger['ledger_id'])
    if not apply:
        return {'ready':True,'repository':name,'private':True,'action':'connect','uploaded':False}
    with ledger_lock(path):
        current = repository.load(path,create=False)
        if token != (current.read_token,current.profile_token,current.runtime_token):
            raise ValueError('Local context changed; retry connecting the verified repository')
        result = history.configure(path,'remote',source,True)
        current['git_preference']='remote'
        repository.save(path,current)
    return {**result,'repository':name,'private_verified':True,'uploaded':False}

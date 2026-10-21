import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from ledger_core.services import history, profile, proposals
from ledger_core.storage import repository
from ledger_core.storage.documents import atomic_write_json, ledger_lock_held


class SyncWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.a=self.root/'a.json'; self.b=self.root/'b.json'
        repository.save(self.a,repository.load(self.a))
        history.configure(self.a,'local'); history.record(self.a)
        remote=self.root/'remote.git'
        subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
        history.git(self.a,'remote','add','origin',str(remote))
        atomic_write_json(profile.runtime_dir(self.a)/'git-local.json',{'mode':'remote'})
        history.sync(self.a)
        profile.runtime_dir(self.b).mkdir()
        subprocess.run(['git','clone','--branch','main',str(remote),str(history.location(self.b))],check=True,capture_output=True)
        for name,target in {'ledger.json':self.b,'user-profile.md':profile.profile_path(self.b),'runtime.json':profile.runtime_dir(self.b)/'runtime.json'}.items():
            shutil.copy2(history.location(self.b)/name,target)
        atomic_write_json(profile.runtime_dir(self.b)/'git-local.json',{'mode':'remote'})

    def change(self,path,operations):
        plan=proposals.prepare(path,{'reason':'sync test','operations':operations})
        proposals.commit(path,plan)
        return plan

    def seed(self):
        plan=self.change(self.a,[{'action':'add','after':{'amount':10,'occurred_at':'2026-10-01'}}])
        history.sync(self.a); history.sync(self.b)
        return plan['operations'][0]['id']

    def test_conflicting_edits_preserve_both_then_explicitly_resolve(self):
        txid=self.seed()
        self.change(self.a,[{'action':'update','id':txid,'set':{'amount':20}}])
        self.change(self.b,[{'action':'update','id':txid,'set':{'amount':30}}])
        history.sync(self.a)
        before=self.b.read_bytes()
        result=history.sync(self.b)
        self.assertEqual(result['state'],'conflict'); self.assertEqual(self.b.read_bytes(),before)
        plan=json.loads(Path(result['plan']).read_text())
        self.assertEqual(len(plan['conflicts']),1)
        plan['choices']={c['path']:'local' for c in plan['conflicts']}
        self.assertTrue(history.sync(self.b,plan)['merged'])
        history.sync(self.a)
        self.assertEqual(repository.load(self.a)['transactions'][0]['amount'],30)
        self.assertEqual(len(history.git(self.b,'show','-s','--format=%P','HEAD').stdout.split()),2)
        with self.assertRaisesRegex(ValueError,'stale'): history.sync(self.b,plan)

    def test_fetch_does_not_hold_write_lock_and_concurrent_write_prevents_install(self):
        original=history.git
        injected=False
        def git(path,*args,**kwargs):
            nonlocal injected
            if args[0]=='fetch':
                self.assertFalse(ledger_lock_held(path))
                if not injected:
                    injected=True
                    self.change(path,[{'action':'add','after':{'amount':9,'occurred_at':'2026-10-01'}}])
            if args[0] in {'push','ls-remote'}: self.assertFalse(ledger_lock_held(path))
            return original(path,*args,**kwargs)
        with patch.object(history,'git',side_effect=git):
            with self.assertRaisesRegex(ValueError,'Local context changed'): history.sync(self.b)
        self.assertEqual(repository.load(self.b)['transactions'][0]['amount'],9)
        self.assertEqual(history.sync(self.b)['state'],'synced')

    def test_same_source_on_two_devices_is_not_double_counted(self):
        row={'amount':8,'occurred_at':'2026-10-01','sources':[{'provider':'bank','transaction_id':'one'}]}
        left=self.change(self.a,[{'action':'add','after':row}])['operations'][0]['id']
        right=self.change(self.b,[{'action':'add','after':row}])['operations'][0]['id']
        history.sync(self.a)
        self.assertTrue(history.sync(self.b)['merged'])
        history.sync(self.a)
        for path in (self.a,self.b):
            ledger=repository.load(path)
            self.assertEqual(len(ledger['transactions']),1)
            canonical=ledger['transactions'][0]['id']
            self.assertEqual(ledger['identity_aliases'][max(left,right)],canonical)
        self.change(self.b,[{'action':'update','id':max(left,right),'set':{'note':'alias edit'}}])
        history.sync(self.b); history.sync(self.a)
        self.assertEqual(repository.load(self.a)['transactions'][0]['note'],'alias edit')

    def test_same_source_with_differing_amounts_requires_conflict_choice(self):
        row={'amount':8,'occurred_at':'2026-10-01','sources':[{'provider':'bank','transaction_id':'one'}]}
        self.change(self.a,[{'action':'add','after':row}])
        self.change(self.b,[{'action':'add','after':{**row,'amount':9}}])
        history.sync(self.a)
        result=history.sync(self.b)
        self.assertEqual(result['state'],'conflict')
        plan=json.loads(Path(result['plan']).read_text())
        plan['choices']={c['path']:'remote' for c in plan['conflicts']}
        history.sync(self.b,plan)
        self.assertEqual(len(repository.load(self.b)['transactions']),1)
        self.assertEqual(repository.load(self.b)['transactions'][0]['amount'],8)

    def test_offline_failure_leaves_saved_rows_and_reports_pending(self):
        self.seed()
        original=history.git
        def fail(path,*args,**kwargs):
            if args[0]=='fetch': raise ValueError('offline')
            return original(path,*args,**kwargs)
        with patch.object(history,'git',side_effect=fail):
            with self.assertRaises(ValueError): history.sync(self.b)
        self.assertEqual(len(repository.load(self.b)['transactions']),1)
        self.assertEqual(history.sync_status(self.b)['state'],'pending')
        self.assertEqual(history.sync(self.b)['state'],'synced')

    def test_auto_sync_requires_local_opt_in_and_retries_after_offline_save(self):
        atomic_write_json(profile.runtime_dir(self.b)/'git-local.json', {'mode':'remote','sync':'after-write'})
        self.change(self.b,[{'action':'add','after':{'amount':13,'occurred_at':'2026-10-02'}}])
        target=profile.runtime_dir(self.b)/'sync-request-local.json'
        self.assertTrue(target.exists())
        original=history.git
        def offline(path,*args,**kwargs):
            if args[0]=='fetch':
                self.assertFalse(ledger_lock_held(path))
                raise ValueError('offline fixture')
            return original(path,*args,**kwargs)
        with patch.object(history,'git',side_effect=offline):
            self.assertEqual(history.flush_auto_sync(self.b)['state'],'pending')
        self.assertTrue(target.exists())
        self.assertEqual(len(repository.load(self.b)['transactions']),1)
        self.assertEqual(history.flush_auto_sync(self.b)['state'],'synced')
        self.assertFalse(target.exists())
        history.sync(self.a)
        self.assertEqual(repository.load(self.a)['transactions'][0]['amount'],13)
        history.configure(self.b,'local')
        with patch.object(history,'sync',side_effect=AssertionError('must not upload')):
            self.assertIsNone(history.flush_auto_sync(self.b))

    def test_remote_snapshot_with_missing_profile_account_is_rejected(self):
        self.seed()
        target=history.location(self.a)/'user-profile.md'
        rules=profile.empty_profile()
        rules['defaults']['account_id']='account-not-present'
        target.write_text(profile.render(rules))
        history.git(self.a,'add','--','user-profile.md')
        history.git(self.a,'commit','-m','Invalid profile fixture')
        history.git(self.a,'push','origin','HEAD:refs/heads/main')
        before=self.b.read_bytes()
        with self.assertRaisesRegex(ValueError,'Snapshot default account'):
            history.sync(self.b)
        self.assertEqual(self.b.read_bytes(),before)

if __name__=='__main__': unittest.main()

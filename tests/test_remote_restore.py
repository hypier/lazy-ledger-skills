import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ledger_core.services import history, profile, proposals, remotes
from ledger_core.storage import repository, transaction

class RemoteRestoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.book=self.root/'a.json'
        repository.save(self.book,repository.load(self.book))
        history.configure(self.book,'local')
        plan=proposals.prepare(self.book,{'reason':'fixture','operations':[{'action':'add','after':{'amount':12,'occurred_at':'2026-10-01'}}]})
        proposals.commit(self.book,plan)
        self.source=str(history.location(self.book))

    def test_preview_and_new_device_recovery_do_not_upload_or_inherit_permissions(self):
        preview=remotes.inspect(self.source)
        target=self.root/'new'/'b.json'
        result=remotes.restore(target,self.source,expected_id=preview['ledger_id'],expected_commit=preview['commit'])
        self.assertTrue(result['restored']); self.assertFalse(result['uploaded'])
        loaded=repository.load(target,create=False)
        self.assertEqual(loaded['ledger_id'],preview['ledger_id'])
        self.assertEqual(loaded['transactions'][0]['amount'],12)
        self.assertEqual(history.settings(target)['mode'],'local')
        self.assertFalse((profile.runtime_dir(target)/'extension-permissions-local.json').exists())
        self.assertIn('source attachments',result['not_restored'])
        with self.assertRaisesRegex(ValueError,'new ledger path'):
            remotes.restore(target,self.source)

    def test_restore_after_install_interrupt_finishes_from_journal(self):
        target=self.root/'b.json'
        with patch.object(transaction,'recover',side_effect=OSError('interrupted')):
            with self.assertRaisesRegex(OSError,'interrupted'): remotes.restore(target,self.source)
        self.assertFalse(target.exists())
        self.assertTrue((profile.runtime_dir(target)/'pending.json').exists())
        loaded=repository.load(target,create=False)
        self.assertEqual(loaded['transactions'][0]['amount'],12)
        self.assertFalse((profile.runtime_dir(target)/'pending.json').exists())

    def test_remote_identity_and_preview_change_are_rejected(self):
        for kwargs in ({'expected_id':'other'},{'expected_commit':'0'*40}):
            with self.assertRaises(ValueError): remotes.restore(self.root/'b.json',self.source,**kwargs)
        self.assertFalse((self.root/'b.json').exists())

    def test_remote_extra_files_are_rejected_before_checkout(self):
        (history.location(self.book)/'unrelated').write_text('no')
        history.git(self.book,'add','-f','unrelated');history.git(self.book,'commit','-m','bad fixture')
        with self.assertRaisesRegex(ValueError,'exactly'): remotes.inspect(self.source)

    def test_private_provider_creation_is_explicit_and_visibility_verified(self):
        info={'nameWithOwner':'owner/book','isPrivate':True,'isArchived':False,'isEmpty':True,'sshUrl':'git@github.com:owner/book.git'}
        failure=subprocess.CompletedProcess([],1,'','')
        success=subprocess.CompletedProcess([],0,json.dumps(info),'')
        with patch.object(remotes,'_gh',return_value=failure) as gh:
            self.assertFalse(remotes.provision(self.book,'owner/book',create=True)['ready'])
            self.assertEqual(gh.call_count,1)
        with patch.object(remotes,'_gh',side_effect=[failure,subprocess.CompletedProcess([],0,'',''),success]) as gh:
            result=remotes.provision(self.book,'owner/book',create=True,apply=True)
            self.assertEqual(gh.call_args_list[1].args,('repo','create','owner/book','--private'))
            self.assertTrue(result['private_verified']);self.assertFalse(result['uploaded'])
        info['isPrivate']=False
        with patch.object(remotes,'_gh',return_value=subprocess.CompletedProcess([],0,json.dumps(info),'')):
            with self.assertRaisesRegex(ValueError,'private'): remotes.provision(self.book,'owner/book',apply=True)

if __name__=='__main__': unittest.main()

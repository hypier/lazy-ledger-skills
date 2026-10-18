import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ledger_core.extensions import registry, runner
from ledger_core.services import profile, proposals
from ledger_core.storage import repository


class ExtensionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.path = self.root / 'ledger.json'
        repository.save(self.path, repository.load(self.path))
        self.candidate = {'amount': 12, 'occurred_at': '2026-10-01T12:00:00+08:00'}
        self.output = {'version': 1, 'candidates': [self.candidate]}
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(json.dumps({'id': 'bank-csv', 'version': '1.0.0', 'entrypoint': 'main.py',
            'core_schema': [2], 'permissions': {'data':'input-only','network':False,'writes':'scratch-only'},
            'fixtures': [{'input': {}, 'expected': self.output}]}))
        (self.root/'main.py').write_text('import json\nprint('+repr(json.dumps(self.output))+')\n')

    def test_candidate_validation_enable_proposal_tamper_and_device_grant(self):
        registry.register(self.path, self.manifest)
        with self.assertRaises(ValueError): registry.run(self.path, 'bank-csv', '1.0.0', {})
        with patch.object(runner, 'execute', return_value=self.output), patch.object(runner, 'available', return_value=True):
            registry.validate_version(self.path, 'bank-csv', '1.0.0')
            registry.set_enabled(self.path, 'bank-csv', '1.0.0', True)
            proposal = registry.run(self.path, 'bank-csv', '1.0.0', {})
            self.assertEqual(repository.load(self.path)['transactions'], [])
            proposals.commit(self.path, proposal)
            self.assertEqual(len(repository.load(self.path)['transactions']), 1)
            (profile.runtime_dir(self.path)/'extensions-local.json').unlink()
            with self.assertRaises(ValueError): registry.run(self.path, 'bank-csv', '1.0.0', {})
            item = registry.find(repository.load(self.path), 'bank-csv', '1.0.0')
            registry.target(self.path,item).write_text('print("tampered")')
            with self.assertRaises(ValueError): registry.validate_version(self.path, 'bank-csv', '1.0.0')

    def test_invalid_financial_fixture_cannot_be_enabled(self):
        registry.register(self.path, self.manifest)
        wrong = {'version': 1, 'candidates': [{**self.candidate, 'amount': -1}]}
        with patch.object(runner, 'execute', return_value=wrong):
            with self.assertRaises(ValueError): registry.validate_version(self.path, 'bank-csv', '1.0.0')
        self.assertFalse(registry.find(repository.load(self.path),'bank-csv','1.0.0')['enabled'])

    def test_missing_sandbox_never_falls_back(self):
        with patch.object(runner,'available',return_value=False):
            with self.assertRaisesRegex(ValueError,'No supported OS sandbox'):
                runner.execute('raise Exception("must not execute")', {})

    @unittest.skipUnless(runner.available(), 'Requires the macOS extension sandbox')
    def test_real_sandbox_denies_files_network_and_process_creation(self):
        secret = self.root/'secret.txt'; secret.write_text('private')
        outside = self.root/'outside.txt'
        script = '''import json,os,socket,subprocess,sys
payload=json.load(sys.stdin)
blocked=[]
for label, action in [
 ('read',lambda:open(payload['read']).read()),
 ('write',lambda:open(payload['write'],'w').write('bad')),
 ('network',lambda:socket.socket().bind(('127.0.0.1',0))),
 ('spawn',lambda:subprocess.run(['/usr/bin/true']))]:
 try: action()
 except PermissionError: blocked.append(label)
print(json.dumps(blocked))
'''
        self.assertEqual(runner.execute(script, {'read':str(secret),'write':str(outside)}), ['read','write','network','spawn'])
        self.assertFalse(outside.exists())
        with self.assertRaisesRegex(ValueError,'execution time'):
            runner.execute('while True: pass', {}, timeout=.1)
        with self.assertRaises(ValueError): runner.execute('print("x"*600000)', {})

if __name__=='__main__': unittest.main()

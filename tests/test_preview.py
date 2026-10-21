"""Live preview publication, failure recovery and HTTP capability boundaries."""
import json
import os
from pathlib import Path
import subprocess
import signal
import time
import selectors
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from ledger_core.services import preview, reports, ui_generation
from ledger_core.storage import repository


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.ledger = self.folder / 'book.json'
        repository.save(self.ledger, repository.load(self.ledger))
        self.report = reports.build(self.ledger, start='2026-10-01', end='2026-10-31')
        self.source = self.folder / 'view.openui'
        self.source.write_text('root = LedgerView([Metric("totals.expense")])')
        self.session = preview.PreviewSession(self.report, self.source, self.folder / 'session')

    def publish(self):
        self.session.tick()
        self.session.tick()

    def test_debounce_publish_failure_and_repair_preserve_frozen_data(self):
        before = self.ledger.read_bytes()
        self.session.tick()
        self.assertEqual(self.session.status['version'], 0)
        self.session.tick()
        original = (self.session.directory / 'preview.html').read_bytes()
        self.assertEqual(self.session.status['version'], 1)
        self.source.write_text('root = LedgerView([Missing(')
        self.publish()
        self.assertEqual(self.session.status['state'], 'invalid')
        self.assertTrue(self.session.status['errors'])
        self.assertEqual(self.session.status['version'], 1)
        self.assertEqual((self.session.directory / 'preview.html').read_bytes(), original)
        self.source.write_text('root = LedgerView([Section("调整结构", [Metric("totals.expense")], "blue")])')
        self.publish()
        self.assertEqual(self.session.status['version'], 2)
        self.assertEqual(self.session.status['errors'], [])
        self.assertNotEqual((self.session.directory / 'preview.html').read_bytes(), original)
        self.assertEqual(self.session.status['snapshot_id'], self.report['id'])
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertEqual(os.stat(self.session.directory / 'status.json').st_mode & 0o777, 0o600)

    def test_unchanged_source_does_not_revalidate_or_republish(self):
        self.publish()
        with patch.object(ui_generation, 'compose') as compose:
            self.session.tick()
            compose.assert_not_called()
        self.assertEqual(self.session.status['version'], 1)

    def test_storage_failure_retries_without_publishing_a_new_version(self):
        self.session.tick()
        with patch.object(preview, 'write_text_secure', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.session.tick()
        self.assertEqual(self.session.status['version'], 0)
        self.assertIsNone(self.session.document)
        self.session.tick()
        self.assertEqual(self.session.status['version'], 1)

    def test_source_changed_during_validation_is_not_published(self):
        compose = ui_generation.compose
        def change(*args, **kwargs):
            result = compose(*args, **kwargs)
            self.source.write_text('root = LedgerView([Text("new version")])')
            return result
        with patch.object(ui_generation, 'compose', side_effect=change):
            self.publish()
        self.assertEqual(self.session.status['version'], 0)
        self.assertFalse((self.session.directory / 'preview.html').exists())
        self.session.tick()
        self.assertEqual(self.session.status['version'], 1)

    def test_missing_or_oversized_source_keeps_last_good_page_and_recovers(self):
        self.publish()
        original = self.session.document
        self.source.unlink()
        self.session.tick()
        self.assertEqual(self.session.status['state'], 'invalid')
        self.assertEqual(self.session.document, original)
        self.source.write_text('x' * 500001)
        self.session.tick()
        self.assertEqual(self.session.status['errors'][0]['code'], 'source')
        self.source.write_text('root = LedgerView([Text("restored")])')
        self.publish()
        self.assertEqual(self.session.status['version'], 2)

    def test_html_updates_require_refs_and_reject_invalid_resources(self):
        with self.assertRaises(ValueError):
            preview.PreviewSession(self.report, self.source, self.folder / 'no-refs', mode='html')
        source = self.folder / 'custom.html'
        source.write_text('<h1>自定义展示</h1><details><summary>依据</summary>测试</details>')
        session = preview.PreviewSession(self.report, source, self.folder / 'html-session', mode='html', refs=['totals.expense'])
        session.tick(); session.tick()
        self.assertEqual(session.status['version'], 1)
        original = session.document
        source.write_text('<script src="https://example.com/bad.js"></script>')
        session.tick(); session.tick()
        self.assertEqual(session.status['state'], 'invalid')
        self.assertEqual(session.document, original)

    def test_session_directory_cannot_overwrite_existing_files(self):
        with self.assertRaises(FileExistsError):
            preview.PreviewSession(self.report, self.source, self.session.directory)
        with self.assertRaisesRegex(ValueError, 'outside'):
            preview.run(self.ledger, self.report['id'], self.source, ROOT / 'bad-preview')
        with self.assertRaisesRegex(ValueError, 'interval'):
            preview.run(self.ledger, self.report['id'], self.source, self.folder / 'another', interval=float('nan'))

    def test_cli_watch_help_has_no_extra_runtime_requirement(self):
        result = subprocess.run([str(ROOT / 'scripts/ledger'), 'report', 'watch', '--help'], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--session-dir', result.stdout)

    def test_cli_session_publishes_and_stops_without_changing_ledger(self):
        archived = reports.archive(self.ledger, self.report, format='view-json')
        folder = self.folder / 'cli-session'
        before = self.ledger.read_bytes()
        process = subprocess.Popen([sys.executable, str(ROOT / 'scripts/ledger_tool.py'), 'report', 'watch',
                                    '--ledger', str(self.ledger), '--id', archived['report_id'],
                                    '--openui', str(self.source), '--session-dir', str(folder), '--interval', '0.1'],
                                   text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=10), 'watch startup timed out')
            first = process.stdout.readline()
            self.assertTrue(first, process.stderr.read() if process.poll() is not None else 'watch produced no URL')
            started = json.loads(first)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with urlopen(started['url'] + 'status') as response:
                    state = json.load(response)
                if state['version'] == 1:
                    break
                time.sleep(0.1)
            self.assertEqual(state['state'], 'ready')
            self.assertTrue((folder / 'preview.html').exists())
            self.assertEqual(self.ledger.read_bytes(), before)
            process.send_signal(signal.SIGINT)
            process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr.read())
            self.assertEqual(json.loads((folder / 'status.json').read_text())['state'], 'stopped')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()

    def test_http_serves_only_capability_scoped_preview_and_diagnostics(self):
        self.publish()
        server, url = preview.server_for(self.session)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(url) as response:
                self.assertIn(b'poll()', response.read())
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
            with urlopen(url + 'status') as response:
                self.assertEqual(json.load(response)['version'], 1)
            with urlopen(url + 'view?v=1') as response:
                self.assertEqual(response.read().decode(), self.session.document)
            for suffix, headers in [('status', {'Host': 'attacker.example'}), ('status', {'Origin': 'https://example.com'}),
                                    ('status', {'Sec-Fetch-Site': 'cross-site'}), ('../../book.json', {}), ('api/ledger', {})]:
                with self.subTest(suffix=suffix, headers=headers), self.assertRaises(HTTPError):
                    urlopen(Request(url + suffix, headers=headers))
            with self.assertRaises(HTTPError):
                urlopen(url.split('/')[0] + '//' + url.split('/')[2] + '/status')
            with self.assertRaises(HTTPError):
                urlopen(Request(url + 'status', data=b'{}', method='POST'))
            self.source.write_text('bad')
            self.publish()
            with urlopen(url + 'status') as response:
                status = json.load(response)
                self.assertEqual(status['state'], 'invalid')
                self.assertEqual(status['version'], 1)
            with urlopen(url + 'view') as response:
                self.assertEqual(response.read().decode(), self.session.document)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

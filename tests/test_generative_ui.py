"""Presentation must not invent facts, expose private context or mutate a ledger."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from ledger_core.storage import repository
from ledger_core.services import generative_ui as ui, reports, imports, proposals, profile
from ledger_core.adapters.runtime_cli import context


class GenerativeUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "book.json"
        repository.save(self.path, repository.load(self.path))
        proposal = proposals.prepare(self.path, {"reason": "fixture", "operations": [
            {"action": "add", "after": {"amount": 12.35, "occurred_at": "2026-10-01", "merchant": "测试商户"}},
            {"action": "add", "after": {"amount": 9.65, "occurred_at": "2026-10-02", "merchant": "第二商户"}},
        ]})
        proposals.commit(self.path, proposal)
        self.report = reports.build(self.path, start="2026-10-01", end="2026-10-31")

    def test_projection_matches_core_and_excludes_private_data(self):
        report = copy.deepcopy(self.report)
        report["profile_document"] = "PRIVATE_PROFILE_SENTINEL"
        report["balance_inputs"] = {"transactions": ["OUT_OF_PERIOD_SENTINEL"]}
        report["transactions"][0]["sources"] = [{"raw": "RAW_BANK_SENTINEL"}]
        report["digest"] = repository.fingerprint({k: v for k, v in report.items() if k != "digest"})
        view = ui.project(report)
        self.assertEqual(view["datasets"]["totals.expense"]["value"], "22.00")
        self.assertEqual(view["datasets"]["totals.expense"]["evidence"], report["evidence"]["expense"])
        for private in ("PRIVATE_PROFILE_SENTINEL", "OUT_OF_PERIOD_SENTINEL", "RAW_BANK_SENTINEL"):
            self.assertNotIn(private, json.dumps(view))
            self.assertNotIn(private, ui.document(view))
        self.assertFalse(view["coverage"]["complete"])

    def test_custom_composition_only_includes_requested_datasets(self):
        layout = {"version": 1, "blocks": [{"component": "Metric", "ref": "totals.expense"}]}
        view = ui.project(self.report, layout)
        self.assertEqual(set(view["datasets"]), {"totals.expense"})
        self.assertIn('Metric("totals.expense")', view["lang"])
        self.assertEqual(view["coverage"]["recorded_rows"], 2)

    def test_untrusted_layout_cannot_inject_values_tools_or_markup(self):
        for block in (
            {"component": "Metric", "ref": "totals.expense", "value": "999"},
            {"component": "HtmlArtifact", "ref": "transactions"},
            {"component": "Metric", "ref": '"); Mutation("delete")'},
            {"component": "Metric", "ref": "__proto__"},
            {"component": "ImportRows", "ref": "import_rows"},
        ):
            with self.subTest(block=block), self.assertRaises(ValueError):
                ui.project(self.report, {"version": 1, "blocks": [block]})

    def test_html_is_offline_and_escapes_source_strings(self):
        report = copy.deepcopy(self.report)
        report["transactions"][0]["merchant"] = '</script><script>alert("bad")</script>'
        report["digest"] = repository.fingerprint({k: v for k, v in report.items() if k != "digest"})
        document = reports.render(report, "openui")
        self.assertNotIn('</script><script>alert("bad")', document)
        self.assertNotIn('<script src=', document)
        self.assertIn("connect-src 'none'", document)

    def test_missing_assets_retains_same_projection_fallback(self):
        with patch.object(ui, "asset_bytes", side_effect=FileNotFoundError):
            document = reports.render(self.report, "openui")
        self.assertIn("交互资源不可用", document)
        self.assertIn("22.00", document)
        self.assertNotIn("balance_inputs", document)

    def test_archive_replays_exact_bytes_without_installed_renderer(self):
        saved = reports.archive(self.path, self.report, format="openui")
        original = Path(saved["artifact"]).read_text()
        proposals.commit(self.path, proposals.prepare(self.path, {"reason": "later", "operations": [
            {"action": "add", "after": {"amount": 100, "occurred_at": "2026-10-03"}}]}))
        with patch.object(ui, "asset_bytes", side_effect=FileNotFoundError):
            self.assertEqual(reports.render_archive(self.path, saved["report_id"], "openui"), original)
        self.assertEqual(json.loads(reports.render_archive(self.path, saved["report_id"], "view-json"))["datasets"]["totals.expense"]["value"], "22.00")
        Path(saved["artifact"]).write_text(original + "tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            reports.render_archive(self.path, saved["report_id"], "openui")

    def test_archive_keeps_custom_layout(self):
        layout = {"version": 1, "blocks": [{"component": "Breakdown", "ref": "by_day"}]}
        saved = reports.archive(self.path, self.report, format="view-json", layout=layout)
        view = json.loads(reports.render_archive(self.path, saved["report_id"], "view-json"))
        self.assertEqual(view["layout"], layout)
        self.assertNotIn("totals.expense", view["datasets"])

    def test_import_preview_and_errors_are_read_only_and_redacted(self):
        source = self.path.parent / "rows.csv"
        source.write_text("occurred_at,amount,type,merchant,secret\n2026-10-01,12.35,expense,测试商户,PRIVATE_CELL\n2026-10-03,5,expense,新商户,PRIVATE_CELL\nbad,4,expense,错误商户,PRIVATE_ERROR_CELL\n")
        before = self.path.read_bytes()
        plan = imports.prepare(self.path, source)
        report = reports.from_import(plan)
        view = ui.project(report)
        self.assertEqual(view["datasets"]["import_summary"]["new"], 1)
        self.assertEqual(view["datasets"]["import_summary"]["review"], 1)
        self.assertEqual(view["datasets"]["import_summary"]["errors"], 1)
        self.assertFalse(view["coverage"]["ready_at_prepare"])
        self.assertNotIn("PRIVATE_CELL", json.dumps(view))
        self.assertNotIn("PRIVATE_ERROR_CELL", json.dumps(view))
        saved = reports.archive(self.path, report, format="openui")
        self.assertIn("来源记录", Path(saved["artifact"]).read_text())
        self.assertEqual(self.path.read_bytes(), before)

    def test_context_discovers_installed_renderer_without_changing_old_manifest(self):
        runtime_file = profile.runtime_dir(self.path) / "runtime.json"
        before = runtime_file.read_bytes()
        capability = context(self.path, "report")["capabilities"]["presentation"]["openui"]
        self.assertTrue(capability["read_only"])
        self.assertTrue(capability["available"])
        self.assertIn("Budget", capability["components"])
        self.assertEqual(runtime_file.read_bytes(), before)

    def test_cli_write_output_and_reject_overwriting(self):
        target = self.path.parent / "report.html"
        command = [sys.executable, str(ROOT / "scripts/ledger_tool.py"), "report", "build", "--ledger", str(self.path),
                   "--start", "2026-10-01", "--end", "2026-10-31", "--format", "openui", "--output", str(target)]
        subprocess.run(command, check=True, capture_output=True)
        original = target.read_bytes()
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(target.read_bytes(), original)

    def test_agent_source_chooses_nested_layout_and_only_selected_data(self):
        from ledger_core.services import ui_generation as gen
        source = 'root = LedgerView([Section("预算与支出", [Columns([Budget("budgets"), Metric("totals.expense")], 2)], "amber")])'
        view = gen.compose(self.report, lang=source)
        self.assertEqual(view["lang"], source)
        self.assertEqual(set(view["datasets"]), {"budgets", "totals.expense"})
        self.assertEqual(view["datasets"]["totals.expense"]["value"], "22.00")
        self.assertNotIn("profile_document", json.dumps(view))
        self.assertEqual(view["snapshot_digest"], self.report["digest"])
        text_only = gen.compose(self.report, lang='root = LedgerView([Text("没有需要展示的数值")])')
        self.assertEqual(text_only["datasets"], {})
        self.assertIn("覆盖情况", ui.fallback(text_only))

    def test_agent_source_errors_are_structured_and_no_node_is_explicit(self):
        from ledger_core.services import ui_generation as gen
        for source in ('root = LedgerView([Missing("x")])', 'root = LedgerView([Metric("bad")])'):
            with self.assertRaises(gen.GenerationError) as caught:
                gen.compose(self.report, lang=source)
            self.assertTrue(json.loads(str(caught.exception))["errors"])
        with patch.object(gen.shutil, "which", return_value=None):
            with self.assertRaisesRegex(gen.GenerationError, "Node.js"):
                gen.compose(self.report, lang='root = LedgerView([Metric("totals.expense")])')
            self.assertIn("22.00", reports.render(self.report, "markdown"))

    def test_authored_archive_freezes_source_and_bundle(self):
        from ledger_core.services import ui_generation as gen
        source = 'root = LedgerView([Disclosure("金额依据", [Metric("totals.expense")])])'
        view = gen.compose(self.report, lang=source)
        saved = reports.archive(self.path, self.report, format="openui", view=view)
        original = Path(saved["artifact"]).read_text()
        with patch.object(ui, "asset_bytes", side_effect=FileNotFoundError):
            self.assertEqual(reports.render_archive(self.path, saved["report_id"], "openui"), original)
        self.assertEqual(json.loads(reports.render_archive(self.path, saved["report_id"], "view-json"))["lang"], source)

    def test_view_json_archive_keeps_agent_source_when_rendered_later(self):
        from ledger_core.services import ui_generation as gen
        source = 'root = LedgerView([Text("自定义说明"), Metric("totals.expense")])'
        saved = reports.archive(self.path, self.report, format="view-json", view=gen.compose(self.report, lang=source))
        rendered = reports.render_archive(self.path, saved["report_id"], "openui")
        import base64, re
        payload = re.search(r'<script id="ledger-view"[^>]*>(.*?)</script>', rendered)[1]
        self.assertEqual(json.loads(base64.b64decode(payload))["lang"], source)

    def test_html_receives_only_selected_data_and_is_archived_with_source(self):
        from ledger_core.services import ui_generation as gen
        import base64, re
        source = '<!doctype html><html><head><style>body{color:navy}</style></head><body><svg viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg><output id="amount"></output><script>document.querySelector("output").textContent=window.ledgerData.datasets["totals.expense"].value;</script></body></html>'
        before = self.path.read_bytes()
        view = gen.compose(self.report, html_source=source, refs=["totals.expense"])
        self.assertEqual(set(view["datasets"]), {"totals.expense"})
        self.assertIn("connect-src &#x27;none&#x27;", view["html_document"])
        payload = re.search(r'atob\("([A-Za-z0-9+/=]+)"\)', view["html_scripts"][0])[1]
        self.assertEqual(json.loads(base64.b64decode(payload))["datasets"]["totals.expense"]["value"], "22.00")
        saved = reports.archive(self.path, self.report, format="openui", view=view)
        self.assertEqual((Path(saved["artifact"]).parent / "source.html").read_text(), source)
        self.assertEqual(reports.render_archive(self.path, saved["report_id"], "openui"), Path(saved["artifact"]).read_text())
        self.assertEqual(self.path.read_bytes(), before)

    def test_html_rejects_external_resources_and_authority_channels(self):
        from ledger_core.services import ui_generation as gen
        for source in ('<script src="https://example.com/a.js"></script>', '<iframe srcdoc="test"></iframe>',
                       '<meta http-equiv="refresh" content="0;url=https://example.com">',
                       '<a href="https://example.com">link</a>', '<button onclick="bad()">x</button>',
                       '<base href="https://example.com">', '<img src="file:///private/file">'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                gen.compose(self.report, html_source=source, refs=["totals.expense"])
        with self.assertRaises(ValueError):
            gen.compose(self.report, html_source='<p>x</p>')
        with self.assertRaises(ValueError):
            gen.compose(self.report, html_source='<p>x</p>', refs=["profile_document"])

    def test_cli_generation_uses_frozen_data_updates_preview_and_preserves_archive(self):
        saved = reports.archive(self.path, self.report, format="view-json")
        source = self.path.parent / "page.openui"
        source.write_text('root = LedgerView([Metric("totals.expense")])')
        target = self.path.parent / "preview.html"
        cli = [sys.executable, str(ROOT / "scripts/ledger_tool.py"), "report"]
        common = ["--ledger", str(self.path), "--id", saved["report_id"]]
        def run(*args):
            return subprocess.run(cli + list(args), text=True, capture_output=True)
        before = self.path.read_bytes()
        checked = run("validate", *common, "--openui", str(source))
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["valid"])
        data = run("data", *common, "--refs", "totals.expense")
        self.assertEqual(set(json.loads(data.stdout)["datasets"]), {"totals.expense"})
        command = ["compose", *common, "--openui", str(source), "--output", str(target)]
        first = run(*command)
        self.assertEqual(first.returncode, 0, first.stderr)
        original = target.read_text()
        source.write_text('root = LedgerView([Section("新的版式", [Metric("totals.expense")], "violet")])')
        self.assertNotEqual(run(*command).returncode, 0)
        replaced = run(*command, "--replace")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertNotEqual(target.read_text(), original)
        archived = run("compose", *common, "--openui", str(source), "--archive")
        self.assertEqual(archived.returncode, 0, archived.stderr)
        self.assertNotEqual(json.loads(archived.stdout)["report_id"], saved["report_id"])
        # Existing archives and arbitrary files cannot be overwritten as previews.
        denied = run("compose", *common, "--openui", str(source), "--output", str(Path(saved["data"]).parent / "preview.html"), "--replace")
        self.assertNotEqual(denied.returncode, 0)
        target.write_text("user file")
        self.assertNotEqual(run(*command, "--replace").returncode, 0)
        self.assertEqual(target.read_text(), "user file")
        source.write_text('root = LedgerView([Metric("invalid")])')
        error = run("validate", *common, "--openui", str(source))
        self.assertNotEqual(error.returncode, 0)
        self.assertFalse(json.loads(error.stderr)["valid"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_generation_spec_wrapper_works_without_a_ledger(self):
        result = subprocess.run([str(ROOT / "scripts/ledger"), "report", "ui-spec"], cwd=self.path.parent,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        spec = json.loads(result.stdout)
        self.assertEqual(spec["version"], 2)
        self.assertIn("schema", spec["library"])
        self.assertNotIn("schema", spec)
        self.assertIn("Columns", spec["library"]["components"])
        self.assertIn("window.ledgerData", spec["html"]["data_global"])

    def test_asset_integrity_and_packaging(self):
        from package_skill import validate
        manifest, assets = ui.asset_bytes()
        self.assertEqual(manifest["dependencies"]["@openuidev/react-lang"], "1.0.1")
        self.assertTrue(assets["renderer.js"])
        _, included = validate(ROOT)
        self.assertIn("assets/openui/renderer.js", included)
        self.assertIn("assets/openui/THIRD_PARTY_LICENSES.txt", included)
        self.assertFalse(any("node_modules" in p or p.startswith("frontend/") for p in included))


if __name__ == "__main__":
    unittest.main()

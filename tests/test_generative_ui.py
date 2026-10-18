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

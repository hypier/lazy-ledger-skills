"""Exercise the extracted distributable, independently of checkout imports."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package_skill import package, validate, ValidationError


class InstalledSkillTest(unittest.TestCase):
    def test_packaged_skill_runs_without_html_for_core_workflows(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive, included = package(ROOT, folder / "dist")
            self.assertIn("assets/ledger-bill-canvas.html", included)
            self.assertFalse(any("-runtime/" in item or item.startswith("tests/") for item in included))
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(folder / "installed")
            installed = folder / "installed" / "lazy-ledger"
            ledger = folder / "personal" / "book.json"
            env = {k: v for k,v in os.environ.items() if k != "PYTHONPATH"}
            def run(*args):
                result = subprocess.run([sys.executable, str(installed / "scripts/ledger_tool.py"), *args, "--ledger", str(ledger)], cwd=folder,
                                        env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout
            run("init", "--git-mode", "off")
            run("add", "--text", "午饭28", "--date", "2026-10-01")
            # Optional legacy renderer is usable in the complete distribution.
            report = folder / "snapshot.html"
            run("bill", "render", "--month", "2026-10", "--output", str(report))
            self.assertIn("<canvas", report.read_text())
            shutil.rmtree(installed / "assets")
            run("record", "--text", "咖啡12", "--date", "2026-10-02")
            rows = folder / "rows.tsv"
            rows.write_text("occurred_at\ttype\tamount\tcategory\tmerchant\n2026-10-03\texpense\t5\t交通\t公交\n")
            run("import-tsv", "--input", str(rows))
            self.assertEqual(json.loads(run("context", "--task", "report"))["coverage"]["transactions"], 3)
            run("habit", "set", "--phrase", "地铁", "--amount", "3")
            run("schema", "register", "--name", "x_trip", "--type", "string", "--description", "行程")
            saved = json.loads(run("bill", "save", "--month", "2026-10", "--body", "按已记录流水汇总。"))
            self.assertNotIn("file", saved)
            self.assertIn("按已记录流水汇总", run("bill", "show", "--month", "2026-10"))
            self.assertEqual(json.loads(run("summary", "--month", "2026-10", "--json"))["totals"]["expense"], 45)
            rows.write_text("occurred_at\ttype\tamount\tcategory\tmerchant\tsource_transaction_id\n2026-10-04\texpense\t7\t交通\t地铁\tref-4\n")
            plan = folder / "import-plan.json"
            plan.write_text(run("import", "prepare", "--input", str(rows), "--expected-count", "1", "--expected-total", "7"))
            run("import", "commit", "--input", str(plan))
            # The portable bundle is installed; permissions are still device-local.
            source = folder / "converter"; source.mkdir()
            output = {"version": 1, "candidates": [{"amount": 4, "occurred_at": "2026-10-05", "x_trip": "work"}]}
            (source / "main.py").write_text("print(" + repr(json.dumps(output)) + ")\n")
            manifest = source / "manifest.json"
            manifest.write_text(json.dumps({"id": "example", "version": "1.0.0", "entrypoint": "main.py", "core_schema": [2],
                "permissions": {"data": "input-only", "network": False, "writes": "scratch-only"}, "fixtures": [{"input": {}, "expected": output}]}))
            run("extension", "register", "--manifest", str(manifest))
            if sys.platform == "darwin":
                run("extension", "validate", "--id", "example", "--version", "1.0.0")
                run("extension", "enable", "--id", "example", "--version", "1.0.0")
                payload = folder / "input.json"; payload.write_text("{}")
                proposal = folder / "proposal.json"
                proposal.write_text(run("extension", "run", "--id", "example", "--version", "1.0.0", "--input", str(payload)))
                run("proposal", "commit", "--input", str(proposal))
                migration = folder / "migration.json"
                migration.write_text(json.dumps({"reason":"字段改名", "operations":[{"field":"x_trip", "rename":"x_project"}]}))
                preview = folder / "migration-plan.json"; preview.write_text(run("metadata-migrate", "--input", str(migration)))
                run("metadata-migrate", "--input", str(preview), "--apply")
            archive = json.loads(run("report", "build", "--start", "2026-10-01", "--end", "2026-10-31", "--archive"))
            expected = 56 if sys.platform == "darwin" else 52
            self.assertEqual(json.loads(Path(archive["data"]).read_text())["metrics"]["totals"]["expense"], expected)
            self.assertTrue(Path(archive["artifact"]).is_file())

    def test_packaging_rejects_filtered_required_runtime_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "lazy-ledger"
            root.mkdir()
            shutil.copy2(ROOT / "SKILL.md", root / "SKILL.md")
            shutil.copytree(ROOT / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(ROOT / "references", root / "references")
            shutil.copytree(ROOT / "assets", root / "assets")
            (root / ".skillignore").write_text((ROOT / ".skillignore").read_text() + "\n*-bill-*.html\n")
            with self.assertRaisesRegex(ValidationError, "filtered"):
                validate(root)

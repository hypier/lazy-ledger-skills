"""Behavioral contracts for the shared core and isolated ledger runtime."""
import contextlib
from datetime import date
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ledger_tool as cli
from ledger_core.domain.model import empty_ledger
from ledger_core.services.analysis import month_trend
from ledger_core.adapters.runtime_cli import context, migrate
from ledger_core.domain.metrics import account_balances, budget_progress, summarize
from ledger_core.domain.money import decimal_amount
from ledger_core.domain.semantics import settle
from ledger_core.services import profile, history
from ledger_core.storage import repository, transaction
from ledger_core.storage.documents import atomic_write_json


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "family.json"
        self.parser = cli.build_parser()
        self.run_cmd("init")

    def run_cmd(self, *args, path=None):
        parsed = self.parser.parse_args([*args, "--ledger", str(path or self.path)])
        if hasattr(parsed.func, "result"):
            return parsed.func.result(parsed)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            parsed.func(parsed)
        output = stream.getvalue()
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output

    def add(self, *args):
        return self.run_cmd("add", *args)["added"][0]

    def test_context_is_neutral_and_reads_do_not_change_bundle(self):
        files = [self.path, profile.profile_path(self.path), profile.runtime_dir(self.path) / "runtime.json"]
        before = [p.read_bytes() for p in files]
        ctx = context(self.path, "analyze")
        self.assertIsNone(ctx["profile"]["defaults"]["account_id"])
        self.assertEqual(ctx["capabilities"]["presentation"]["templates"], [])
        self.assertEqual(ctx["needs_setup"], ["git_preference"])
        self.run_cmd("habit", "list", "--json")
        self.run_cmd("summary", "--json")
        self.assertEqual(before, [p.read_bytes() for p in files])
        self.assertFalse(history.location(self.path).exists())

    def test_mixed_currency_projection_is_refused(self):
        with self.assertRaises(ValueError):
            summarize([{"type": "expense", "currency": "CNY", "amount": 1}, {"type": "expense", "currency": "USD", "amount": 1}])

    def test_force_init_retains_identity_and_backs_up_profile(self):
        self.run_cmd("history", "configure", "--mode", "local")
        self.run_cmd("account", "update", "--id", "acc_cash", "--default")
        identity = repository.load(self.path)["ledger_id"]
        self.add("--amount", "10")
        self.run_cmd("init", "--force")
        ledger = repository.load(self.path)
        self.assertEqual(ledger["ledger_id"], identity)
        self.assertEqual(ledger["transactions"], [])
        self.assertIsNone(ledger.profile["defaults"]["account_id"])
        self.assertTrue(list((self.root / "backups").glob("*.user-profile.md")))

    def test_same_directory_ledgers_have_independent_profiles(self):
        other = self.root / "work.json"
        self.run_cmd("init", path=other)
        self.run_cmd("account", "update", "--id", "acc_cash", "--default")
        self.assertEqual(context(self.path, "record")["profile"]["defaults"]["account_id"], "acc_cash")
        self.assertIsNone(context(other, "record")["profile"]["defaults"]["account_id"])
        self.assertEqual(self.add("--amount", "10")["account_id"], "acc_cash")

    def test_confirmed_rules_survive_observation_and_keep_prose(self):
        doc = profile.profile_path(self.path)
        doc.write_text(doc.read_text() + "\n每月关心交通开支。\n")
        self.run_cmd("habit", "set", "--phrase", "午饭", "--amount", "18")
        self.add("--text", "午饭29")
        rules, _, text = profile.read(self.path)
        self.assertEqual(rules["habits"][0]["amount"], 18)
        self.assertIn("每月关心交通开支", text)
        self.assertEqual(self.add("--text", "午饭")["amount"], 18)

    def test_disabled_and_expired_rules_never_supply_amount(self):
        rules, _, _ = profile.read(self.path)
        for extra in ({"enabled": False}, {"expires_on": "2000-01-01"}):
            rules["habits"] = [{"phrase": "午饭", "amount": 18, **extra}]
            profile.profile_path(self.path).write_text(profile.render(rules))
            with self.assertRaises(SystemExit):
                self.add("--text", "午饭")
        self.assertEqual(repository.load(self.path)["transactions"], [])

    def test_revision_and_external_change_guards(self):
        first, stale = repository.load(self.path), repository.load(self.path)
        first["x_note"] = "first"
        repository.save(self.path, first)
        stale["x_note"] = "stale"
        with self.assertRaises(repository.ConflictError):
            repository.save(self.path, stale)
        stale = repository.load(self.path)
        raw = json.loads(self.path.read_text()); raw["x_note"] = "external"
        atomic_write_json(self.path, raw)
        with self.assertRaises(repository.ConflictError):
            repository.save(self.path, stale)
        self.assertEqual(json.loads(self.path.read_text())["x_note"], "external")

    def test_profile_and_manifest_changes_invalidate_loaded_writer(self):
        for target in (profile.profile_path(self.path), profile.runtime_dir(self.path) / "runtime.json"):
            ledger = repository.load(self.path)
            before = self.path.read_bytes()
            target.write_text(target.read_text() + "\n")
            with self.assertRaises(ValueError):
                repository.save(self.path, ledger)
            self.assertEqual(self.path.read_bytes(), before)

    def test_idempotent_add_and_stale_proposal(self):
        first = self.add("--amount", "23.10", "--operation-id", "request-1")
        revision = repository.load(self.path)["revision"]
        again = self.add("--amount", "23.10", "--operation-id", "request-1")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(repository.load(self.path)["revision"], revision)
        with self.assertRaises(SystemExit):
            self.add("--amount", "25", "--operation-id", "request-1")
        with self.assertRaises(SystemExit):
            self.add("--amount", "30", "--expected-revision", str(revision - 1))
        self.assertEqual(len(repository.load(self.path)["transactions"]), 1)

    def test_decimal_rounding_and_excluded_cash_flow(self):
        self.assertEqual(str(decimal_amount("2.675")), "2.68")
        for value in (True, "NaN", "Infinity"):
            with self.assertRaises(ValueError):
                decimal_amount(value)
        self.add("--amount", "2.675", "--account", "现金", "--not-mine")
        ledger = repository.load(self.path)
        self.assertEqual(summarize(ledger["transactions"])["totals"]["expense"], 0)
        cash = next(a for a in account_balances(ledger) if a["id"] == "acc_cash")
        self.assertEqual(cash["balance"], -2.68)
        self.assertFalse(cash["opening_confirmed"])

    def test_partial_repayment_and_dated_writeoff_agree_with_budget_and_balance(self):
        advance = self.add("--amount", "100.10", "--advance", "--account", "现金", "--date", "2026-08-01")
        income = self.add("--amount", "40.05", "--type", "income", "--account", "现金", "--date", "2026-09-01")
        self.run_cmd("advance", "settle", "--id", advance["id"], "--by", income["id"])
        self.run_cmd("advance", "writeoff", "--id", advance["id"], "--date", "2026-10-01")
        ledger = repository.load(self.path)
        old = next(t for t in ledger["transactions"] if t["id"] == advance["id"])
        self.assertEqual(old["written_off_amount"], 60.05)
        self.assertEqual(summarize(ledger["transactions"])["totals"]["expense"], 60.05)
        self.assertEqual(summarize(ledger["transactions"])["totals"]["income"], 0)
        self.assertEqual(next(a for a in account_balances(ledger) if a["id"] == "acc_cash")["balance"], -60.05)
        august = self.run_cmd("summary", "--month", "2026-08", "--json")
        october = self.run_cmd("summary", "--month", "2026-10", "--json")
        self.assertEqual(august["totals"]["expense"], 0)
        pack = self.run_cmd("bill", "show", "--month", "2026-08", "--json")
        self.assertEqual(sum(day["amount"] for day in pack["facts"]["daily_expense"]), 0)
        self.assertEqual(month_trend(ledger["transactions"], end_date=date(2026, 10, 31), months=6)[-1]["expense"], 60.05)
        self.assertEqual(october["totals"]["expense"], 60.05)
        self.run_cmd("budget", "set", "--month", "2026-10", "--amount", "100")
        october = self.run_cmd("summary", "--month", "2026-10", "--json")
        self.assertEqual(october["budgets"][0]["spent"], 60.05)
        self.assertTrue(self.run_cmd("doctor", "--json")["ok"])
        with self.assertRaises(SystemExit):
            self.run_cmd("delete", "--id", income["id"], "--yes")

    def test_salary_or_vague_arrival_does_not_settle_equal_advance(self):
        self.add("--text", "公司垫付800")
        self.add("--text", "工资到账800")
        self.add("--text", "公司把800打过来了", "--allow-duplicate")
        advance = next(t for t in repository.load(self.path)["transactions"] if t.get("advance"))
        self.assertEqual(advance["advance"], "open")
        with self.assertRaises(SystemExit):
            self.run_cmd("advance", "settle", "--id", advance["id"])

    def test_settlement_rejects_excess_and_wrong_currency_without_mutation(self):
        advance = {"id": "a", "advance": "open", "amount": 10, "currency": "CNY", "occurred_at": "2026-01-01"}
        income = {"id": "i", "type": "income", "amount": 11, "currency": "CNY", "occurred_at": "2026-02-01"}
        for changes in ({}, {"amount": 5, "currency": "USD"}, {"amount": 5, "occurred_at": "2025-12-01"}):
            with self.assertRaises(ValueError):
                settle(advance, {**income, **changes})
            self.assertNotIn("advance_settlements", advance)

    def test_interrupted_bundle_recovers_all_files_before_read(self):
        ledger = repository.load(self.path)
        ledger["x_marker"] = "new"
        ledger.profile_override = {**ledger.profile, "goals": ["check recovery"]}
        original = transaction.write_text_secure
        calls = []
        def interrupt(path, text):
            calls.append(str(path))
            if len(calls) == 2:
                raise OSError("simulated interruption")
            return original(path, text)
        with patch.object(transaction, "write_text_secure", side_effect=interrupt):
            with self.assertRaises(OSError):
                repository.save(self.path, ledger)
        recovered = repository.load(self.path)
        self.assertEqual(recovered["x_marker"], "new")
        self.assertEqual(recovered.profile["goals"], ["check recovery"])
        self.assertFalse((profile.runtime_dir(self.path) / "pending.json").exists())

    def test_unknown_versions_or_malformed_collections_are_not_erased(self):
        original = self.path.read_text()
        for change in ({"schema_version": 99}, {"transactions": {}}, {"preferences": []}):
            value = json.loads(original); value.update(change)
            atomic_write_json(self.path, value)
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                repository.load(self.path)
            self.assertEqual(self.path.read_bytes(), before)

    def test_optional_field_registry_is_visible_and_financial_change_is_refused(self):
        self.run_cmd("schema", "register", "--name", "x_project", "--type", "string", "--description", "项目")
        self.assertIn("x_project", context(self.path, "import")["capabilities"]["custom_fields"])
        with self.assertRaises(SystemExit):
            self.run_cmd("schema", "register", "--name", "amount", "--type", "number", "--description", "金额")
        ledger = repository.load(self.path)
        ledger.runtime["extensions"].append({"id": "unknown-financial-model", "enabled": True})
        with self.assertRaises(ValueError):
            repository.save(self.path, ledger)

    def test_migration_preview_backup_and_unknown_field_preservation(self):
        legacy = self.root / "legacy.json"
        data = empty_ledger(); data.update(schema_version=1, x_custom={"hello": "world"})
        data["preferences"]["default_account_id"] = "acc_wechat"
        atomic_write_json(legacy, data)
        before = legacy.read_bytes()
        self.assertFalse(migrate(legacy)["applied"])
        self.assertEqual(legacy.read_bytes(), before)
        result = migrate(legacy, apply=True)
        self.assertEqual((Path(result["backup"]) / "ledger.json").read_bytes(), before)
        new = repository.load(legacy)
        self.assertEqual(new["schema_version"], 2)
        self.assertEqual(new["x_custom"], {"hello": "world"})
        self.assertIsNone(new.profile["defaults"]["account_id"])

    def test_local_history_is_isolated_and_restore_adds_commit(self):
        subprocess.run(["git", "init", str(self.root)], check=True, capture_output=True)
        unrelated = self.root / "private-note.txt"; unrelated.write_text("not in snapshot")
        self.run_cmd("history", "configure", "--mode", "local")
        first = history.list_history(self.path)["versions"][0]["commit"]
        self.add("--amount", "32")
        second = history.list_history(self.path)["versions"][0]["commit"]
        self.assertNotEqual(first, second)
        self.assertEqual(set(history.git(self.path, "ls-files").stdout.splitlines()), set(history.FILES))
        self.assertEqual(history.git(self.path, "remote").stdout, "")
        preview = history.restore(self.path, first)
        self.assertEqual(preview["transactions_after"], 0)
        self.assertEqual(len(repository.load(self.path)["transactions"]), 1)
        applied = history.restore(self.path, first, True)
        self.assertTrue(applied["applied"])
        self.assertEqual(repository.load(self.path)["transactions"], [])
        self.assertEqual(len(history.list_history(self.path)["versions"]), 3)
        self.assertTrue(unrelated.exists())

    def test_history_failure_does_not_fail_or_repeat_the_saved_transaction(self):
        self.run_cmd("history", "configure", "--mode", "local")
        with patch.object(history, "record", side_effect=ValueError("simulated Git failure")):
            first = self.add("--amount", "30", "--operation-id", "request")
        again = self.add("--amount", "30", "--operation-id", "request")
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(repository.load(self.path)["transactions"]), 1)
        self.assertEqual(history.record(self.path)["state"], "recorded")

    def test_remote_configuration_needs_explicit_private_target(self):
        with self.assertRaises(ValueError):
            history.configure(self.path, "remote", "https://example.com/private.git")
        with self.assertRaises(ValueError):
            history.configure(self.path, "remote", "https://token@example.com/private.git", True)
        self.assertFalse(history.location(self.path).exists())

    def test_fast_forward_sync_and_divergence_preserve_both_versions(self):
        # Local bare transport exercises synchronization without any network/account.
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        self.run_cmd("history", "configure", "--mode", "local")
        history.git(self.path, "remote", "add", "origin", str(remote))
        atomic_write_json(profile.runtime_dir(self.path) / "git-local.json", {"mode": "remote"})
        history.sync(self.path)
        other = self.root / "device-b.json"
        profile.runtime_dir(other).mkdir()
        subprocess.run(["git", "clone", "--branch", "main", str(remote), str(history.location(other))], check=True, capture_output=True)
        for name, target in {"ledger.json": other, "user-profile.md": profile.profile_path(other), "runtime.json": profile.runtime_dir(other) / "runtime.json"}.items():
            shutil.copy2(history.location(other) / name, target)
        atomic_write_json(profile.runtime_dir(other) / "git-local.json", {"mode": "remote"})
        self.add("--amount", "55")
        history.sync(self.path)
        history.sync(other)
        self.assertEqual(len(repository.load(other)["transactions"]), 1)
        head = history.git(self.path, "rev-parse", "HEAD").stdout
        revision = repository.load(other)["revision"]
        history.sync(self.path)
        history.sync(other)
        self.assertEqual(history.git(other, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(repository.load(other)["revision"], revision)
        # Simultaneous independent records merge by stable IDs.
        self.add("--amount", "66")
        self.run_cmd("add", "--amount", "77", path=other)
        history.sync(self.path)
        self.assertTrue(history.sync(other)["merged"])
        history.sync(self.path)
        self.assertEqual(sorted(r["amount"] for r in repository.load(self.path)["transactions"]), [55, 66, 77])
        self.assertEqual(repository.load(self.path)["transactions"], repository.load(other)["transactions"])


if __name__ == "__main__":
    unittest.main()

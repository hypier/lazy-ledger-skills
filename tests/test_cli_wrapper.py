"""Tests for the `scripts/ledger` wrapper and the lookup surface it exposes.

The wrapper injects `--ledger`, and that flag belongs to the innermost
subparser. These tests exist mainly to keep that injection position correct:
`account list --ledger X` works, `account --ledger X list` does not.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ledger"


def run_wrapper(*args, cwd=None, check=True):
    result = subprocess.run(
        [str(WRAPPER), *args],
        cwd=cwd or str(ROOT),
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {result.args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class WrapperTest(unittest.TestCase):
    def test_wrapper_is_executable(self):
        self.assertTrue(os.access(WRAPPER, os.X_OK), "scripts/ledger must be executable")

    def test_wrapper_injects_ledger_after_second_level_subcommand(self):
        # Regression: --ledger must land after `list`, otherwise argparse
        # rejects it as an invalid account subcommand.
        with tempfile.TemporaryDirectory() as tmp:
            run_wrapper("add", "--text", "地铁 5", cwd=tmp)
            result = run_wrapper("account", "list", "--json", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertIn("accounts", payload)
            # Default resolution is ./lazy-ledger.json relative to the caller.
            self.assertTrue(Path(tmp, "data", "ledgers", "default", "ledger.json").exists())

    def test_wrapper_handles_habit_and_bill_subcommands(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_wrapper("add", "--text", "星巴克 38", cwd=tmp)
            memory = run_wrapper("habit", "memory", cwd=tmp)
            self.assertTrue(json.loads(memory.stdout)["written"])

            bill = run_wrapper("bill", "show", "--month", "2026-08", "--json", cwd=tmp)
            self.assertEqual(json.loads(bill.stdout)["month"], "2026-08")

    def test_wrapper_forwards_audit_to_audit_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_wrapper("add", "--text", "地铁 5", cwd=tmp)
            result = run_wrapper("audit", "--json", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["transaction_count"], 1)

    def test_wrapper_forwards_screenshot_without_injecting_ledger(self):
        # bill_screenshot.py has no --ledger flag; injecting one would fail.
        result = run_wrapper("shot", "--help")
        self.assertIn("prepare", result.stdout)

    def test_open_alias_forwards_to_serve_with_open_flag(self):
        result = run_wrapper("open", "--help")
        self.assertIn("serve", result.stdout)
        self.assertIn("--open", result.stdout)

    def test_wrapper_from_scripts_dir_uses_skill_root_data(self):
        result = run_wrapper("which", cwd=str(ROOT / "scripts"))
        ledger_line = next(line for line in result.stdout.splitlines() if line.startswith("ledger:"))
        self.assertIn(str(ROOT / "data" / "ledgers" / "default" / "ledger.json"), ledger_line)
        self.assertNotIn("/scripts/data/", ledger_line)

    def test_wrapper_resolves_ledger_from_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "named-ledger.json")
            env = dict(os.environ, LAZY_LEDGER_FILE=str(target))
            result = subprocess.run(
                [str(WRAPPER), "add", "--text", "午饭 18"],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.exists())

    def test_explicit_ledger_flag_is_not_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp, "first.json")
            second = Path(tmp, "second.json")
            env = dict(os.environ, LAZY_LEDGER_FILE=str(first))
            subprocess.run(
                [str(WRAPPER), "add", "--text", "午饭 18", "--ledger", str(second)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertTrue(second.exists())
            self.assertFalse(first.exists())


class ListTruncationTest(unittest.TestCase):
    """`list`/`recent` must never truncate silently.

    A silent default limit makes "that day has no transactions" look true when
    the rows were simply cut off, which is exactly wrong for a ledger.
    """

    def _seed(self, tmp, count):
        # Explicit fields: parsing "商品0 10" would read the trailing 0 as the amount.
        for i in range(count):
            run_wrapper(
                "add",
                "--amount", str(10 + i),
                "--merchant", f"商户{i}",
                "--date", f"2026-08-{(i % 28) + 1:02d}",
                cwd=tmp,
            )

    def test_json_reports_total_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 25)
            payload = json.loads(run_wrapper("list", "--json", cwd=tmp).stdout)
            self.assertEqual(payload["total"], 25)
            self.assertEqual(payload["count"], 20)  # default limit
            self.assertTrue(payload["truncated"])
            self.assertEqual(payload["limit"], 20)
            self.assertEqual(len(payload["transactions"]), 20)

    def test_json_truncated_is_false_when_limit_covers_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 5)
            payload = json.loads(run_wrapper("list", "--json", cwd=tmp).stdout)
            self.assertEqual(payload["total"], 5)
            self.assertEqual(payload["count"], 5)
            self.assertFalse(payload["truncated"])

    def test_explicit_limit_is_reflected_in_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 25)
            payload = json.loads(run_wrapper("list", "--json", "--limit", "100", cwd=tmp).stdout)
            self.assertEqual(payload["count"], 25)
            self.assertFalse(payload["truncated"])

    def test_period_is_reported_so_callers_know_the_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 3)
            payload = json.loads(run_wrapper("list", "--json", "--start", "2026-08-01", cwd=tmp).stdout)
            self.assertIn("period", payload)

    def test_text_output_warns_when_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 8)
            result = run_wrapper("list", "--limit", "2", cwd=tmp)
            self.assertIn("匹配共 8 笔", result.stdout)

    def test_text_output_stays_quiet_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 3)
            result = run_wrapper("list", "--limit", "50", cwd=tmp)
            self.assertNotIn("匹配共", result.stdout)

    def test_recent_inherits_the_same_reporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp, 10)
            payload = json.loads(run_wrapper("recent", "--json", cwd=tmp).stdout)
            self.assertEqual(payload["total"], 10)
            self.assertEqual(payload["count"], 5)  # recent defaults to 5
            self.assertTrue(payload["truncated"])


class FindWithoutAmountTest(unittest.TestCase):
    """`find --text` is a lookup: the merchant alone must be enough."""

    def _seed(self, tmp):
        run_wrapper("add", "--text", "2026-08-18 星巴克 38", cwd=tmp)
        run_wrapper("add", "--text", "2026-08-19 地铁 5", cwd=tmp)

    def test_find_matches_merchant_without_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            result = run_wrapper("find", "--text", "星巴克", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["proposal"]["amount"])
            merchants = [c["merchant"] for c in payload["candidates"]]
            self.assertEqual(merchants, ["星巴克"])

    def test_find_scores_amount_and_date_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(tmp)
            result = run_wrapper("find", "--text", "2026-08-18 星巴克 38", cwd=tmp)
            payload = json.loads(result.stdout)
            top = payload["candidates"][0]
            self.assertEqual(top["merchant"], "星巴克")
            # amount 2 + date 2 + merchant 2 + category 1
            self.assertEqual(top["score"], 7)

    def test_add_still_requires_an_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_wrapper("add", "--text", "星巴克", cwd=tmp, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Amount is required", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

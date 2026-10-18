"""Regression tests for data-integrity and safety fixes."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ledger"


def run(*args, cwd, check=True):
    result = subprocess.run(
        [str(WRAPPER), *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {result.args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class DataIntegrityTest(unittest.TestCase):
    def test_parallel_adds_do_not_lose_transactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            processes = [
                subprocess.Popen(
                    [
                        str(WRAPPER),
                        "add",
                        "--type",
                        "expense",
                        "--amount",
                        "1",
                        "--category",
                        "其他",
                        "--merchant",
                        f"并行{i}",
                    ],
                    cwd=tmp,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for i in range(12)
            ]
            for process in processes:
                _, stderr = process.communicate(timeout=60)
                self.assertEqual(process.returncode, 0, stderr)
            ledger = json.loads((Path(tmp) / "data" / "ledgers" / "default" / "ledger.json").read_text())
            self.assertEqual(len(ledger["transactions"]), 12)

    def test_backups_keep_multiple_snapshots_from_one_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("backup", cwd=tmp)
            run("backup", cwd=tmp)
            backups = list((Path(tmp) / "data" / "ledgers" / "default" / "backups").glob("*.json"))
            self.assertEqual(len(backups), 2)
            self.assertEqual(len({path.name for path in backups}), 2)

    def test_budget_delete_removes_only_one_unicode_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            first = json.loads(run("budget", "set", "--month", "2026-10", "--category", "餐饮", "--amount", "100", cwd=tmp).stdout)
            second = json.loads(run("budget", "set", "--month", "2026-10", "--category", "交通", "--amount", "50", cwd=tmp).stdout)
            self.assertNotEqual(first["id"], second["id"])
            run("budget", "delete", "--id", first["id"], cwd=tmp)
            payload = json.loads(run("budget", "list", "--month", "2026-10", "--json", cwd=tmp).stdout)
            rows = payload["budgets"]
            self.assertEqual([row["id"] for row in rows], [second["id"]])

    def test_unknown_explicit_account_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            result = run(
                "add",
                "--type",
                "expense",
                "--amount",
                "9",
                "--account",
                "不存在的卡",
                cwd=tmp,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            ledger = json.loads((Path(tmp) / "data" / "ledgers" / "default" / "ledger.json").read_text())
            self.assertEqual(ledger["transactions"], [])

    def test_doctor_survives_a_corrupt_amount(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            path = Path(tmp) / "data" / "ledgers" / "default" / "ledger.json"
            ledger = json.loads(path.read_text())
            ledger["transactions"].append(
                {
                    "id": "bad_amount",
                    "type": "expense",
                    "amount": "abc",
                    "currency": "CNY",
                    "category": "其他",
                    "occurred_at": "2026-10-01T12:00:00+08:00",
                    "source": "manual",
                }
            )
            path.write_text(json.dumps(ledger, ensure_ascii=False))
            result = run("doctor", cwd=tmp, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertIn("invalid_amount", result.stdout)

    def test_excluded_expense_is_not_counted_by_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run(
                "add",
                "--type",
                "expense",
                "--amount",
                "100",
                "--category",
                "餐饮",
                "--merchant",
                "测试",
                "--exclude-from-totals",
                cwd=tmp,
            )
            run("budget", "set", "--month", "2026-10", "--category", "餐饮", "--amount", "50", cwd=tmp)
            payload = json.loads(run("budget", "list", "--month", "2026-10", "--json", cwd=tmp).stdout)
            self.assertEqual(payload["budgets"][0]["spent"], 0.0)
            summary = json.loads(run("summary", "--month", "2026-10", "--json", cwd=tmp).stdout)
            self.assertEqual(summary["totals"]["expense"], 0.0)
            self.assertEqual(summary["excluded_count"], 1)


if __name__ == "__main__":
    unittest.main()

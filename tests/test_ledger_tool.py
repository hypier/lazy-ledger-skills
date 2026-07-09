import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "ledger_tool.py"


def run_tool(*args, check=True):
    result = subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed: {result.args}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class LedgerToolTest(unittest.TestCase):
    def test_parse_text_outputs_proposal_without_writing_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"

            result = run_tool(
                "parse",
                "--ledger",
                str(ledger),
                "--text",
                "2026-07-07 星巴克 38",
            )

            proposal = json.loads(result.stdout)
            self.assertEqual(proposal["amount"], 38.0)
            self.assertEqual(proposal["type"], "expense")
            self.assertEqual(proposal["category"], "咖啡")
            self.assertEqual(proposal["merchant"], "星巴克")
            self.assertEqual(proposal["occurred_at"][:10], "2026-07-07")
            self.assertFalse(ledger.exists())

    def test_parse_keeps_known_coffee_merchant_when_keyword_also_sets_category(self):
        result = run_tool("parse", "--text", "2026-07-07 瑞幸咖啡 19.9")

        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["category"], "咖啡")
        self.assertEqual(proposal["merchant"], "瑞幸")

    def test_add_text_refuses_likely_duplicate_unless_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"

            run_tool("init", "--ledger", str(ledger))
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")

            duplicate = run_tool(
                "add",
                "--ledger",
                str(ledger),
                "--text",
                "2026-07-07 星巴克 38",
                check=False,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("likely_duplicate", duplicate.stderr)
            self.assertEqual(len(json.loads(ledger.read_text())["transactions"]), 1)

            run_tool(
                "add",
                "--ledger",
                str(ledger),
                "--text",
                "2026-07-07 星巴克 38",
                "--allow-duplicate",
            )
            self.assertEqual(len(json.loads(ledger.read_text())["transactions"]), 2)

    def test_add_text_to_new_ledger_defaults_currency_to_cny(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"

            run_tool("add", "--ledger", str(ledger), "--text", "昨天星巴克 38")

            data = json.loads(ledger.read_text())
            self.assertEqual(data["currency"], "CNY")
            self.assertEqual(data["transactions"][0]["currency"], "CNY")

    def test_recent_lists_latest_transactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"

            run_tool("init", "--ledger", str(ledger))
            run_tool("add", "--ledger", str(ledger), "--amount", "12", "--merchant", "旧商户", "--date", "2026-07-01")
            run_tool("add", "--ledger", str(ledger), "--amount", "34", "--merchant", "新商户", "--date", "2026-07-08")

            result = run_tool("recent", "--ledger", str(ledger), "--limit", "1")

            self.assertIn("新商户", result.stdout)
            self.assertNotIn("旧商户", result.stdout)

    def test_import_tsv_adds_multiple_transactions(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            source = Path(tmp) / "rows.tsv"
            source.write_text(
                "\n".join(
                    [
                        "occurred_at\ttype\tamount\tcategory\tmerchant\tnote\tsource\tconfidence",
                        "2026-07-01T10:00:00+08:00\texpense\t12.5\t餐饮\t早餐店\t豆浆油条\timport\t0.9",
                        "2026-07-01T12:00:00+08:00\tincome\t88\t红包\t朋友\t午饭红包\timport\t1.0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = run_tool("import-tsv", "--ledger", str(ledger), "--input", str(source))

            report = json.loads(result.stdout)
            self.assertEqual(report["added"], 2)
            data = json.loads(ledger.read_text())
            self.assertEqual(len(data["transactions"]), 2)
            self.assertEqual(data["currency"], "CNY")
            self.assertEqual({tx["source"] for tx in data["transactions"]}, {"import"})

    def test_doctor_reports_likely_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            ledger.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "currency": "CNY",
                        "created_at": "2026-07-08T12:00:00+08:00",
                        "updated_at": "2026-07-08T12:00:00+08:00",
                        "transactions": [
                            {
                                "id": "tx_1",
                                "type": "expense",
                                "amount": 38,
                                "currency": "CNY",
                                "category": "咖啡",
                                "merchant": "星巴克",
                                "occurred_at": "2026-07-07T12:00:00+08:00",
                                "source": "text",
                                "created_at": "2026-07-08T12:00:00+08:00",
                                "updated_at": "2026-07-08T12:00:00+08:00",
                            },
                            {
                                "id": "tx_2",
                                "type": "expense",
                                "amount": 38,
                                "currency": "CNY",
                                "category": "咖啡",
                                "merchant": "星巴克",
                                "occurred_at": "2026-07-07T18:00:00+08:00",
                                "source": "text",
                                "created_at": "2026-07-08T12:00:00+08:00",
                                "updated_at": "2026-07-08T12:00:00+08:00",
                            },
                        ],
                        "categories": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_tool("doctor", "--ledger", str(ledger), "--json")

            report = json.loads(result.stdout)
            self.assertFalse(report["ok"])
            self.assertIn("likely_duplicate", {issue["code"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
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

    def test_parse_paid_amount_beats_original_price(self):
        result = run_tool("parse", "--text", "2026-07-07 星巴克 原价45 实付38")
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["amount"], 38.0)
        self.assertEqual(proposal["merchant"], "星巴克")

    def test_parse_infers_wechat_method(self):
        result = run_tool("parse", "--text", "2026-07-07 微信 星巴克 38")
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["method"], "wechat")
        self.assertEqual(proposal["merchant"], "星巴克")

    def test_parse_splits_multiple_items(self):
        result = run_tool(
            "parse",
            "--text",
            "2026-07-07 午饭 26\n2026-07-07 晚饭 38",
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["count"], 2)
        amounts = [item["amount"] for item in payload["proposals"]]
        self.assertEqual(amounts, [26.0, 38.0])

    def test_parse_splits_two_amounts_on_one_line(self):
        result = run_tool("parse", "--text", "午饭26 晚饭38")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["amount"] for item in payload["proposals"]], [26.0, 38.0])
        self.assertEqual(payload["proposals"][0]["category"], "餐饮")

    def test_parse_stacked_payment_paste(self):
        paste = "\n".join(
            [
                "星巴克",
                "支出",
                "¥38.00",
                "2026-07-07 12:03",
                "瑞幸咖啡",
                "支出",
                "¥19.90",
                "2026-07-08 09:10",
            ]
        )
        result = run_tool("parse", "--text", paste)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["proposals"][0]["merchant"], "星巴克")
        self.assertEqual(payload["proposals"][0]["amount"], 38.0)
        self.assertEqual(payload["proposals"][1]["merchant"], "瑞幸")

    def test_parse_month_day_token(self):
        result = run_tool("parse", "--text", "7月7日 星巴克 38")
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["occurred_at"][5:10], "07-07")
        self.assertEqual(proposal["amount"], 38.0)

    def test_prefer_overrides_builtin_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            run_tool("init", "--ledger", str(ledger))
            run_tool(
                "prefer",
                "--ledger",
                str(ledger),
                "--merchant",
                "星巴克",
                "--category",
                "餐饮",
            )
            result = run_tool("parse", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            proposal = json.loads(result.stdout)
            self.assertEqual(proposal["category"], "餐饮")

    def test_add_text_returns_month_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            result = run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["added"][0]["merchant"], "星巴克")
            self.assertEqual(payload["month"]["totals"]["expense"], 38.0)

    def test_update_category_is_remembered_for_later_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            added = json.loads(
                run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38").stdout
            )["added"][0]
            run_tool(
                "update",
                "--ledger",
                str(ledger),
                "--id",
                added["id"],
                "--category",
                "餐饮",
            )
            proposal = json.loads(
                run_tool("parse", "--ledger", str(ledger), "--text", "2026-07-08 星巴克 19").stdout
            )
            self.assertEqual(proposal["category"], "餐饮")

    def test_show_markdown_includes_category_and_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            run_tool("add", "--ledger", str(ledger), "--text", "2026-06-07 午饭 20")
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            result = run_tool("show", "--ledger", str(ledger), "--month", "2026-07", "--compare")
            self.assertIn("## 2026-07", result.stdout)
            self.assertIn("咖啡", result.stdout)
            self.assertIn("较2026-06", result.stdout)

    def test_export_csv_writes_header_and_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            output = Path(tmp) / "out.csv"
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            run_tool(
                "export",
                "--ledger",
                str(ledger),
                "--format",
                "csv",
                "--output",
                str(output),
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn("merchant", text)
            self.assertIn("星巴克", text)

    def test_render_dashboard_contains_calendar_and_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            output = Path(tmp) / "report.html"
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            run_tool("render", "--ledger", str(ledger), "--output", str(output))
            html = output.read_text(encoding="utf-8")
            self.assertIn("支出月历", html)
            self.assertIn("待复核", html)
            self.assertIn("星巴克", html)
            self.assertNotIn("__LEDGER_DATA__", html)

    def test_add_assigns_default_and_named_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            added = json.loads(
                run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38").stdout
            )["added"][0]
            self.assertEqual(added["account"], "微信零钱")
            self.assertEqual(added["account_id"], "acc_wechat")
            cash = json.loads(
                run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 现金 午饭 20").stdout
            )["added"][0]
            self.assertEqual(cash["account"], "现金")
            self.assertEqual(cash["account_id"], "acc_cash")

    def test_parse_transfer_sets_source_and_destination(self):
        result = run_tool("parse", "--text", "从微信转到支付宝 500")
        proposal = json.loads(result.stdout)
        self.assertEqual(proposal["type"], "transfer")
        self.assertEqual(proposal["account"], "微信零钱")
        self.assertEqual(proposal["to_account"], "支付宝")
        self.assertEqual(proposal["amount"], 500.0)

    def test_account_balance_decreases_after_expense(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 微信 星巴克 38")
            result = run_tool("account", "list", "--ledger", str(ledger), "--json")
            payload = json.loads(result.stdout)
            wechat = next(item for item in payload["accounts"] if item["id"] == "acc_wechat")
            self.assertEqual(wechat["balance"], -38.0)

    def test_budget_progress_shows_remaining_in_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            run_tool("budget", "set", "--ledger", str(ledger), "--month", "2026-07", "--category", "咖啡", "--amount", "100")
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            result = run_tool("show", "--ledger", str(ledger), "--month", "2026-07")
            self.assertIn("### 预算", result.stdout)
            self.assertIn("咖啡", result.stdout)
            self.assertIn("剩余", result.stdout)

    def test_render_includes_accounts_and_budgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            output = Path(tmp) / "report.html"
            run_tool("budget", "set", "--ledger", str(ledger), "--amount", "8000")
            run_tool("add", "--ledger", str(ledger), "--text", "2026-07-07 星巴克 38")
            run_tool("render", "--ledger", str(ledger), "--output", str(output))
            html = output.read_text(encoding="utf-8")
            self.assertIn("账户余额", html)
            self.assertIn("预算", html)
            self.assertIn("微信零钱", html)

    def test_init_marks_json_as_document_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            run_tool("init", "--ledger", str(ledger))
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["store"], "lazy-ledger-docs")
            self.assertEqual(data["store_version"], 1)
            self.assertIn("transactions", data)
            self.assertIn("accounts", data)


def _scripts_on_path():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return scripts


class DocumentStoreTest(unittest.TestCase):
    def test_insert_update_remove_persist(self):
        _scripts_on_path()
        from ledger_db import STORE_KIND, DocumentNotFound, DocumentStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            store = DocumentStore(path)
            store.collection("transactions").insert({"id": "tx_1", "type": "expense", "amount": 12.0})
            self.assertEqual(store.collection("transactions").get("tx_1")["amount"], 12.0)
            store.collection("transactions").update("tx_1", {"amount": 15.0})
            again = DocumentStore(path)
            self.assertEqual(again.data["store"], STORE_KIND)
            self.assertEqual(again.collection("transactions").get("tx_1")["amount"], 15.0)
            again.collection("transactions").remove("tx_1")
            with self.assertRaises(DocumentNotFound):
                DocumentStore(path).collection("transactions").get("tx_1")


class LiveServerTest(unittest.TestCase):
    def test_serve_rejects_non_localhost(self):
        _scripts_on_path()
        from ledger_server import run_server

        with self.assertRaises(SystemExit):
            run_server("/tmp/unused-ledger.json", host="0.0.0.0", port=8765, block=False)

    def test_live_server_can_add_patch_and_delete(self):
        _scripts_on_path()
        from ledger_server import run_server

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.json"
            server = run_server(ledger, port=18765, block=False)
            try:
                port = server.server_address[1]
                base = f"http://127.0.0.1:{port}"
                deadline = time.time() + 5
                last_error = None
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(base + "/api/health", timeout=1) as resp:
                            health = json.loads(resp.read().decode("utf-8"))
                        break
                    except (urllib.error.URLError, ConnectionRefusedError, OSError) as exc:
                        last_error = exc
                        time.sleep(0.05)
                else:
                    raise AssertionError(f"server did not start: {last_error}")
                self.assertTrue(health["ok"])

                with urllib.request.urlopen(base + "/", timeout=2) as resp:
                    html = resp.read().decode("utf-8")
                self.assertIn("懒人记账", html)

                add_req = urllib.request.Request(
                    base + "/api/transactions",
                    data=json.dumps({"text": "2026-07-07 星巴克 38"}).encode("utf-8"),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(add_req, timeout=5) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(payload["count"], 1)
                tx_id = payload["added"][0]["id"]

                with urllib.request.urlopen(base + "/api/ledger", timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(len(data["transactions"]), 1)
                self.assertEqual(data["store"], "lazy-ledger-docs")

                patch_req = urllib.request.Request(
                    base + f"/api/transactions/{tx_id}",
                    data=json.dumps({"amount": 40}).encode("utf-8"),
                    method="PATCH",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(patch_req, timeout=5) as resp:
                    updated = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(updated["amount"], 40.0)

                delete_req = urllib.request.Request(
                    base + f"/api/transactions/{tx_id}",
                    method="DELETE",
                )
                urllib.request.urlopen(delete_req, timeout=5).close()
                with urllib.request.urlopen(base + "/api/ledger", timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(data["transactions"], [])
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()

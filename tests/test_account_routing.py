"""Confirmed payment facts never change because another account is richer."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WRAPPER = SCRIPTS / "ledger"

sys.path.insert(0, str(SCRIPTS))
import ledger_tool  # noqa: E402


def run(*args, cwd, check=True):
    result = subprocess.run([str(WRAPPER), *args], cwd=str(cwd), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise AssertionError(f"{result.args}\n{result.stdout}\n{result.stderr}")
    return result


class LargeExpenseAccountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        self.ledger = Path(self.cwd) / "data" / "ledgers" / "default" / "ledger.json"
        run("init", cwd=self.cwd)
        run("account", "update", "--id", "acc_wechat", "--opening", "2000", cwd=self.cwd)
        run("account", "update", "--id", "acc_bank", "--opening", "20000", cwd=self.cwd)
        # A default requires an explicit user setting.
        run("account", "update", "--id", "acc_wechat", "--default", cwd=self.cwd)
        run("record", "--text", "星巴克38", cwd=self.cwd)

    def tearDown(self):
        self.tmp.cleanup()

    def parse(self, text):
        return json.loads(run("parse", "--text", text, cwd=self.cwd).stdout)

    def test_small_purchase_keeps_the_confirmed_account(self):
        payload = self.parse("美团外卖45")
        self.assertEqual(payload["account"], "微信零钱")
        self.assertIn("已确认", payload["account_reason"])

    def test_large_purchase_keeps_confirmed_account(self):
        payload = self.parse("京东买了台电脑8000")
        self.assertEqual(payload["account"], "微信零钱")
        # The payment method has to follow, or the row says a bank card paid by WeChat.
        self.assertEqual(payload["method"], "wechat")
        self.assertIn("已确认", payload["account_reason"])

    def test_amount_never_changes_account(self):
        # Both amounts use the user-confirmed account.
        self.assertEqual(self.parse("京东买了台显示器3000")["account"], "微信零钱")
        self.assertEqual(self.parse("超市囤货1500")["account"], "微信零钱")

    def test_reply_does_not_invent_a_different_payment_account(self):
        out = run("record", "--text", "京东买了台电脑8000", cwd=self.cwd).stdout
        self.assertNotIn("银行卡", out)
        self.assertIn("微信零钱", out)
        self.assertNotIn("余额不够", out)

    def test_negative_book_balance_is_not_hidden_by_reassignment(self):
        run("record", "--text", "京东买了台电脑8000", cwd=self.cwd)
        summary = json.loads(run("summary", "--range", "this-month", "--json", cwd=self.cwd).stdout)
        wechat = next(a for a in summary["accounts"] if a["name"] == "微信零钱")
        self.assertEqual(wechat["balance"], -6038)

    def test_explicit_account_is_never_overridden(self):
        # The user said WeChat paid; even if that overdraws it, that is their
        # statement of fact and the brief's negative-balance detector will say so.
        payload = self.parse("微信买了台电脑8000")
        self.assertEqual(payload["account"], "微信零钱")
        self.assertIsNone(payload.get("account_reason"))

    def test_advance_is_not_rerouted(self):
        # An advance is money that comes back; moving it silently would hide the
        # fact that the wallet was used.
        payload = self.parse("给公司垫付差旅3000")
        self.assertEqual(payload["account"], "微信零钱")


class AffordabilityHelperTest(unittest.TestCase):
    def test_picks_the_richest_account(self):
        ledger = ledger_tool.empty_ledger()
        for account in ledger["accounts"]:
            if account["id"] == "acc_wechat":
                account["opening_balance"] = 500
            if account["id"] == "acc_bank":
                account["opening_balance"] = 9000
            if account["id"] == "acc_alipay":
                account["opening_balance"] = 3000
        best = ledger_tool.affordable_account(ledger, 4000)
        self.assertEqual(best["name"], "银行卡")

    def test_returns_none_when_nothing_can_cover_it(self):
        ledger = ledger_tool.empty_ledger()
        for account in ledger["accounts"]:
            account["opening_balance"] = 10
        self.assertIsNone(ledger_tool.affordable_account(ledger, 4000))


if __name__ == "__main__":
    unittest.main()

"""Tests for economic-event semantics: type/category consistency and refund links.

These cover the design fixes for:
  - reimbursement language recorded as income instead of inflating expenses
  - advance/loan language flagged so it is not treated as ordinary spending
  - the type/category consistency invariant reported by doctor
  - own-account transfer detection, including an amount between the accounts
  - confident refund matches persisted as related_transaction_id
"""

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


def ledger_path(cwd):
    return Path(cwd) / "data" / "ledgers" / "default" / "ledger.json"


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


class EventClassificationTest(unittest.TestCase):
    """Reimbursement arriving is income, not an expense."""

    def test_reimbursement_phrases_are_income(self):
        for text in ("报销回来300", "报销了500", "报销打款200", "报销到账150"):
            with self.subTest(text=text):
                tx_type, category, _flags, _confidence = ledger_tool.classify_event(text)
                self.assertEqual(tx_type, "income", text)
                self.assertEqual(category, "收入", text)

    def test_reimbursement_does_not_inflate_expenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "报销回来300", cwd=tmp)
            ledger = json.loads(ledger_path(tmp).read_text())
            tx = ledger["transactions"][0]
            self.assertEqual(tx["type"], "income")
            self.assertEqual(tx["category"], "收入")

            summary = run("summary", "--range", "this-month", "--json", cwd=tmp)
            totals = json.loads(summary.stdout)["totals"]
            self.assertEqual(totals["expense"], 0.0)
            self.assertEqual(totals["income"], 300.0)

    def test_advance_language_is_not_income_and_is_flagged(self):
        for text in ("垫付800", "借给小王200", "代付500"):
            with self.subTest(text=text):
                tx_type, _category, flags, _confidence = ledger_tool.classify_event(text)
                # Cash leaves the account, but the money is owed back.
                self.assertEqual(tx_type, "expense", text)
                self.assertTrue(flags.get("advance"), text)

    def test_red_packet_direction_depends_on_the_verb(self):
        # Receiving red packets was previously always recorded as an expense.
        for text in ("收到红包200", "收红包88", "抢到红包50"):
            with self.subTest(text=text):
                tx_type, category, _flags, _confidence = ledger_tool.classify_event(text)
                self.assertEqual(tx_type, "income", text)
                self.assertEqual(category, "红包", text)
        for text in ("发红包200", "给妈妈发红包500"):
            with self.subTest(text=text):
                tx_type, category, _flags, _confidence = ledger_tool.classify_event(text)
                self.assertEqual(tx_type, "expense", text)
                self.assertEqual(category, "红包", text)


class RepaymentDetectionTest(unittest.TestCase):
    def test_credit_repayment_phrases_are_transfers(self):
        for text in ("还花呗", "还白条", "信用卡还款", "还信用卡2000"):
            with self.subTest(text=text):
                self.assertEqual(ledger_tool.infer_type(text), "transfer", text)


class TransferDetectionTest(unittest.TestCase):
    def test_common_transfer_phrases(self):
        for text in ("还信用卡2000", "微信提现500", "从微信转100到支付宝", "从银行卡转500到微信"):
            with self.subTest(text=text):
                self.assertEqual(ledger_tool.infer_type(text), "transfer", text)

    def test_route_tolerates_amount_between_accounts(self):
        self.assertEqual(
            ledger_tool.infer_transfer_route("从微信转100到支付宝"),
            ("微信", "支付宝"),
        )

    def test_paying_a_person_is_not_an_own_account_transfer(self):
        # Money to a person is spending, not movement between the user's accounts.
        for text in ("转账给妈妈500", "微信转给小王100"):
            with self.subTest(text=text):
                self.assertEqual(ledger_tool.infer_type(text), "expense", text)

    def test_withdrawal_resolves_both_accounts(self):
        self.assertEqual(ledger_tool.infer_transfer_route("微信提现500"), ("微信", "银行卡"))
        self.assertEqual(ledger_tool.infer_transfer_route("支付宝提现1000"), ("支付宝", "银行卡"))
        # An explicit destination must not be mistaken for the source.
        self.assertEqual(ledger_tool.infer_transfer_route("微信提现到银行卡"), ("微信", "银行卡"))
        self.assertEqual(ledger_tool.infer_transfer_route("提现到银行卡500"), (None, "银行卡"))

    def test_withdrawal_records_as_transfer(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "微信提现500", cwd=tmp)
            ledger = json.loads(ledger_path(tmp).read_text())
            tx = ledger["transactions"][0]
            self.assertEqual(tx["type"], "transfer")
            self.assertEqual(tx["account"], "微信零钱")
            self.assertEqual(tx["to_account"], "银行卡")

            summary = run("summary", "--range", "this-month", "--json", cwd=tmp)
            totals = json.loads(summary.stdout)["totals"]
            # A transfer is not spending.
            self.assertEqual(totals["expense"], 0.0)


class CategoryTypeConsistencyTest(unittest.TestCase):
    def test_doctor_flags_income_category_on_expense(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            path = ledger_path(tmp)
            ledger = json.loads(path.read_text())
            ledger["transactions"].append(
                {
                    "id": "tx_bad_1",
                    "type": "expense",
                    "amount": 50,
                    "currency": "CNY",
                    "category": "收入",
                    "occurred_at": "2026-10-01T12:00:00+08:00",
                    "source": "text",
                    "created_at": "2026-10-01T12:00:00+08:00",
                    "updated_at": "2026-10-01T12:00:00+08:00",
                }
            )
            path.write_text(json.dumps(ledger, ensure_ascii=False))

            result = run("doctor", "--json", cwd=tmp, check=False)
            report = json.loads(result.stdout)
            self.assertFalse(report["ok"])
            codes = [issue["code"] for issue in report["issues"]]
            self.assertIn("category_type_mismatch", codes)

    def test_clean_ledger_stays_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "昨天微信星巴克38", cwd=tmp)
            result = run("doctor", "--json", cwd=tmp)
            report = json.loads(result.stdout)
            self.assertTrue(report["ok"], report)


class RefundLinkTest(unittest.TestCase):
    def test_confident_refund_is_linked_to_its_expense(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "昨天微信星巴克38", cwd=tmp)
            result = run("add", "--text", "今天星巴克退款38", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertTrue(payload.get("refund_links"), payload)

            ledger = json.loads(ledger_path(tmp).read_text())
            expense = next(tx for tx in ledger["transactions"] if tx["type"] == "expense")
            refund = next(tx for tx in ledger["transactions"] if tx["type"] == "refund")
            self.assertEqual(refund["related_transaction_id"], expense["id"])
            self.assertEqual(refund["relation"], "refund")

    def test_linked_refund_is_no_longer_reported_unmatched(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "昨天微信星巴克38", cwd=tmp)
            run("add", "--text", "今天星巴克退款38", cwd=tmp)
            result = run("show", cwd=tmp)
            self.assertNotIn("未对上的退款", result.stdout)

    def test_orphan_refund_is_still_surfaced(self):
        # With no matching expense there is nothing to link, so it must be reported.
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "今天星巴克退款38", cwd=tmp)
            result = run("show", cwd=tmp)
            self.assertIn("未对上的退款", result.stdout)

    def test_ambiguous_refunds_are_not_linked(self):
        # Two identical candidate expenses: the match is not deterministic, so
        # the relation must be left for the user rather than guessed.
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "前天微信星巴克38", cwd=tmp)
            run("add", "--text", "昨天微信星巴克38", "--allow-duplicate", cwd=tmp)
            result = run("add", "--text", "今天星巴克退款38", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertFalse(payload.get("refund_links"))

            ledger = json.loads(ledger_path(tmp).read_text())
            refund = next(tx for tx in ledger["transactions"] if tx["type"] == "refund")
            self.assertIsNone(refund.get("related_transaction_id"))


if __name__ == "__main__":
    unittest.main()


class AdvanceStateTest(unittest.TestCase):
    """An advance is money owed back, so it must not inflate spending."""

    def _add(self, tmp, *args):
        result = run("add", *args, cwd=tmp)
        return json.loads(result.stdout)["added"][0]

    def test_advance_marked_open_and_excluded_from_expense(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            self._add(tmp, "--text", "垫付800")
            self._add(tmp, "--text", "昨天星巴克38")
            summary = json.loads(run("summary", "--range", "this-month", "--json", cwd=tmp).stdout)
            # Only the coffee is real spending; the 800 is recoverable.
            self.assertEqual(summary["totals"]["expense"], 38.0)

    def test_explicit_advance_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            tx = self._add(tmp, "--text", "给公司买显示器1200", "--advance")
            self.assertEqual(tx.get("advance"), "open")

    def test_advance_settles_when_reimbursement_arrives(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            advance = self._add(tmp, "--text", "垫付800")
            result = run("add", "--text", "报销回来800", cwd=tmp)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["advance_settlements"]), 1)

            ledger = json.loads(ledger_path(tmp).read_text())
            by_id = {tx["id"]: tx for tx in ledger["transactions"]}
            self.assertEqual(by_id[advance["id"]]["advance"], "settled")
            income = next(tx for tx in ledger["transactions"] if tx["type"] == "income")
            self.assertEqual(income["related_transaction_id"], advance["id"])
            self.assertEqual(income["relation"], "reimbursement")

            # Reimbursed money in and out nets to zero.
            summary = json.loads(run("summary", "--range", "this-month", "--json", cwd=tmp).stdout)
            self.assertEqual(summary["totals"]["net"], 0.0)

    def test_ambiguous_reimbursement_does_not_settle(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            self._add(tmp, "--text", "垫付800")
            # Two possible advances of the same size: refuse to guess.
            self._add(tmp, "--text", "给部门垫付800", "--allow-duplicate")
            payload = json.loads(run("add", "--text", "报销回来800", cwd=tmp).stdout)
            self.assertNotIn("advance_settlements", payload)
            open_rows = json.loads(run("advance", "list", "--json", cwd=tmp).stdout)
            self.assertEqual(open_rows["count"], 2)

    def test_writeoff_turns_advance_into_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            advance = self._add(tmp, "--text", "借给小王200")
            run("advance", "writeoff", "--id", advance["id"], cwd=tmp)
            summary = json.loads(run("summary", "--range", "this-month", "--json", cwd=tmp).stdout)
            self.assertEqual(summary["totals"]["expense"], 200.0)
            self.assertEqual(json.loads(run("advance", "list", "--json", cwd=tmp).stdout)["count"], 0)

    def test_advance_list_hides_settled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            advance = self._add(tmp, "--text", "垫付500")
            income = self._add(tmp, "--amount", "500", "--type", "income")
            run("advance", "settle", "--id", advance["id"], "--by", income["id"], cwd=tmp)
            self.assertEqual(json.loads(run("advance", "list", "--json", cwd=tmp).stdout)["count"], 0)
            all_rows = json.loads(run("advance", "list", "--all", "--json", cwd=tmp).stdout)
            self.assertEqual(all_rows["count"], 1)


class NotMineAliasTest(unittest.TestCase):
    """`--not-mine` is the plain-language form of a totals exclusion."""

    def test_not_mine_excludes_from_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            payload = json.loads(run("add", "--text", "同事聚餐 代付600", "--not-mine", cwd=tmp).stdout)
            self.assertTrue(payload["added"][0].get("excluded_from_totals"))
            summary = json.loads(run("summary", "--range", "this-month", "--json", cwd=tmp).stdout)
            self.assertEqual(summary["totals"]["expense"], 0.0)


class SemanticsSeparationTest(unittest.TestCase):
    """`not-mine` and `advance` are different answers and must not merge."""

    def test_not_mine_is_not_an_open_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            tx = json.loads(run("add", "--text", "聚餐代付600", "--not-mine", cwd=tmp).stdout)["added"][0]
            self.assertIsNone(tx.get("advance"))
            self.assertTrue(tx.get("excluded_from_totals"))
            self.assertEqual(json.loads(run("advance", "list", "--json", cwd=tmp).stdout)["count"], 0)

    def test_single_character_fragment_is_not_a_merchant(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            tx = json.loads(run("add", "--text", "给公司垫付差旅1500", cwd=tmp).stdout)["added"][0]
            self.assertNotEqual(tx.get("merchant"), "给")
            self.assertEqual(tx.get("advance"), "open")


class ReviewQueueTest(unittest.TestCase):
    """The review queue persists so a review session can span calls."""

    def test_queue_persists_and_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            tx = json.loads(run("add", "--text", "星巴克 咖啡38", "--confidence", "0.6", cwd=tmp).stdout)["added"][0]
            refreshed = json.loads(run("review", "refresh", "--json", cwd=tmp).stdout)
            self.assertEqual(refreshed["added"], 0)  # detected and persisted by the write
            self.assertEqual(refreshed["open_count"], 1)

            # A fresh command still sees the queued item, proving persistence.
            nxt = json.loads(run("review", "next", "--json", cwd=tmp).stdout)
            self.assertEqual(nxt["item"]["transaction_id"], tx["id"])

            run("review", "resolve", "--id", tx["id"], "--code", "low_confidence", cwd=tmp)
            self.assertIsNone(json.loads(run("review", "next", "--json", cwd=tmp).stdout)["item"])

            # Re-running the scan must not resurrect a resolved item.
            again = json.loads(run("review", "refresh", "--json", cwd=tmp).stdout)
            self.assertEqual(again["added"], 0)
            self.assertEqual(again["open_count"], 0)


class LedgerCompatTest(unittest.TestCase):
    """Older ledgers gain the new collections without losing data."""

    def test_normalize_adds_review_queue(self):
        ledger = ledger_tool.empty_ledger()
        ledger.pop("review_queue", None)
        normalized = ledger_tool.normalize_ledger(ledger)
        self.assertEqual(normalized["review_queue"], [])


class ReimbursementTenseTest(unittest.TestCase):
    """"公司报销" means money is coming; "打过来了" means it arrived.

    Treating the bare word 报销 as arrival booked a taxi fare the user had just
    paid as income, moving the period total in the opposite direction. Wrong
    direction is worse than a wrong label: it silently understates spending.
    """

    def test_expectation_is_an_advance_not_income(self):
        for text in ("打车26，公司报销", "公司报销的车费128", "这个月要报销400"):
            with self.subTest(text=text):
                tx_type, _category, flags, _confidence = ledger_tool.classify_event(text)
                self.assertEqual(tx_type, "expense", text)
                self.assertTrue(flags.get("advance"), text)

    def test_arrival_language_is_income(self):
        for text in ("公司把26打过来了", "报销回来26", "报销到账300", "公司打给我26", "报销了500"):
            with self.subTest(text=text):
                tx_type, category, _flags, _confidence = ledger_tool.classify_event(text)
                self.assertEqual(tx_type, "income", text)
                self.assertEqual(category, "收入", text)

    def test_advance_then_reimbursement_nets_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("add", "--text", "打车26，公司报销", cwd=tmp)
            run("add", "--text", "报销回来26", cwd=tmp)

            ledger = json.loads(ledger_path(tmp).read_text())
            advance = next(tx for tx in ledger["transactions"] if tx["type"] == "expense")
            self.assertEqual(advance.get("advance"), "settled")

            totals = json.loads(run("summary", "--range", "this-month", "--json", cwd=tmp).stdout)["totals"]
            self.assertEqual(totals["expense"], 0.0)
            self.assertEqual(totals["income"], 0.0)
            self.assertEqual(totals["net"], 0.0)


class RecordCommandTest(unittest.TestCase):
    """``record`` writes like ``add`` and answers in words, not JSON."""

    def test_reply_states_what_was_written_and_why(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            result = run("record", "--text", "打车26，公司报销", cwd=tmp)
            self.assertIn("记好了", result.stdout)
            self.assertIn("垫付", result.stdout)
            self.assertIn("26", result.stdout)
            # The reason must quote the word that fired, not the rule name.
            self.assertIn("报销", result.stdout)
            self.assertNotIn("REIMBURSE", result.stdout)

    def test_reply_reports_the_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            run("record", "--text", "打车26，公司报销", cwd=tmp)
            result = run("record", "--text", "报销回来26", cwd=tmp)
            self.assertIn("核销", result.stdout)

    def test_record_and_add_agree_on_the_ledger(self):
        """Same write path means the two can never drift apart."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            run("init", cwd=tmp_a)
            run("init", cwd=tmp_b)
            run("record", "--text", "星巴克38", cwd=tmp_a)
            run("add", "--text", "星巴克38", cwd=tmp_b)

            def shape(path):
                tx = json.loads(path.read_text())["transactions"][0]
                return {key: tx.get(key) for key in ("type", "amount", "category", "merchant", "account")}

            self.assertEqual(shape(ledger_path(tmp_a)), shape(ledger_path(tmp_b)))

    def test_json_flag_returns_the_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            run("init", cwd=tmp)
            payload = json.loads(run("record", "--text", "星巴克38", "--json", cwd=tmp).stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["added"][0]["nature"], "spending")
            self.assertEqual(payload["added"][0]["settlement"], "none")
            self.assertIn("after", payload)

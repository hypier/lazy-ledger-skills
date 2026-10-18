"""Tests for the detectors that look for mistakes rather than trends.

Three of these are special because they notice *absence* or *contradiction*:
a payment that looks entered twice, a bill that used to arrive and stopped, and
one merchant filed under two categories. Trends answer "how much"; these answer
"is the ledger telling the truth".
"""

import datetime
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ledger_insight  # noqa: E402

TODAY = datetime.date(2026, 10, 18)


def expense(day, amount, merchant=None, category="外食", **extra):
    row = {
        "id": f"tx_{day}_{amount}",
        "type": "expense",
        "amount": amount,
        "occurred_at": f"{day}T12:00:00+08:00",
        "category": category,
        "merchant": merchant,
    }
    row.update(extra)
    return row


class DuplicateSuspectedTest(unittest.TestCase):
    def test_same_amount_same_day_is_flagged(self):
        rows = [expense("2026-10-10", 48, "美团"), expense("2026-10-10", 48, "美团")]
        findings = ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY")
        self.assertEqual(len(findings), 1)
        # A possible double entry changes the total, so it is not a "maybe".
        self.assertEqual(findings[0]["severity"], "alert")
        self.assertIn("¥48.00", findings[0]["message"])

    def test_different_amounts_are_two_purchases(self):
        rows = [expense("2026-10-10", 48, "美团"), expense("2026-10-10", 52, "美团")]
        self.assertEqual(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY"), [])

    def test_far_apart_days_are_two_purchases(self):
        rows = [expense("2026-10-01", 48, "美团"), expense("2026-10-12", 48, "美团")]
        self.assertEqual(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY"), [])

    def test_different_subjects_are_not_confused(self):
        rows = [expense("2026-10-10", 48, "美团"), expense("2026-10-10", 48, "滴滴")]
        self.assertEqual(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY"), [])

    def test_one_off_large_purchase_is_not_a_duplicate_when_only_once(self):
        rows = [expense("2026-10-10", 8000, "京东", category="购物")]
        self.assertEqual(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY"), [])


class DuplicateHabitTest(unittest.TestCase):
    """A daily habit costs the same amount and must not look like a double entry."""

    def test_routine_same_price_meals_are_not_duplicates(self):
        # 48 yuan takeaway every other day: every pair is equal and within 48h.
        rows = [expense(f"2026-10-{day:02d}", 48, "美团") for day in (2, 4, 6, 7, 9, 11, 12, 14)]
        self.assertEqual(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY"), [])

    def test_a_one_off_charged_twice_is_still_flagged(self):
        rows = [expense("2026-10-10", 8000, "京东", category="购物")] * 2
        rows = [dict(row, id=f"{row['id']}_{index}") for index, row in enumerate(rows)]
        findings = ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY")
        self.assertEqual(len(findings), 1)

    def test_pairs_appear_for_a_merchant_seen_only_twice(self):
        # Two equal payments from a merchant with no habit: that is the shape of
        # a real double entry, so it is reported.
        rows = [expense("2026-10-10", 38, "星巴克", "咖啡茶饮")] * 2
        rows = [dict(row, id=f"{row['id']}_{index}") for index, row in enumerate(rows)]
        self.assertEqual(len(ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY")), 1)

    def test_habit_merchant_stays_quiet_while_a_new_one_is_flagged(self):
        # 星巴克 is a four-times-a-month habit at 38; 京东 is new and appears twice.
        rows = [expense(f"2026-10-{day:02d}", 38, "星巴克", "咖啡茶饮") for day in (1, 3, 5, 8)]
        rows += [expense("2026-10-10", 8000, "京东", "购物"), expense("2026-10-10", 8000, "京东", "购物")]
        rows = [dict(row, id=f"{row['id']}_{index}") for index, row in enumerate(rows)]
        findings = ledger_insight.find_duplicate_suspected(rows, TODAY, "CNY")
        self.assertEqual(len(findings), 1)
        self.assertIn("京东", findings[0]["message"])


class MissingRecurringTest(unittest.TestCase):
    def test_stopped_monthly_bill_is_reported(self):
        rows = [
            expense("2026-06-15", 25, "网易云音乐", "订阅"),
            expense("2026-07-15", 25, "网易云音乐", "订阅"),
            expense("2026-08-15", 25, "网易云音乐", "订阅"),
        ]
        findings = ledger_insight.find_missing_recurring(rows, TODAY, "CNY")
        self.assertEqual(len(findings), 1)
        self.assertIn("网易云音乐", findings[0]["message"])
        self.assertIn("2026-08", findings[0]["message"])

    def test_bill_that_still_arrives_is_not_reported(self):
        rows = [
            expense("2026-07-15", 25, "网易云音乐", "订阅"),
            expense("2026-08-15", 25, "网易云音乐", "订阅"),
            expense("2026-09-15", 25, "网易云音乐", "订阅"),
            expense("2026-10-15", 25, "网易云音乐", "订阅"),
        ]
        self.assertEqual(ledger_insight.find_missing_recurring(rows, TODAY, "CNY"), [])

    def test_sporadic_spending_is_not_a_stopped_bill(self):
        rows = [
            expense("2026-03-15", 25, "网易云音乐", "订阅"),
            expense("2026-06-15", 25, "网易云音乐", "订阅"),
            expense("2026-08-15", 25, "网易云音乐", "订阅"),
        ]
        self.assertEqual(ledger_insight.find_missing_recurring(rows, TODAY, "CNY"), [])

    def test_one_silent_month_is_not_yet_a_pattern(self):
        rows = [
            expense("2026-07-15", 25, "网易云音乐", "订阅"),
            expense("2026-08-15", 25, "网易云音乐", "订阅"),
            expense("2026-09-15", 25, "网易云音乐", "订阅"),
        ]
        # Last month only, so a latency in posting is still plausible.
        self.assertEqual(ledger_insight.find_missing_recurring(rows, TODAY, "CNY"), [])

    def test_advances_do_not_count_as_recurring_bills(self):
        rows = [
            expense("2026-06-15", 25, "网易云音乐", "订阅", advance="open"),
            expense("2026-07-15", 25, "网易云音乐", "订阅", advance="open"),
            expense("2026-08-15", 25, "网易云音乐", "订阅", advance="open"),
        ]
        self.assertEqual(ledger_insight.find_missing_recurring(rows, TODAY, "CNY"), [])


class CategoryDriftTest(unittest.TestCase):
    def test_merchant_filed_two_ways_across_months(self):
        rows = [
            expense("2026-08-12", 40, "星巴克", "餐饮"),
            expense("2026-09-11", 38, "星巴克", "餐饮"),
            expense("2026-10-12", 42, "星巴克", "购物"),
        ]
        findings = ledger_insight.find_category_drift(rows, TODAY, "CNY")
        self.assertEqual(len(findings), 1)
        self.assertIn("餐饮", findings[0]["message"])
        self.assertIn("购物", findings[0]["message"])

    def test_retagging_within_one_month_is_not_drift(self):
        rows = [
            expense("2026-10-11", 38, "星巴克", "餐饮"),
            expense("2026-10-12", 42, "星巴克", "购物"),
        ]
        self.assertEqual(ledger_insight.find_category_drift(rows, TODAY, "CNY"), [])

    def test_consistent_merchant_is_quiet(self):
        rows = [
            expense("2026-09-11", 38, "星巴克", "餐饮"),
            expense("2026-10-12", 42, "星巴克", "餐饮"),
        ]
        self.assertEqual(ledger_insight.find_category_drift(rows, TODAY, "CNY"), [])

    def test_untracked_advance_does_not_create_drift(self):
        rows = [
            expense("2026-09-11", 38, "星巴克", "餐饮"),
            expense("2026-10-12", 42, "星巴克", "购物", advance="open"),
        ]
        self.assertEqual(ledger_insight.find_category_drift(rows, TODAY, "CNY"), [])


class DetectorWiringTest(unittest.TestCase):
    def test_all_detectors_run_without_a_ledger_collection(self):
        ledger = {"transactions": [expense("2026-10-10", 48, "美团")]}
        findings = ledger_insight.build_findings(ledger, today=TODAY, currency="CNY")
        self.assertIsInstance(findings, list)
        # Sorted most severe first, so the brief can take a prefix.
        severities = [ledger_insight.SEVERITY.get(item["severity"], 9) for item in findings]
        self.assertEqual(severities, sorted(severities))


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "scripts" / "ledger_audit.py"


def run_audit(transactions):
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger.json"
        ledger.write_text(json.dumps({"transactions": transactions}, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(AUDIT), "--ledger", str(ledger), "--json"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)


def transaction(tx_id, tx_type="expense", **fields):
    row = {
        "id": tx_id,
        "type": tx_type,
        "amount": 38.0,
        "merchant": "same merchant",
        "occurred_at": "2026-08-01T12:00:00+08:00",
    }
    row.update(fields)
    return row


class LedgerAuditTest(unittest.TestCase):
    def test_distinct_credit_references_are_source_confirmed_rows(self):
        report = run_audit(
            [
                transaction("tx_1", credit_statement_ref="credit:1"),
                transaction("tx_2", credit_statement_ref="credit:2"),
            ]
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["cross_source_candidate_count"], 0)
        self.assertEqual(len(report["source_confirmed_same_value_groups"]), 1)

    def test_disjoint_source_rows_are_cross_source_candidates(self):
        report = run_audit(
            [
                transaction("tx_wechat", wechat_transaction_id="wx:1"),
                transaction("tx_bank", bank_statement_ref="bank:1"),
            ]
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["cross_source_candidate_count"], 1)

    def test_repeated_authoritative_reference_is_confirmed_duplicate_evidence(self):
        report = run_audit(
            [
                transaction("tx_1", wechat_transaction_id="wx:1"),
                transaction("tx_2", merchant="different merchant", wechat_transaction_id="wx:1"),
            ]
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["duplicate_source_reference_count"], 1)
        duplicates = report["source_references"]["wechat_transaction_id"]["duplicates"]
        self.assertEqual(duplicates, [{"value": "wx:1", "ids": ["tx_1", "tx_2"]}])
        self.assertEqual(report["source_confirmed_same_value_groups"], [])

    def test_missing_transaction_id_fails_the_audit(self):
        report = run_audit([transaction(None)])

        self.assertFalse(report["ok"])
        self.assertEqual(report["unique_transaction_ids"], 0)
        self.assertEqual(report["missing_transaction_id_indexes"], [0])

    def test_expense_and_refund_pair_is_not_a_duplicate(self):
        report = run_audit(
            [
                transaction("tx_expense", "expense"),
                transaction("tx_refund", "refund"),
            ]
        )

        self.assertTrue(report["ok"])
        self.assertEqual(report["cross_source_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()

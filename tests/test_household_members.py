import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ledger_core.domain.metrics import summarize
from ledger_core.services.analysis import filter_from_args

class HouseholdMembersTest(unittest.TestCase):
    def test_member_income_respects_existing_semantics(self):
        def row(kind, amount, **kw):
            return dict(type=kind, amount=amount, currency='CNY', occurred_at='2026-08-27T16:12:04+08:00', **kw)
        rows = [row('income', 10000, x_member='家人'), row('income', 100, x_member='本人'),
                row('transfer', 5000, x_member='家人'), row('income', 9000, x_member='本人', excluded_from_totals=True),
                row('income', 50, x_member='家人', relation='reimbursement'), row('income', 20)]
        metrics = summarize(rows)
        self.assertEqual(metrics['totals']['income'], 10120)
        self.assertEqual(metrics['by_income_member'], {'家人':10000, '本人':100, '未标注':20})
        selected, _ = filter_from_args(rows, SimpleNamespace(member='家人', month='2026-08'))
        self.assertEqual(len(selected), 3)
        self.assertEqual(summarize(selected)['totals']['income'], 10000)
        self.assertEqual(filter_from_args(rows, SimpleNamespace(member='未知', month='2026-08'))[0], [])

if __name__ == '__main__': unittest.main()

"""A configured income view must reconcile without creating cash events."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ledger_core.domain.metrics import summarize, account_balances
from ledger_core.domain.model import empty_ledger
from ledger_core.storage.repository import Ledger
from ledger_core.services import reporting_basis as basis, reports, generative_ui
from ledger_core.services.analysis import build_summary, month_snapshot


class ReportingBasisTest(unittest.TestCase):
    def setUp(self):
        self.config = {'version': 1, 'income_label': '结算所得', 'income_adjustments': [
            {'id': 'receipts', 'sign': 1, 'member': '甲', 'label': '工作收款',
             'match': {'type': 'transfer', 'category': '工作调拨', 'account_id': 'work'}},
            {'id': 'out', 'sign': -1, 'member': '甲', 'label': '走账扣减',
             'match': {'type': 'expense', 'category': '走账', 'excluded_from_totals': True},
             'not_match': {'account_id': 'work'}},
            {'id': 'return', 'sign': 1, 'member': '甲', 'label': '走账退回',
             'match': {'type': 'refund', 'category': '走账退款', 'excluded_from_totals': True}}
        ]}
        def row(id, kind, amount, category, **kw):
            return dict(id=id, type=kind, amount=amount, category=category, currency='CNY',
                        occurred_at='2026-09-10T12:00:00+08:00', **kw)
        self.rows = [row('salary', 'income', 100, '工资', x_member='乙'),
                     row('work', 'transfer', 1000, '工作调拨', account_id='work'),
                     row('pass', 'expense', 900, '走账', excluded_from_totals=True),
                     row('direct', 'expense', 200, '走账', account_id='work', excluded_from_totals=True),
                     row('back', 'refund', 10, '走账退款', excluded_from_totals=True),
                     row('expense', 'expense', 50, '餐饮'), row('refund', 'refund', 5, '餐饮'),
                     row('family', 'transfer', 2000, '家庭调拨')]
        self.profile = {'reporting': self.config}

    def test_income_reconciles_without_mutating_rows(self):
        before = copy.deepcopy(self.rows)
        result = basis.apply(summarize(self.rows), self.rows, self.profile)
        self.assertEqual(result['raw_totals']['income'], 100)
        self.assertEqual(result['totals'], dict(income=210, expense=50, refund=5, transfer=3000, net=165))
        self.assertEqual(result['by_income_member'], {'乙':100, '甲':110})
        self.assertEqual(sum(result['by_income_category'].values()), 210)
        self.assertEqual(sum(e['contribution'] for e in result['income_evidence']), 210)
        self.assertEqual(self.rows, before)
        with self.assertRaisesRegex(ValueError, 'already applied'):
            basis.apply(result, self.rows, self.profile)

    def test_disabled_profile_keeps_original_metrics(self):
        metrics = summarize(self.rows)
        self.assertEqual(basis.apply(copy.deepcopy(metrics), self.rows, {}), metrics)

    def test_overlap_and_double_count_are_refused(self):
        duplicate = copy.deepcopy(self.config['income_adjustments'][0])
        duplicate['id'] = 'duplicate'
        self.config['income_adjustments'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'Overlapping'):
            basis.apply(summarize(self.rows), self.rows, self.profile)
        self.config['income_adjustments'] = [{'id':'bad', 'sign':1, 'member':'甲', 'label':'重复',
                                            'match':{'type':'income', 'category':'工资'}}]
        with self.assertRaisesRegex(ValueError, 'excluded'):
            basis.validate(self.config)

    def test_direction_and_member_conflicts_are_refused(self):
        self.config['income_adjustments'][0]['sign'] = -1
        with self.assertRaisesRegex(ValueError, 'direction'):
            basis.validate(self.config)
        self.config['income_adjustments'][0]['sign'] = 1
        self.rows[1]['x_member'] = '乙'
        with self.assertRaisesRegex(ValueError, 'member conflicts'):
            basis.attributed_rows(self.rows, self.profile)

    def test_summary_report_ui_and_comparison_agree(self):
        ledger = Ledger(empty_ledger())
        ledger.profile = self.profile
        ledger.profile_text = ''
        ledger.profile_token = None
        ledger.read_token = None
        ledger.runtime = {}
        ledger.runtime_token = None
        ledger['transactions'] = self.rows
        previous = [dict(r, id='previous-'+r['id'], occurred_at='2026-08-10T12:00:00+08:00') for r in self.rows]
        ledger['transactions'] += previous
        balances = account_balances(ledger)
        summary = build_summary(ledger, SimpleNamespace(month='2026-09', compare=True))
        report = reports.from_ledger(ledger, start='2026-09-01', end='2026-09-30')
        self.assertEqual(summary['totals'], report['metrics']['totals'])
        self.assertEqual(month_snapshot(ledger, '2026-09')['totals'], summary['totals'])
        self.assertEqual(summary['comparison']['previous_totals'], summary['totals'])
        self.assertEqual(summary['comparison']['income_change_pct'], 0)
        member = build_summary(ledger, SimpleNamespace(month='2026-09', member='甲'))
        self.assertEqual(member['totals']['income'], 110)
        view = generative_ui.project(report, refs=['totals.income'])
        dataset = view['datasets']['totals.income']
        self.assertEqual(float(dataset['value']), 210)
        self.assertEqual(float(dataset['raw_value']), 100)
        self.assertEqual(len(dataset['contributions']), 4)
        self.assertEqual(account_balances(ledger), balances)


if __name__ == '__main__':
    unittest.main()

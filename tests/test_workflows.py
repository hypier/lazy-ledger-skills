import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ledger_core.storage import repository
from ledger_core.services import proposals, imports, observations, reports, profile


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.path = self.root / 'ledger.json'
        repository.save(self.path, repository.load(self.path))

    def proposal(self, amount=10, **fields):
        return proposals.prepare(self.path, {'reason': '用户记账', 'operations': [{'action': 'add', 'after': {'amount': amount, 'occurred_at': '2026-10-01T12:00:00+08:00', **fields}}]})

    def test_proposal_is_previewed_stale_guarded_and_idempotent(self):
        proposal = self.proposal()
        self.assertEqual(repository.load(self.path)['transactions'], [])
        receipt = proposals.commit(self.path, proposal)
        again = proposals.commit(self.path, proposal)
        self.assertEqual(receipt['operation_id'], again['operation_id'])
        self.assertEqual(len(repository.load(self.path)['transactions']), 1)
        stale = self.proposal(20)
        doc = profile.profile_path(self.path); doc.write_text(doc.read_text()+'\n用户补充\n')
        with self.assertRaises(ValueError): proposals.commit(self.path, stale)

    def test_targeted_undo_preserves_unrelated_later_records(self):
        a = self.proposal(10); proposals.commit(self.path, a)
        b = self.proposal(20); proposals.commit(self.path, b)
        proposals.undo(self.path, a['id'], apply=True)
        self.assertEqual([r['amount'] for r in repository.load(self.path)['transactions']], [20])
        with self.assertRaises(ValueError): proposals.undo(self.path, a['id'], apply=True)

    def test_undo_refuses_a_later_change_to_the_same_row(self):
        a = self.proposal(); proposals.commit(self.path, a)
        txid = a['operations'][0]['id']
        edit = proposals.prepare(self.path, {'reason':'纠正', 'operations':[{'action':'update','id':txid,'set':{'amount':15}}]})
        proposals.commit(self.path, edit)
        with self.assertRaises(ValueError): proposals.undo(self.path, a['id'], apply=True)

    def statement(self, lines, name='wechat.csv', prefix=''):
        p=self.root/name
        p.write_text(prefix+'交易时间,交易类型,交易对方,商品,收/支,金额(元),支付方式,当前状态,交易单号\n'+lines, encoding='utf-8-sig')
        return p

    def test_import_replays_source_identity_and_preserves_real_repeat_purchase(self):
        file=self.statement('2026-10-01 12:00:00,商户消费,饭馆,午饭,支出,28,零钱,支付成功,wx1\n2026-10-01 12:01:00,商户消费,饭馆,午饭,支出,28,零钱,支付成功,wx2\n')
        plan=imports.prepare(self.path,file,expected_count=2,expected_total='56')
        self.assertTrue(plan['ready']); proposals.commit(self.path,plan['proposal'])
        again=imports.prepare(self.path,file)
        self.assertTrue(again['ready']); proposals.commit(self.path,again['proposal'])
        rows=repository.load(self.path)['transactions']
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['field_evidence']['amount']['original'],'28')

    def test_import_discrepancies_block_writes(self):
        file=self.statement('2026-10-01 12:00:00,商户消费,饭馆,午饭,支出,28,零钱,支付成功,wx1\n')
        plan=imports.prepare(self.path,file,expected_count=2)
        self.assertFalse(plan['ready']); self.assertNotIn('proposal',plan)
        self.assertEqual(repository.load(self.path)['transactions'],[])

    def test_refunded_original_is_reviewed_without_becoming_refund_income(self):
        file=self.statement('2026-10-01 12:00:00,商户消费,商店,商品,支出,28,零钱,退款成功,wx1\n')
        plan=imports.prepare(self.path,file)
        self.assertFalse(plan['ready'])
        parsed=imports.parse_file(file)
        self.assertEqual(parsed['rows'][0]['candidate']['type'],'expense')
        self.assertEqual(repository.load(self.path)['transactions'],[])

    def test_alipay_header_and_refund_keep_original_cells(self):
        file=self.root/'alipay.csv'
        file.write_text('支付宝交易记录\n交易号,交易创建时间,交易对方,商品名称,金额（元）,收/支,交易状态\na1,2026-10-01 12:00:00,商店,退货,12,收入,退款成功\n')
        plan=imports.prepare(self.path,file)
        self.assertTrue(plan['ready']); proposals.commit(self.path,plan['proposal'])
        row=repository.load(self.path)['transactions'][0]
        self.assertEqual(row['type'],'refund')
        self.assertEqual(row['sources'][0]['provider'],'alipay')
        self.assertEqual(row['sources'][0]['raw']['交易状态'],'退款成功')

    def test_cross_source_overlap_needs_explicit_link(self):
        file=self.statement('2026-10-01 12:00:00,商户消费,饭馆,午饭,支出,28,零钱,支付成功,wx1\n')
        proposals.commit(self.path,imports.prepare(self.path,file)['proposal'])
        file2=self.statement('2026-10-01 12:00:00,商户消费,饭馆,午饭,支出,28,银行卡,支付成功,bank1\n',name='bank.csv')
        ambiguous=imports.prepare(self.path,file2,provider='bank')
        self.assertFalse(ambiguous['ready'])
        tx=repository.load(self.path)['transactions'][0]
        linked=imports.prepare(self.path,file2,provider='bank',links={'2':tx['id']})
        proposals.commit(self.path,linked['proposal'])
        rows=repository.load(self.path)['transactions']
        self.assertEqual(len(rows),1); self.assertEqual(len(rows[0]['sources']),2)

    def test_observations_invalidate_on_same_count_edit_and_never_rewrite_profile(self):
        proposal=self.proposal(category='餐饮'); proposals.commit(self.path,proposal)
        before=profile.profile_path(self.path).read_bytes()
        old=observations.get(self.path,refresh=True)
        edit=proposals.prepare(self.path,{'reason':'分类纠正','operations':[{'action':'update','id':proposal['operations'][0]['id'],'set':{'category':'交通'}}]})
        proposals.commit(self.path,edit)
        new=observations.get(self.path)
        self.assertNotEqual(new['input_token'],old['input_token'])
        self.assertEqual(new['category_counts'],{'交通':1})
        self.assertEqual(profile.profile_path(self.path).read_bytes(),before)

    def test_archived_report_is_independent_of_later_corrections_and_escapes_content(self):
        p=self.proposal(merchant='<script>alert(1)</script>'); proposals.commit(self.path,p)
        report=reports.build(self.path,start='2026-10-01',end='2026-10-31')
        artifact=reports.archive(self.path,report,format='html')
        self.assertNotIn('<script>',Path(artifact['artifact']).read_text())
        proposals.undo(self.path,p['id'],apply=True)
        old=reports.read_archive(self.path,report['id'])
        self.assertEqual(old['metrics']['totals']['expense'],10)
        self.assertEqual(len(old['evidence']['expense']),1)
        current=reports.build(self.path,start='2026-10-01',end='2026-10-31')
        self.assertEqual(current['metrics']['totals']['expense'],0)
        self.assertFalse(old['coverage']['complete'])


class EvolutionTest(unittest.TestCase):
    setUp = WorkflowTest.setUp
    proposal = WorkflowTest.proposal

    def test_metadata_migration_preserves_financial_metrics_and_retries_once(self):
        from ledger_core.services import migrations
        ledger=repository.load(self.path)
        ledger.runtime['custom_fields']['x_project']={'type':'string','description':'项目','affects_totals':False}
        repository.save(self.path,ledger)
        proposals.commit(self.path,self.proposal(x_project='12'))
        plan=migrations.prepare(self.path,{'reason':'项目编号改名','operations':[{'field':'x_project','rename':'x_project_id','type':'number'}]})
        self.assertEqual(plan['before'],plan['after'])
        receipt=migrations.apply(self.path,plan)
        self.assertEqual(migrations.apply(self.path,plan),receipt)
        ledger=repository.load(self.path)
        self.assertEqual(ledger['transactions'][0]['x_project_id'],12)
        self.assertNotIn('x_project',ledger.runtime['custom_fields'])
        self.assertTrue((Path(receipt['backup'])/'ledger.json').exists())
        with self.assertRaises(ValueError): migrations.prepare(self.path,{'reason':'unsafe','operations':[{'field':'amount','type':'string'}]})

    def test_finding_decisions_follow_evidence_and_snooze_expires(self):
        from ledger_core.services import findings
        candidate={'key':'low:t1','code':'low','transaction_id':'t1','amount':12,'detail':'需确认','status':'open'}
        ledger={'review_queue':[]}
        item=findings.reconcile(ledger,[candidate])[0]
        findings.transition(item,'dismissed','已确认')
        ledger['review_queue']=[item]
        self.assertEqual(findings.reconcile(ledger,[candidate])[0]['status'],'dismissed')
        changed=findings.reconcile(ledger,[{**candidate,'amount':13}])[0]
        self.assertEqual(changed['status'],'open')
        item.update(status='snoozed',snooze_until='2000-01-01')
        self.assertEqual(findings.reconcile(ledger,[candidate])[0]['status'],'open')
        self.assertEqual(findings.reconcile(ledger,[])[0]['status'],'resolved')
        receipt=findings.notifications(self.path,[changed])
        self.assertEqual(len(receipt['items']),1)
        self.assertEqual(findings.notifications(self.path,[changed],acknowledge=receipt)['items'],[])
        newer={**changed,'evidence':'new-evidence'}
        self.assertEqual(len(findings.notifications(self.path,[newer])['items']),1)

if __name__=='__main__': unittest.main()

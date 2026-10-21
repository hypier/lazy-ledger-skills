"""Cross-cutting acceptance at public service boundaries; synthetic data only."""
import copy
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from ledger_core.services import imports, proposals, migrations, profile, findings, reports
from ledger_core.storage import repository
from ledger_core.domain.merge import align_source_identities

class ReleaseAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'book.json'
        repository.save(self.path,repository.load(self.path))

    def test_bank_debit_credit_and_credit_refunds_preserve_cells(self):
        source=self.root/'bank.csv'
        source.write_text('交易日期,摘要,支出金额,收入金额,余额,流水号\n2026-10-01,午饭,12,,88,b1\n2026-10-02,工资,,100,188,b2\n')
        plan=imports.prepare(self.path,source)
        self.assertTrue(plan['ready']);proposals.commit(self.path,plan['proposal'])
        rows=repository.load(self.path)['transactions']
        self.assertEqual([r['type'] for r in rows],['expense','income'])
        self.assertEqual(rows[0]['sources'][0]['raw']['余额'],'88')
        source.write_text('消费日期,交易说明,入账金额,交易性质,交易号\n2026-10-03,退货,5,退款,c1\n')
        plan=imports.prepare(self.path,source)
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['proposal']['operations'][0]['after']['type'],'refund')
        self.assertEqual(plan['source']['provider'],'credit')

    def test_bank_ambiguous_signed_row_and_unconfirmed_image_do_not_write(self):
        source=self.root/'bank.csv';source.write_text('交易日期,支出金额,收入金额\n2026-10-01,12,100\n')
        self.assertFalse(imports.prepare(self.path,source)['ready'])
        source=self.root/'image.tsv';source.write_text('occurred_at\tamount\ttype\tsource\n2026-10-01\t12\texpense\timage\n')
        self.assertFalse(imports.prepare(self.path,source)['ready'])
        self.assertTrue(imports.prepare(self.path,source,confirmed_image=True)['ready'])
        self.assertEqual(repository.load(self.path)['transactions'],[])

    def test_legacy_entry_replays_without_duplicate_or_losing_evidence(self):
        source=self.root/'rows.tsv';source.write_text('occurred_at\tamount\ttype\tnote\texcluded_from_totals\n2026-10-01\t12\texpense\tkeep\ttrue\n')
        self.assertEqual(imports.import_legacy(self.path,source)['added'],1)
        self.assertEqual(imports.import_legacy(self.path,source,allow_duplicate=True)['added'],0)
        row=repository.load(self.path)['transactions'][0]
        self.assertTrue(row['excluded_from_totals']);self.assertEqual(row['sources'][0]['raw']['note'],'keep')

    def test_identity_alignment_rewrites_active_references_not_audit(self):
        base={'ledger':{'transactions':[]}}
        left={'ledger':{'transactions':[{'id':'b','amount':5,'sources':[{'provider':'bank','transaction_id':'x'}]}], 'changes':[{'after_id':'b'}]}}
        right={'ledger':{'transactions':[{'id':'a','amount':5,'sources':[{'provider':'bank','transaction_id':'x'}]}, {'id':'r','amount':5,'related_transaction_id':'b'}]}}
        b,l,r=align_source_identities([base,left,right])
        self.assertEqual(l['ledger']['transactions'][0]['id'],'a')
        self.assertEqual(r['ledger']['transactions'][1]['related_transaction_id'],'a')
        self.assertEqual(l['ledger']['changes'],[{'after_id':'b'}])

    def test_full_core_migration_preview_preserves_unknown_and_financial_state(self):
        old=json.loads(self.path.read_text());old['schema_version']=1;old['vendor_metadata']={'retained':True}
        self.path.write_text(json.dumps(old));profile.profile_path(self.path).unlink()
        (profile.runtime_dir(self.path)/'runtime.json').unlink()
        preview=migrations.migrate_core(self.path)
        self.assertEqual(preview['before'],preview['after'])
        self.assertIn('source_identities',preview['before']);self.assertIn('outstanding',preview['before'])
        result=migrations.migrate_core(self.path,True)
        self.assertTrue(result['applied']);self.assertTrue((Path(result['backup'])/'ledger.json').exists())
        self.assertEqual(repository.load(self.path)['vendor_metadata'],{'retained':True})

    def test_actionable_findings_are_persisted_and_dismissals_survive_refresh(self):
        ops=[{'action':'add','after':{'amount':800,'occurred_at':'2026-10-01','merchant':'商户'}} for _ in range(2)]
        proposals.commit(self.path,proposals.prepare(self.path,{'reason':'fixture','operations':ops}))
        ledger=repository.load(self.path)
        duplicate=next(r for r in ledger['review_queue'] if r['code']=='duplicate_suspected')
        findings.transition(duplicate,'dismissed','confirmed separate purchases')
        repository.save(self.path,ledger)
        self.assertFalse(any(f['kind']=='duplicate_suspected' for f in findings.active_insights(repository.load(self.path))))

    def test_comparisons_use_matching_elapsed_month_window(self):
        from ledger_core.services.analysis import resolve_period, previous_period
        current=resolve_period(range_name='this-month',now=datetime.datetime(2026,10,18))
        previous=previous_period(current)
        self.assertEqual((previous['start'],previous['end']),('2026-09-01','2026-09-18'))
        full=previous_period(resolve_period(month='2026-10'))
        self.assertEqual(full['end'],'2026-09-30')

    def test_legacy_memory_is_preserved_without_becoming_confirmed_rules(self):
        from ledger_core.services.habits import habit_memory_path
        old=json.loads(self.path.read_text());old['schema_version']=1
        self.path.write_text(json.dumps(old));profile.profile_path(self.path).unlink()
        (profile.runtime_dir(self.path)/'runtime.json').unlink()
        habit_memory_path(self.path).write_text('旧统计：午饭常为20元。')
        receipt=migrations.migrate_core(self.path,True)
        loaded=repository.load(self.path)
        self.assertIn('旧统计',loaded.profile_text)
        self.assertEqual(loaded.profile['habits'],[])
        self.assertTrue((Path(receipt['backup'])/'legacy-memory.md').exists())

    def test_correction_evidence_replaces_stale_source_value_and_undo_restores_it(self):
        row={'amount':12,'occurred_at':'2026-10-01','field_evidence':{'amount':{'kind':'source','value':12}}}
        plan=proposals.prepare(self.path,{'reason':'source','operations':[{'action':'add','after':row}]})
        proposals.commit(self.path,plan);identity=plan['operations'][0]['id']
        correction=proposals.prepare(self.path,{'reason':'receipt says 15','operations':[{'action':'update','id':identity,'set':{'amount':15}}]})
        proposals.commit(self.path,correction)
        evidence=repository.load(self.path)['transactions'][0]['field_evidence']['amount']
        self.assertEqual((evidence['kind'],evidence['value'],evidence['previous_value']),('correction',15,12))
        proposals.undo(self.path,correction['id'],apply=True)
        self.assertEqual(repository.load(self.path)['transactions'][0]['field_evidence']['amount']['value'],12)

    def test_compact_report_retains_metrics_and_archived_snapshot(self):
        ledger=repository.load(self.path);ledger.profile_override={**ledger.profile,'presentation':{'density':'compact'}};repository.save(self.path,ledger)
        report=reports.build(self.path,start='2026-10-01',end='2026-10-31')
        self.assertIn('收入 0.00',reports.render(report))
        self.assertIn('不代表全部实际收支',reports.render(report))
        self.assertNotIn('<script',reports.render(report,'html'))
        saved=reports.archive(self.path,report,format='html')
        self.assertEqual(reports.read_archive(self.path,saved['report_id'])['digest'],report['digest'])

if __name__=='__main__':unittest.main()

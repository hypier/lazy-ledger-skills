"""Synthetic browser fixtures; never opens a user's ledger."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ledger_core.storage import repository
from ledger_core.services import reports, generative_ui as ui, proposals, imports


def generate(folder):
    folder = Path(folder)
    path = folder / "fixture.json"
    repository.save(path, repository.load(path))
    operations = [{"action": "add", "after": {"amount": 12.35, "occurred_at": f"2026-10-{i+1:02}",
                   "type": "expense", "merchant": f"午餐商户 {i+1}", "category": "餐饮"}} for i in range(24)]
    operations.extend([
        {"action": "add", "after": {"amount": 8000, "occurred_at": "2026-10-01", "type": "income", "merchant": "工资"}},
        {"action": "add", "after": {"amount": 5, "occurred_at": "2026-10-02", "type": "refund", "merchant": '</script><script>window.injected=true</script>'}},
        {"action": "add", "collection": "budgets", "after": {"id": "budget_food", "month": "2026-10", "category": "餐饮", "amount": 500}},
    ])
    proposals.commit(path, proposals.prepare(path, {"reason": "synthetic fixture", "operations": operations}))
    report = reports.build(path, start="2026-10-01", end="2026-10-31")
    (folder / "monthly.html").write_text(reports.render(report, "openui"))
    (folder / "budget.html").write_text(reports.render(report, "openui", layout={"version": 1, "blocks": [{"component": "Budget", "ref": "budgets"}]}))
    (folder / "view.json").write_text(reports.render(report, "view-json"))
    broken = ui.project(report)
    broken["lang"] += '\nevil = Mutation("write")'
    (folder / "broken.html").write_text(ui.document(broken))
    bad_data = ui.project(report)
    bad_data["datasets"]["transactions"] = None
    (folder / "bad-data.html").write_text(ui.document(bad_data))
    source = folder / "input.csv"
    source.write_text('occurred_at,amount,type,merchant\n2026-10-01,12.35,expense,午餐商户 1\n2026-10-01,6,expense,新商户\nbad,4,expense,错误商户\n')
    plan = imports.prepare(path, source)
    (folder / "import.html").write_text(reports.render(reports.from_import(plan), "openui"))
    return report


if __name__ == "__main__":
    generate(sys.argv[1])

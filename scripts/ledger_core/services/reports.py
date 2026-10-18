"""Immutable, format-independent reports with row-level evidence."""
import html
import json
import uuid
from pathlib import Path
from ..domain.model import now_iso
from ..domain.metrics import summarize, budget_progress, account_balances, category_parent_map
from ..domain.semantics import counts_as
from ..storage import repository
from ..storage.documents import atomic_write_json, write_text_secure, read_json
from .profile import runtime_dir
from .proposals import context_token


def build(path, *, start, end):
    ledger = repository.load(path, create=False)
    from .history import settings, sync_status
    state = {**settings(path), "status": sync_status(path)}
    return from_ledger(ledger, start=start, end=end, history_state=state)


def from_ledger(ledger, *, start, end, history_state=None):
    from datetime import date
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("Report start is after end")
    rows = [r for r in ledger["transactions"] if start <= r.get("occurred_at", "")[:10] <= end]
    period = {"label": f"{start} 至 {end}", "start": start, "end": end,
              "month": start[:7] if start[:7] == end[:7] else None}
    metrics = summarize(rows, period=period, category_parents=category_parent_map(ledger))
    metrics["budgets"] = budget_progress(ledger, rows, period)
    metrics["accounts"] = account_balances(ledger)
    evidence = {kind: [r["id"] for r in rows if counts_as(r, kind)] for kind in ("expense", "income", "refund")}
    report = {"version": 1, "id": "report_" + uuid.uuid4().hex, "generated_at": now_iso(),
              "context": context_token(ledger), "currency": ledger["currency"], "period": period,
              "metrics": metrics, "evidence": evidence, "transactions": rows,
              "balance_scope": "current ledger snapshot, not period-end balance",
              "balance_inputs": {"accounts": ledger["accounts"], "transactions": ledger["transactions"]},
              "history": history_state or {"mode": "off"},
              "review": [r for r in ledger.get("review_queue", []) if r.get("status") == "open"],
              "coverage": {"recorded_rows": len(rows), "unassigned_rows": sum(not r.get("account_id") for r in rows),
                           "unknown_openings": [a["id"] for a in ledger["accounts"] if not a.get("opening_confirmed")],
                           "sources": ledger.get("imports", []), "complete": False},
              "profile": ledger.profile, "profile_document": ledger.profile_text,
              "capabilities": ledger.runtime, "metric_version": 2,
              "presentation": ledger.profile.get("presentation", {})}
    report["digest"] = repository.fingerprint(report)
    return report


def check(report):
    digest = repository.fingerprint({k:v for k,v in report.items() if k != "digest"})
    if report.get("version") != 1 or digest != report.get("digest"):
        raise ValueError("Report snapshot failed its integrity check")


def render(report, format="markdown"):
    check(report)
    if format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if format not in {"markdown", "html"}:
        raise ValueError("Unsupported report format")
    totals = report["metrics"]["totals"]
    lines = [f"# {report['period']['label']} 账务报告", "",
             f"币种：{report['currency']}；账本修订：{report['context']['revision']}。",
             f"已记录收入 {totals['income']:.2f}，支出 {totals['expense']:.2f}，退款 {totals['refund']:.2f}，净额 {totals['net']:.2f}。",
             f"覆盖 {report['coverage']['recorded_rows']} 条流水，其中 {report['coverage']['unassigned_rows']} 条未指定账户。",
             "以上为已记录数据，不代表全部实际收支；账户期初未确认时，余额仅作流水核对。", "", "## 金额依据", ""]
    for kind, label in (("expense", "支出"), ("income", "收入"), ("refund", "退款")):
        lines.append(f"- {label}依据：" + ("、".join(report["evidence"][kind]) or "无"))
    if report["metrics"]["budgets"]:
        lines.extend(["", "## 预算", ""])
        for budget in report["metrics"]["budgets"]:
            lines.append(f"- {budget['category']}：已用 {budget['spent']:.2f} / {budget['limit']:.2f}，剩余 {budget['remaining']:.2f}。")
    lines.extend(["", "## 账户余额（生成报告时的流水余额）", ""])
    for account in report["metrics"]["accounts"]:
        lines.append(f"- {account['name']}：{account['balance']:.2f}" + ("（期初未确认）" if not account['opening_confirmed'] else ""))
    if report.get("review"):
        lines.extend(["", "## 待复核", ""])
        lines.extend("- " + str(item.get("detail") or item.get("code")) for item in report["review"])
    state = report.get("history", {})
    if state.get("mode") == "remote":
        lines.extend(["", "同步状态：" + str(state.get("status", {}).get("state", "never_synced")) + "。"])
    elif state.get("mode") == "local":
        lines.extend(["", "历史：仅本地 Git 快照。"])
    lines.extend(["", "## 明细", ""])
    for row in report["transactions"] if report.get("presentation", {}).get("density") != "compact" else []:
        label = str(row.get("merchant") or row.get("note") or row.get("category") or "").replace("\n", " ")
        # Markdown source text is rendered as escaped plain text, never active HTML.
        label = html.escape(label).replace("[", "\\[").replace("]", "\\]")
        lines.append(f"- {row['occurred_at'][:10]} · {row['type']} · {row['amount']:.2f} · {label} · {row['id']}")
    markdown = "\n".join(lines) + "\n"
    if format == "markdown":
        return markdown
    return html_document(markdown)


def html_document(markdown):
    """Escape all source strings; HTML never interprets financial data or runs code."""
    content = []
    for line in markdown.splitlines():
        if not line: continue
        tag, text = ('h1', line[2:]) if line.startswith('# ') else ('h2', line[3:]) if line.startswith('## ') else ('p', line)
        content.append('<' + tag + '>' + html.escape(html.unescape(text)) + '</' + tag + '>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>账务报告</title><style>body{max-width:72ch;margin:2rem auto;padding:1rem;font:16px/1.7 system-ui;color:#22332b;background:#fafbf8;overflow-wrap:anywhere}h1{font-size:1.6rem}h2{font-size:1.2rem;margin-top:2rem}main{padding:1rem}a{color:#245f49}</style><main>' + ''.join(content) + '</main></html>'


def archive(path, report, *, format="markdown"):
    check(report)
    output = render(report, format)
    target = runtime_dir(path) / "reports" / report["id"]
    target.mkdir(parents=True, exist_ok=False)
    atomic_write_json(target / "data.json", report)
    extension = {"markdown": "md", "json": "json", "html": "html"}[format]
    artifact = target / ("report." + extension)
    write_text_secure(artifact, output)
    atomic_write_json(target / "manifest.json", {"version": 1, "report_id": report["id"], "data_digest": report["digest"], "renderer": "core-report-v1", "format": format})
    return {"report_id": report["id"], "data": str(target / "data.json"), "artifact": str(artifact)}


def read_archive(path, report_id):
    import re
    if not re.fullmatch(r"report_[0-9a-f]{32}", report_id):
        raise ValueError("Invalid report ID")
    report = read_json(runtime_dir(path) / "reports" / report_id / "data.json")
    check(report)
    return report

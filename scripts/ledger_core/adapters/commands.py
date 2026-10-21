"""Command output adapters shared by the CLI and local HTTP service."""
from ledger_core.domain import money
import csv
import functools
import io
import json
import os
import sys
from datetime import date
from pathlib import Path
from ledger_core.storage.documents import ledger_lock, write_text_secure
from ledger_core.domain.model import now_iso, default_currency, ledger_preferences
from ledger_core.domain.metrics import account_balances, budget_progress, tx_day, month_key, compact_candidate, category_entries
from ledger_core.domain.semantics import counts_as, nature_of, settlement_of
from ledger_advice import build_advice
from ledger_brief import digest, render_brief
from ledger_core.services.findings import active_insights as build_findings
from ledger_voice import compose_record

from ledger_core.domain.vocabulary import (VALID_ADVANCE_STATES)
from ledger_core.services.analysis import build_summary, collect_open_items, filter_from_args, filter_transactions, month_snapshot, render_show, resolve_period
from ledger_core.services.common import advance_remaining, advance_settled_amount, coerce_tags, load_ledger, load_optional_ledger, mutating, safe_amount, tx_sort_key
from ledger_core.services.habits import habits_payload
from ledger_core.services.inspections import (build_review_queue, doctor_report)
from ledger_core.services.interpretation import (classify_event_detailed, extract_date_token, likely_duplicate_candidates, normalized_text, parse_text_transaction, parse_text_transactions)
from ledger_core.services.mutations import (add_transaction_result)
from ledger_core.services.presentation import attach_bill_file, build_bill_pack, format_list_row, render_bill_html, render_bill_pack


def json_command(func):
    @functools.wraps(func)
    def cli(args):
        result = func(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    cli.result = func
    return cli


@json_command
def add_transaction(args):
    return add_transaction_result(args)


def brief_command(args):
    """Say how things stand, as a person would, instead of printing a table."""
    ledger = load_ledger(args.ledger, create=False)
    currency = default_currency(ledger.get("currency"))
    # A briefing is about now, so default to the current month rather than
    # everything ever recorded.
    if not (getattr(args, "month", None) or getattr(args, "start", None) or getattr(args, "end", None) or getattr(args, "range", None)):
        args.range = "this-month"
    data = build_summary(ledger, args)
    findings = build_findings(
        ledger,
        currency=currency,
        balances=data["accounts"],
        limit=6,
    )
    open_items = collect_open_items(ledger, currency)
    # A stale advance is reported both as a finding and as an open item. Showing
    # both makes the butler look like it is nagging, so keep the finding's
    # phrasing and drop the duplicate.
    stale_ids = {
        finding.get("transaction_id")
        for finding in findings
        if finding.get("kind") == "stale_advance"
    }
    open_items = [
        item
        for item in open_items
        if not (item.get("kind") == "advance_open" and item.get("transaction_id") in stale_ids)
    ]
    expenses = [
        tx
        for tx in ledger.get("transactions") or []
        if isinstance(tx, dict) and counts_as(tx, "expense") and month_key(tx.get("occurred_at")) == month_key(now_iso())
    ]
    advice = build_advice(ledger, expenses, month_key(now_iso()), currency, findings=findings) if ledger_preferences(ledger).get("communication", {}).get("advice", False) else []
    if args.json:
        print(
            json.dumps(
                {
                    "headline": render_brief(data, ledger, currency, findings, open_items)[:1],
                    "findings": findings,
                    "open_items": open_items,
                    "advice": advice,
                    "totals": data.get("totals"),
                    "count": data.get("count"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for line in render_brief(data, ledger, currency, findings, open_items, advice=advice):
        print(line)


def _record_facts(payload, ledger, currency):
    """Attach the two axes and the period totals to every row we just wrote."""
    month = payload.get("month") or month_key(now_iso())
    after = payload.get("month") or {}
    for row in payload.get("added") or []:
        if not isinstance(row, dict):
            continue
        row["nature"] = nature_of(row)
        row["settlement"] = settlement_of(row)
    return payload


@mutating
def record_command(args):
    """Write a row the way ``add`` does, then say what it means.

    ``add`` answers "what was stored"; this answers "what did I just do to my
    money". Same write path, same validation, same duplicate protection -- only
    the reply differs, which is why the two cannot drift apart.
    """
    ledger_before = load_ledger(args.ledger, create=False) if os.path.exists(args.ledger) else None
    currency = default_currency((ledger_before or {}).get("currency") or args.currency)
    month = month_key(now_iso())
    before = month_snapshot(ledger_before, month) if ledger_before else {"count": 0, "totals": {}}
    before_totals = dict(before.get("totals") or {})

    payload = add_transaction_result(args)
    if payload is None:
        return

    currency = default_currency((load_ledger(args.ledger, create=False) or {}).get("currency"))
    payload = _record_facts(payload, ledger_before, currency)
    after = payload.get("month") or {}
    after_totals = dict(after.get("totals") or {})
    after_totals["label"] = month
    payload["before"] = before_totals
    payload["after"] = after_totals

    event = None
    rows = payload.get("added") or []
    if args.text and len(rows) == 1:
        event = classify_event_detailed(args.text)
        payload["event"] = event

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    ledger_after = load_ledger(args.ledger, create=False)
    findings = []
    if len(rows) == 1:
        findings = _related_findings(ledger_after, rows[0], currency)
    for line in compose_record(payload, currency, findings=findings):
        print(line)


def _related_findings(ledger, row, currency):
    """Findings this particular write could plausibly have caused or revealed.

    Reporting everything would make every coffee purchase re-raise last month's
    stale advance. Only two things are relevant right after a write: a balance
    that just went negative, and the account the money moved through.
    """
    if not isinstance(row, dict):
        return []
    balances = account_balances(ledger)
    findings = [item for item in build_findings(ledger, balances=balances, currency=currency) if item["kind"] == "negative_balance"]
    touched = {row.get("account_id"), row.get("to_account_id")}
    return [
        finding
        for finding in findings
        if finding.get("account") in {record.get("name") for record in balances if record.get("id") in touched}
    ]


def digest_command(args):
    """One report, ready to forward -- this is what a scheduled run prints.

    It shares every number with ``brief`` and differs only in audience: a digest
    is read away from the conversation, possibly on a phone, so commands and
    internal vocabulary are dropped and the closing section names only what
    genuinely needs a decision.
    """
    ledger = load_ledger(args.ledger, create=False)
    currency = default_currency(ledger.get("currency"))
    if not (args.month or args.start or args.end or args.range):
        args.range = "this-month"
    data = build_summary(ledger, args)
    findings = build_findings(
        ledger,
        currency=currency,
        balances=data["accounts"],
        limit=6,
    )
    open_items = collect_open_items(ledger, currency)
    stale_ids = {
        finding.get("transaction_id")
        for finding in findings
        if finding.get("kind") == "stale_advance"
    }
    open_items = [
        item
        for item in open_items
        if not (item.get("kind") == "advance_open" and item.get("transaction_id") in stale_ids)
    ]
    expenses = [
        tx
        for tx in ledger.get("transactions") or []
        if isinstance(tx, dict) and counts_as(tx, "expense")
    ]
    advice = build_advice(ledger, expenses, month_key(now_iso()), currency, findings=findings) if ledger_preferences(ledger).get("communication", {}).get("advice", False) else []
    lines = digest(data, ledger, currency, findings, open_items, advice=advice)
    if args.json:
        print(
            json.dumps(
                {
                    "period": data.get("period"),
                    "lines": lines,
                    "totals": data.get("totals"),
                    "findings": findings,
                    "open_items": open_items,
                    "advice": advice,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    print("\n".join(lines))


def summary_command(args):
    ledger = load_ledger(args.ledger, create=False)
    data = build_summary(ledger, args)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(render_show(data, ledger.get("currency", "CNY")).rstrip())


def show_command(args):
    ledger = load_ledger(args.ledger, create=False)
    default_range = None if (args.month or args.start or args.end or args.range) else "this-month"
    data = build_summary(ledger, args, default_range=default_range)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    text = render_show(data, ledger.get("currency", "CNY")).rstrip()
    if data["count"] == 0 and default_range == "this-month":
        months = [month_key(tx.get("occurred_at")) for tx in ledger.get("transactions", []) if isinstance(tx, dict) and month_key(tx.get("occurred_at"))]
        if months:
            text += f"\n最近有记录的月份是 {max(months)}。"
    print(text)


def bill_show_command(args):
    ledger = load_ledger(args.ledger, create=False)
    pack = attach_bill_file(build_bill_pack(ledger, args.month), args.ledger)
    if args.json:
        print(json.dumps(pack, ensure_ascii=False, indent=2))
        return
    print(render_bill_pack(pack, ledger.get("currency", "CNY")).rstrip())


def bill_list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows = [
        {
            "id": item.get("id"),
            "month": item.get("month"),
            "title": item.get("title"),
            "updated_at": item.get("updated_at"),
            "has_body": bool(item.get("body")),
        }
        for item in ledger.get("bills") or []
        if isinstance(item, dict)
    ]
    rows.sort(key=lambda item: item.get("month") or "", reverse=True)
    if args.json:
        print(json.dumps({"bills": rows}, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("还没有月度账单。")
        return
    for item in rows:
        mark = "已写" if item["has_body"] else "仅数字"
        print(f"{item['month']} | {item.get('title') or '月度账单'} | {mark}")


def bill_render_command(args):
    ledger = load_ledger(args.ledger, create=False)
    pack, html_path = render_bill_html(
        ledger,
        args.ledger,
        month=args.month,
        output_path=args.output,
        template_path=args.template,
    )
    print(json.dumps({"month": pack["month"], "file": str(html_path)}, ensure_ascii=False, indent=2))


def list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows, period = filter_from_args(ledger["transactions"], args)
    rows.sort(key=tx_sort_key, reverse=True)
    total = len(rows)
    selected = rows[: args.limit]
    # Silent truncation is how "that day has no transactions" mistakes happen:
    # always report how many matched and whether this is all of them.
    truncated = total > len(selected)
    if args.json:
        print(json.dumps({
            "transactions": selected,
            "count": len(selected),
            "total": total,
            "limit": args.limit,
            "truncated": truncated,
            "period": period,
        }, ensure_ascii=False, indent=2))
        return
    for tx in selected:
        print(format_list_row(tx))
    if truncated:
        print(f"（仅显示最近 {len(selected)} 笔，匹配共 {total} 笔；加 --limit {total} 查看全部）")


def find_command(args):
    ledger = load_ledger(args.ledger, create=False)
    if args.text:
        # A lookup may know only the merchant, so do not demand an amount.
        proposal = parse_text_transaction(
            args.text,
            ledger=ledger,
            currency=args.currency or ledger.get("currency", "CNY"),
            require_amount=False,
        )
        has_amount = proposal.get("amount") is not None
        has_date = bool(extract_date_token(args.text.strip()))
        # Without an amount, the merchant is the only strong signal.
        merchant_weight = 2 if has_amount else 3
        scored = []
        for tx in ledger["transactions"]:
            if not isinstance(tx, dict):
                continue
            score = 0
            if has_amount and round(safe_amount(tx.get("amount")), 2) == round(float(proposal["amount"]), 2):
                score += 2
            if has_date and tx_day(tx) == tx_day(proposal):
                score += 2
            if normalized_text(tx.get("merchant")) and normalized_text(tx.get("merchant")) == normalized_text(proposal.get("merchant")):
                score += merchant_weight
            if tx.get("category") and tx.get("category") == proposal.get("category"):
                score += 1
            if score >= 3:
                item = compact_candidate(tx)
                item["score"] = score
                scored.append(item)
        scored.sort(key=lambda item: (item["score"], item.get("occurred_at") or ""), reverse=True)
        print(json.dumps({"query": args.text, "proposal": proposal, "candidates": scored[: args.limit]}, ensure_ascii=False, indent=2))
        return
    list_command(args)


def render_command(args):
    from ledger_core.services import reports
    if args.template:
        raise SystemExit("Fixed templates are retired; use report build")
    ledger = load_ledger(args.ledger, create=False)
    days = [tx["occurred_at"][:10] for tx in ledger["transactions"]]
    start, end = (min(days), max(days)) if days else (date.today().isoformat(), date.today().isoformat())
    report = reports.build(args.ledger, start=start, end=end)
    write_text_secure(args.output, reports.render(report, "html"))
    print(str(Path(args.output).resolve()))


def serve_command(args):
    from ledger_server import run_server

    run_server(args.ledger, host=args.host, port=args.port, open_browser=args.open, block=True)


def parse_command(args):
    ledger = load_optional_ledger(args.ledger, args.currency)
    proposals = parse_text_transactions(
        args.text,
        ledger=ledger,
        currency=args.currency or ledger.get("currency", "CNY"),
        source=args.source,
    )
    for proposal in proposals:
        duplicates = likely_duplicate_candidates(ledger.get("transactions", []), proposal)
        if duplicates:
            proposal["possible_duplicates"] = duplicates
    if len(proposals) == 1:
        print(json.dumps(proposals[0], ensure_ascii=False, indent=2))
        return
    print(json.dumps({"count": len(proposals), "proposals": proposals}, ensure_ascii=False, indent=2))


def recent_command(args):
    list_command(args)


def insight_command(args):
    """Report what a careful person would notice, with evidence and a next step."""
    ledger = load_ledger(args.ledger, create=True)
    findings = build_findings(
        ledger,
        currency=default_currency(ledger.get("currency")),
        limit=getattr(args, "limit", None),
        balances=account_balances(ledger),
    )
    counts = {}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    if getattr(args, "json", False):
        print(json.dumps({"findings": findings, "count": len(findings), "severity_counts": counts}, ensure_ascii=False, indent=2))
        return
    if not findings:
        print("账本很干净，暂时没发现值得说的事。")
        return
    labels = {"alert": "要处理", "warn": "留意", "info": "看看", "praise": "不错"}
    for finding in findings:
        print(f"[{labels.get(finding['severity'], finding['severity'])}] {finding['message']}")
        action = finding.get("action")
        if action:
            print(f"    → {action}")


def doctor_command(args):
    ledger = load_ledger(args.ledger, create=False)
    report = doctor_report(ledger)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("账本体检: 通过" if report["ok"] else f"账本体检: {report['issue_count']} 个问题")
    for issue in report["issues"]:
        tx_id = issue.get("id") or "-"
        print(f"- {issue['code']} | {tx_id} | {issue['message']}")


@json_command
def import_tsv_command(args):
    from ledger_core.services.imports import import_legacy
    return import_legacy(args.ledger, args.input, currency=args.currency,
                         allow_duplicate=args.allow_duplicate, confirmed_image=getattr(args, "confirmed_image", False))


def export_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows, period = filter_from_args(ledger["transactions"], args)
    rows.sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    fields = [
        "id",
        "occurred_at",
        "type",
        "amount",
        "currency",
        "category",
        "merchant",
        "method",
        "account",
        "to_account",
        "note",
        "tags",
        "source",
        "confidence",
    ]
    if args.format == "json":
        payload = json.dumps({"period": period, "count": len(rows), "transactions": rows}, ensure_ascii=False, indent=2)
    else:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for tx in rows:
            row = {field: tx.get(field, "") for field in fields}
            row["tags"] = ",".join(coerce_tags(tx.get("tags")))
            writer.writerow(row)
        payload = buffer.getvalue()
    if args.output:
        output = Path(args.output)
        write_text_secure(output, payload)
        print(str(output.resolve()))
        return
    sys.stdout.write(payload if payload.endswith("\n") else payload + "\n")


from ledger_core.services.maintenance import prefer_command as _prefer_command
prefer_command = json_command(_prefer_command)


def category_list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    entries = category_entries(ledger)
    if args.json:
        print(json.dumps({"categories": entries}, ensure_ascii=False, indent=2))
        return
    if not entries:
        print("还没有自定义分类；内置分类可直接使用。")
        return
    for item in entries:
        parent = f" -> {item['parent']}" if item.get("parent") else ""
        icon = f"{item['icon']} " if item.get("icon") else ""
        print(f"{icon}{item['name']}{parent}")


def review_refresh_command(args):
    from ledger_core.services.maintenance import review_refresh_command as run
    payload = run(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"新增 {payload['added']} 项待复核，当前共 {payload['open_count']} 项。")
    for item in payload['open'][:10]:
        print(f"- [{item.get('code')}] {item.get('transaction_id')} · {item.get('detail')}")


def review_next_command(args):
    ledger = load_ledger(args.ledger, create=True)
    items = build_review_queue(ledger)
    combined = items
    priority = {"category_type_mismatch": 0, "unmatched_refund": 1, "low_confidence": 2}
    combined.sort(key=lambda item: (priority.get(item.get("code"), 9), item.get("occurred_at") or ""))
    if args.json:
        payload = {"item": combined[0] if combined else None, "remaining": len(combined)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if not combined:
        print("没有待复核项。")
        return
    item = combined[0]
    print(f"[{item.get('code')}] {item.get('transaction_id')} · {item.get('detail')}")
    print(f"建议: {item.get('action')}（共 {len(combined)} 项待处理）")


def review_notifications_command(args):
    from ledger_core.services.findings import notifications
    from ledger_core.storage.documents import ledger_lock
    with ledger_lock(args.ledger):
        ledger = load_ledger(args.ledger, create=False)
        receipt = json.loads(Path(args.ack).read_text()) if args.ack else None
        result = notifications(args.ledger, build_review_queue(ledger), acknowledge=receipt)
    print(json.dumps(result, ensure_ascii=False, indent=2))


from ledger_core.services.maintenance import review_resolve_command as _review_resolve_command
review_resolve_command = json_command(_review_resolve_command)


def advance_list_command(args):
    ledger = load_ledger(args.ledger, create=True)
    rows = [
        tx
        for tx in ledger["transactions"]
        if isinstance(tx, dict) and tx.get("advance") in VALID_ADVANCE_STATES
    ]
    if not getattr(args, "all", False):
        rows = [tx for tx in rows if tx.get("advance") == "open"]
    items = [
        {
            **compact_candidate(tx),
            "advance": tx.get("advance"),
            "remaining": advance_remaining(tx),
            "settled": advance_settled_amount(tx),
        }
        for tx in rows
    ]
    if getattr(args, "json", False):
        print(json.dumps({"advances": items, "count": len(items)}, ensure_ascii=False, indent=2))
        return
    if not items:
        print("没有待收的垫付款。")
        return
    open_items = [item for item in items if item["advance"] == "open"]
    lines = [
        f"待收垫付 {len(open_items)} 笔，合计 {money.format_money(sum(item['remaining'] for item in open_items), ledger.get('currency'))}"
    ]
    states = {"open": "待收", "settled": "已收回", "written_off": "已核销"}
    for item in items:
        date = (item.get("occurred_at") or "")[:10]
        label = item.get("merchant") or item.get("note") or item.get("id")
        state = states.get(item["advance"], item["advance"])
        if item["advance"] == "open":
            detail = f"待收 {money.format_money(item['remaining'], ledger.get('currency'))}"
        else:
            detail = f"{state} {money.format_money(item['settled'], ledger.get('currency'))}"
        lines.append(f"- {date} {label} {detail} · {item.get('id')}")
    print("\n".join(lines))


from ledger_core.services.maintenance import advance_settle_command as _advance_settle_command
advance_settle_command = json_command(_advance_settle_command)


from ledger_core.services.maintenance import advance_writeoff_command as _advance_writeoff_command
advance_writeoff_command = json_command(_advance_writeoff_command)


def habit_list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    payload = habits_payload(ledger)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    summary = (payload.get("profile") or {}).get("summary") or ""
    if summary:
        print(summary)
        return
    print("按原始信息记账；没有确认的默认账户时保留未分配，缺金额时询问。")


def habit_profile_command(args):
    from ledger_core.services.maintenance import habit_profile_command as run
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else (payload.get("profile") or {}).get("summary") or "")


from ledger_core.services.maintenance import habit_memory_command as _habit_memory_command
habit_memory_command = json_command(_habit_memory_command)


from ledger_core.services.maintenance import habit_rebuild_command as _habit_rebuild_command
habit_rebuild_command = json_command(_habit_rebuild_command)


def account_list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows = account_balances(ledger)
    payload = {
        "default_account_id": ledger_preferences(ledger).get("default_account_id"),
        "accounts": rows,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    default_id = payload["default_account_id"]
    for item in rows:
        mark = " *" if item["id"] == default_id else ""
        print(f"{item['id']} | {item['name']} | {item['type']} | {item['balance']:.2f}{mark}")


def budget_list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    period = resolve_period(month=args.month, range_name=None if args.month else "this-month")
    rows = filter_transactions(ledger["transactions"], start=period["start"], end=period["end"])
    payload = budget_progress(ledger, rows, period)
    if args.json:
        print(json.dumps({"period": period, "budgets": payload}, ensure_ascii=False, indent=2))
        return
    if not payload:
        print(f"{period.get('label')}没有预算。")
        return
    for item in payload:
        status = "超支" if item["over"] else "剩余"
        print(
            f"{item['category']} | 限额 {item['limit']:.2f} | 已花 {item['spent']:.2f} | {status} {abs(item['remaining']):.2f}"
        )


from ledger_core.services.maintenance import backup_command as _backup_command
backup_command = json_command(_backup_command)


def init_command(args):
    from ledger_core.services.maintenance import init_command as run
    result = run(args)
    if result.get("backup"): print("Backed up existing ledger to " + result["backup"])
    print(("Ledger already exists: " if result.get("exists") else "") + result["path"])

# Preserve output adapters while exposing return-value services to HTTP.
from ledger_core.services.mutations import bill_save_command as _bill_save_command
bill_save_command = json_command(_bill_save_command)
from ledger_core.services.mutations import update_command as _update_command
update_command = json_command(_update_command)
from ledger_core.services.mutations import delete_command as _delete_command
delete_command = json_command(_delete_command)
from ledger_core.services.mutations import category_set_command as _category_set_command
category_set_command = json_command(_category_set_command)
from ledger_core.services.mutations import habit_set_command as _habit_set_command
habit_set_command = json_command(_habit_set_command)
from ledger_core.services.mutations import habit_delete_command as _habit_delete_command
habit_delete_command = json_command(_habit_delete_command)
from ledger_core.services.mutations import account_add_command as _account_add_command
account_add_command = json_command(_account_add_command)
from ledger_core.services.mutations import account_update_command as _account_update_command
account_update_command = json_command(_account_update_command)
from ledger_core.services.mutations import budget_set_command as _budget_set_command
budget_set_command = json_command(_budget_set_command)
from ledger_core.services.mutations import budget_delete_command as _budget_delete_command
budget_delete_command = json_command(_budget_delete_command)

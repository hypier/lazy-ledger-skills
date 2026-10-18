"""Report composition and output formatting."""
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from ledger_db import write_text_secure
from ledger_core.domain.model import ledger_preferences
from ledger_core.domain.metrics import tx_day, month_key, category_parent_map
from ledger_core.domain.semantics import counts_as

from ledger_core.domain.vocabulary import (METHOD_LABELS, VIEWER_PRIVATE_FIELDS)
from ledger_core.services.analysis import (build_summary, format_money, format_pct)
from ledger_core.services.common import (last_day_of_month, local_today, safe_amount, validate_month)
from ledger_core.services.habits import (ledger_stem)


def available_months(ledger):
    months = {
        month_key(tx.get("occurred_at"))
        for tx in ledger.get("transactions") or []
        if isinstance(tx, dict) and month_key(tx.get("occurred_at"))
    }
    return sorted(months, reverse=True)


def default_bill_month(ledger, month=None):
    if month:
        return validate_month(month)
    today = local_today().strftime("%Y-%m")
    months = available_months(ledger)
    if today in months or not months:
        return today
    return months[0]


def find_bill(ledger, month):
    for bill in ledger.get("bills") or []:
        if isinstance(bill, dict) and bill.get("month") == month:
            return bill
    return None


def build_bill_pack(ledger, month=None):
    month = default_bill_month(ledger, month)
    args = SimpleNamespace(
        month=month,
        range=None,
        start=None,
        end=None,
        query=None,
        type=None,
        category=None,
        method=None,
        account=None,
        compare=True,
    )
    summary = build_summary(ledger, args)
    category_parents = category_parent_map(ledger)
    category_counts = defaultdict(int)
    daily_expense = defaultdict(float)
    weekly_expense = defaultdict(float)
    weekly_counts = defaultdict(int)
    for transaction in ledger.get("transactions") or []:
        if not counts_as(transaction, "expense"):
            continue
        if month_key(transaction.get("occurred_at")) != month or transaction.get("type") != "expense":
            continue
        category = transaction.get("category")
        if not isinstance(category, str) or not category:
            category = "其他"
        category_counts[category_parents.get(category, category)] += 1
        day = tx_day(transaction)
        if not day:
            continue
        try:
            day_number = int(day[-2:])
        except (TypeError, ValueError):
            continue
        amount = safe_amount(transaction.get("amount"))
        daily_expense[day_number] += amount
        week_index = (day_number - 1) // 7
        weekly_expense[week_index] += amount
        weekly_counts[week_index] += 1
    year, month_number = (int(part) for part in month.split("-"))
    next_month = date(year + (month_number == 12), month_number % 12 + 1, 1)
    days_in_month = (next_month - date(year, month_number, 1)).days
    daily_series = [
        {"day": day_number, "amount": round(daily_expense[day_number], 2)}
        for day_number in range(1, days_in_month + 1)
    ]
    weekly_series = []
    for week_index, start_day in enumerate(range(1, days_in_month + 1, 7)):
        end_day = min(start_day + 6, days_in_month)
        weekly_series.append(
            {
                "label": f"{start_day:02d}-{end_day:02d}",
                "amount": round(weekly_expense[week_index], 2),
                "count": weekly_counts[week_index],
            }
        )
    profile = ledger_preferences(ledger).get("usage_profile") or {}
    saved = find_bill(ledger, month)
    currency = ledger.get("currency", "CNY")
    brief = (
        f"根据 {month} 的记账事实写一份月度账单，给用户看，不是给会计看。"
        "先写这月怎么花钱、习惯有没有变，再写和上月的差别，最后一句下月可以留意的事。"
        "不要说教，不要编造账本里没有的数字。正文大约 400 到 800 字，结构可以像一封短信，不必套表格。"
    )
    return {
        "month": month,
        "months": available_months(ledger),
        "currency": currency,
        "facts": {
            "period": summary.get("period"),
            "totals": summary.get("totals"),
            "count": summary.get("count"),
            "daily_average": summary.get("daily_average"),
            "by_category": summary.get("by_category"),
            "category_counts": dict(category_counts),
            "by_day": summary.get("by_day"),
            "daily_expense": daily_series,
            "weekly_expense": weekly_series,
            "by_weekday": summary.get("by_weekday"),
            "weekday": summary.get("weekday"),
            "top_merchants": summary.get("top_merchants"),
            "comparison": summary.get("comparison"),
            "budgets": summary.get("budgets"),
            "recurring": summary.get("recurring"),
            "outliers": summary.get("outliers"),
            "insights": summary.get("insights"),
            "pace": summary.get("pace"),
            "pending_reimbursement": summary.get("pending_reimbursement"),
        },
        "habit_summary": profile.get("summary"),
        "brief": brief,
        "bill": saved,
    }


def render_bill_pack(pack, currency="CNY"):
    month = pack.get("month")
    facts = pack.get("facts") or {}
    totals = facts.get("totals") or {}
    saved = pack.get("bill") or {}
    lines = [f"# {month} 月度账单", ""]
    if saved.get("body"):
        title = saved.get("title")
        if title:
            lines.extend([f"**{title}**", ""])
        lines.append(saved["body"].rstrip())
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "还没有文字结论。用下面的事实写完后执行 `bill save`。",
            "",
            f"支出 {format_money(totals.get('expense') or 0, currency)} · 收入 {format_money(totals.get('income') or 0, currency)} · 共 {facts.get('count') or 0} 笔",
        ]
    )
    comparison = facts.get("comparison") or {}
    if comparison:
        lines.append(
            f"较{comparison.get('previous_label') or '上月'}：支出 {format_pct(comparison.get('expense_change_pct'))}"
        )
    if facts.get("insights"):
        lines.extend(["", "## 事实要点"])
        for item in facts["insights"]:
            lines.append(f"- {item}")
    if pack.get("habit_summary"):
        lines.extend(["", "## 记账习惯", pack["habit_summary"]])
    lines.extend(["", "## 写作要求", pack.get("brief") or ""])
    return "\n".join(lines) + "\n"


def default_bill_html_path(ledger_path, month):
    path = Path(ledger_path)
    target = path.parent / f"{ledger_stem(path)}-bill-{month}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def bill_canvas_payload(pack):
    return {
        "month": pack.get("month"),
        "currency": pack.get("currency", "CNY"),
        "facts": pack.get("facts") or {},
        "bill": pack.get("bill"),
    }


def attach_bill_file(pack, ledger_path):
    month = pack.get("month")
    path = default_bill_html_path(ledger_path, month)
    attached = dict(pack)
    attached["file"] = {
        "path": str(path.resolve()),
        "name": path.name,
        "exists": path.exists(),
    }
    return attached


def write_bill_html(pack, output_path, template_path=None):
    from .reports import html_document
    if template_path:
        raise ValueError("Fixed templates are retired; use report build and a verified renderer")
    output = Path(output_path)
    write_text_secure(output, html_document(render_bill_pack(pack)))
    return output.resolve()


def render_bill_html(ledger, ledger_path, month=None, output_path=None, template_path=None):
    from . import reports
    if template_path:
        raise ValueError("Fixed templates are retired; use report build")
    pack = build_bill_pack(ledger, month)
    chosen = pack["month"]
    end = last_day_of_month(*map(int, chosen.split("-")))
    report = reports.from_ledger(ledger, start=chosen+"-01", end=f"{chosen}-{end:02d}")
    text = reports.render(report, "markdown")
    saved = find_bill(ledger, chosen)
    if saved and saved.get("body"):
        text += "\n## 已保存的月报文字（请结合当前数据复核）\n\n" + saved["body"] + "\n"
    dest = Path(output_path) if output_path else default_bill_html_path(ledger_path, chosen)
    write_text_secure(dest, reports.html_document(text))
    return pack, dest.resolve()


def format_list_row(tx):
    date = tx_day(tx)
    merchant = tx.get("merchant") or "-"
    account = tx.get("account") or "-"
    dest = f"→{tx.get('to_account')}" if tx.get("to_account") else ""
    method = METHOD_LABELS.get(tx.get("method"), tx.get("method") or "-")
    note = tx.get("note") or ""
    amount = safe_amount(tx.get("amount"))
    return (
        f"{tx.get('id')} | {date} | {tx.get('type')} | {tx.get('category')} | "
        f"{amount:.2f} | {merchant} | {account}{dest} | {method} | {note}"
    )


def viewer_ledger_data(ledger):
    # A static report may be shared. Keep the visual data, but do not embed
    # local attachment paths or authoritative statement references.
    data = json.loads(json.dumps(ledger, ensure_ascii=False))
    for tx in data.get("transactions") or []:
        if isinstance(tx, dict):
            for field in VIEWER_PRIVATE_FIELDS:
                tx.pop(field, None)
    return data

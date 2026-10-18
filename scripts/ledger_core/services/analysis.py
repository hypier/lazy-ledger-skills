"""Period queries, evidence matching and analysis projections."""
from ledger_core.domain import money
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from ledger_core.domain.model import default_currency
from ledger_core.domain.metrics import account_balances, budget_progress, summarize, tx_day, month_key, compact_candidate, category_parent_map
from ledger_core.domain.semantics import counts_as
from ledger_core.services.findings import active_insights as build_findings

from ledger_core.domain.vocabulary import (METHOD_LABELS, REIMBURSED_TAGS, REIMBURSE_TAGS)
from ledger_core.services.common import (advance_remaining, apply_advance_settlement, last_day_of_month, local_today, safe_amount, validate_day, validate_month)
from ledger_core.services.interpretation import (classify_event_detailed, normalized_text)


def month_snapshot(ledger, month):
    rows = [
        tx
        for tx in ledger.get("transactions", [])
        if isinstance(tx, dict) and month_key(tx.get("occurred_at")) == month
    ]
    period = {"label": month, "start": f"{month}-01" if month else None, "end": None, "month": month}
    data = summarize(rows, period=period, category_parents=category_parent_map(ledger))
    data["budgets"] = budget_progress(ledger, rows, period)
    return {
        "key": month,
        "count": data["count"],
        "totals": data["totals"],
        "daily_average": data["daily_average"],
        "budgets": data["budgets"],
    }


def shift_month(year, month, delta):
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def shift_date_years(value, delta):
    try:
        return value.replace(year=value.year + delta)
    except ValueError:
        return date(value.year + delta, 2, 28)


def quarter_index(month):
    return (month - 1) // 3


def resolve_period(month=None, range_name=None, start=None, end=None, now=None):
    today = (now or local_today()).date()
    if start:
        start = validate_day(start, label="start date")
    if end:
        end = validate_day(end, label="end date")
    if start and end and start > end:
        raise SystemExit(f"Start date {start} is after end date {end}")
    if month:
        month = validate_month(month)
        year, mon = int(month[:4]), int(month[5:7])
        last = last_day_of_month(year, mon)
        return {
            "label": month,
            "start": f"{month}-01",
            "end": f"{month}-{last:02d}",
            "month": month,
        }
    if range_name == "this-month":
        month = today.strftime("%Y-%m")
        return {
            "label": "本月",
            "kind": "this-month",
            "start": today.replace(day=1).isoformat(),
            "end": today.isoformat(),
            "month": month,
        }
    if range_name == "last-month":
        year, mon = shift_month(today.year, today.month, -1)
        last = last_day_of_month(year, mon)
        month = f"{year:04d}-{mon:02d}"
        return {
            "label": "上月",
            "start": f"{month}-01",
            "end": f"{month}-{last:02d}",
            "month": month,
        }
    if range_name == "last7":
        start_day = today - timedelta(days=6)
        return {"label": "近7天", "start": start_day.isoformat(), "end": today.isoformat(), "month": None}
    if range_name == "last30":
        start_day = today - timedelta(days=29)
        return {"label": "近30天", "start": start_day.isoformat(), "end": today.isoformat(), "month": None}
    if range_name == "this-quarter":
        start_month = quarter_index(today.month) * 3 + 1
        start_day = date(today.year, start_month, 1)
        qn = quarter_index(today.month) + 1
        return {
            "label": f"{today.year} Q{qn}",
            "start": start_day.isoformat(),
            "end": today.isoformat(),
            "month": None,
            "kind": "this-quarter",
        }
    if range_name == "last-quarter":
        start_month = quarter_index(today.month) * 3 + 1
        year, mon = shift_month(today.year, start_month, -3)
        last = last_day_of_month(year, mon + 2)
        qn = quarter_index(mon) + 1
        return {
            "label": f"{year} Q{qn}",
            "start": date(year, mon, 1).isoformat(),
            "end": date(year, mon + 2, last).isoformat(),
            "month": None,
            "kind": "last-quarter",
        }
    if range_name == "this-year":
        return {
            "label": f"{today.year}年至今",
            "start": date(today.year, 1, 1).isoformat(),
            "end": today.isoformat(),
            "month": None,
            "kind": "this-year",
        }
    if range_name == "last-year":
        year = today.year - 1
        return {
            "label": f"{year}年",
            "start": date(year, 1, 1).isoformat(),
            "end": date(year, 12, 31).isoformat(),
            "month": None,
            "kind": "last-year",
        }
    if start or end:
        label = f"{start or '…'} ~ {end or '…'}"
        return {"label": label, "start": start, "end": end, "month": None}
    return {"label": "全部", "start": None, "end": None, "month": None}


def previous_period(period):
    start = period.get("start")
    end = period.get("end")
    kind = period.get("kind")
    if kind in {"this-year", "last-year"} and start and end:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        prev_start = shift_date_years(start_d, -1)
        prev_end = shift_date_years(end_d, -1)
        label = f"{prev_start.year}年至今" if kind == "this-year" else f"{prev_start.year}年"
        return {"label": label, "start": prev_start.isoformat(), "end": prev_end.isoformat(), "month": None, "kind": kind}
    if kind in {"this-quarter", "last-quarter"} and start and end:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        year, mon = shift_month(start_d.year, start_d.month, -3)
        prev_start = date(year, mon, 1)
        end_year, end_month = shift_month(year, mon, 2)
        quarter_end = date(end_year, end_month, last_day_of_month(end_year, end_month))
        prev_end = quarter_end if kind == "last-quarter" else min(quarter_end, prev_start + timedelta(days=(end_d - start_d).days))
        qn = quarter_index(mon) + 1
        return {
            "label": f"{year} Q{qn}",
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "month": None,
            "kind": kind,
        }
    if period.get("month"):
        year, mon = shift_month(int(period["month"][:4]), int(period["month"][5:7]), -1)
        last = last_day_of_month(year, mon)
        month = f"{year:04d}-{mon:02d}"
        if kind == "this-month":
            last = min(last, int(end[-2:]))
        return {"label": month + (f"同期（截至{last}日）" if kind == "this-month" else ""), "start": f"{month}-01", "end": f"{month}-{last:02d}", "month": month}
    if start and end:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        days = (end_d - start_d).days + 1
        prev_end = start_d - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        return {
            "label": f"{prev_start.isoformat()} ~ {prev_end.isoformat()}",
            "start": prev_start.isoformat(),
            "end": prev_end.isoformat(),
            "month": None,
        }
    return None


def filter_transactions(
    transactions,
    month=None,
    query=None,
    tx_type=None,
    category=None,
    method=None,
    start=None,
    end=None,
    account=None,
    category_parents=None,
):
    rows = []
    q = (query or "").lower()
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        day = tx_day(tx)
        if month and month_key(tx.get("occurred_at")) != month:
            continue
        if start and (not day or day < start):
            continue
        if end and (not day or day > end):
            continue
        if tx_type and tx.get("type") != tx_type:
            continue
        tx_category = tx.get("category")
        if category:
            if not isinstance(tx_category, str):
                continue
            if tx_category != category and (category_parents or {}).get(tx_category) != category:
                continue
        if method and tx.get("method") != method:
            continue
        if account:
            haystack = {
                value
                for value in (tx.get("account_id"), tx.get("account"), tx.get("to_account_id"), tx.get("to_account"))
                if isinstance(value, str)
            }
            if account not in haystack:
                continue
        if q:
            haystack = " ".join(
                str(tx.get(k, ""))
                for k in ("category", "merchant", "note", "amount", "type", "method", "account", "to_account", "tags")
            ).lower()
            if q not in haystack:
                continue
        rows.append(tx)
    return rows


def filter_from_args(transactions, args, category_parents=None):
    period = resolve_period(
        month=getattr(args, "month", None),
        range_name=getattr(args, "range", None),
        start=getattr(args, "start", None),
        end=getattr(args, "end", None),
    )
    rows = filter_transactions(
        transactions,
        month=None,
        query=getattr(args, "query", None),
        tx_type=getattr(args, "type", None),
        category=getattr(args, "category", None),
        method=getattr(args, "method", None),
        start=period["start"],
        end=period["end"],
        account=getattr(args, "account", None),
        category_parents=category_parents,
    )
    return rows, period


def tx_tags(tx):
    tags = tx.get("tags") or []
    if isinstance(tags, str):
        return {part.strip() for part in tags.split(",") if part.strip()}
    if not isinstance(tags, (list, tuple, set)):
        return set()
    return {str(item).strip() for item in tags if str(item).strip()}


def detect_recurring(transactions, today=None, lookback_months=8):
    today = (today or local_today()).date()
    cutoff_year, cutoff_month = shift_month(today.year, today.month, -lookback_months)
    cutoff = f"{cutoff_year:04d}-{cutoff_month:02d}-01"
    groups = defaultdict(list)
    for tx in transactions:
        if not counts_as(tx, "expense"):
            continue
        day = tx_day(tx)
        if not day or day < cutoff:
            continue
        merchant = normalized_text(tx.get("merchant") or "")
        if merchant:
            key = ("merchant", merchant)
        else:
            amount_key = round(safe_amount(tx.get("amount")))
            if amount_key <= 0:
                continue
            category = tx.get("category")
            if not isinstance(category, str) or not category:
                category = "其他"
            key = ("category", category, amount_key)
        groups[key].append(tx)

    current_month = today.strftime("%Y-%m")
    rows = []
    for items in groups.values():
        dated = []
        for tx in items:
            day = tx_day(tx)
            if not day:
                continue
            try:
                dated.append((datetime.strptime(day, "%Y-%m-%d").date(), tx))
            except ValueError:
                continue
        dated.sort(key=lambda item: item[0])
        if len(dated) < 3:
            continue
        months = sorted({value.strftime("%Y-%m") for value, _tx in dated})
        if len(months) < 3:
            continue
        amounts = [safe_amount(tx.get("amount")) for _day, tx in dated]
        median_amount = sorted(amounts)[len(amounts) // 2]
        if median_amount <= 0:
            continue
        spread = (max(amounts) - min(amounts)) / median_amount
        if spread > 0.35:
            continue
        gaps = [(dated[index][0] - dated[index - 1][0]).days for index in range(1, len(dated))]
        median_gap = sorted(gaps)[len(gaps) // 2] if gaps else 0
        if median_gap < 5:
            continue
        if 25 <= median_gap <= 40:
            cadence = "monthly"
        elif 6 <= median_gap <= 9:
            cadence = "weekly"
        elif 13 <= median_gap <= 17:
            cadence = "biweekly"
        elif len(months) >= 3 and len(dated) <= len(months) + 1:
            cadence = "monthly"
        else:
            continue
        last_date = dated[-1][0]
        sample = dated[-1][1]
        recorded_this_month = any(value.strftime("%Y-%m") == current_month for value, _tx in dated)
        expected_day = sorted(value.day for value, _tx in dated)[len(dated) // 2]
        missing = cadence == "monthly" and not recorded_this_month and today.day >= max(expected_day - 3, 1)
        rows.append(
            {
                "label": sample.get("merchant") or sample.get("category") or "周期支出",
                "cadence": cadence,
                "typical_amount": round(median_amount, 2),
                "months": len(months),
                "count": len(dated),
                "last_date": last_date.isoformat(),
                "expected_day": expected_day,
                "recorded_this_month": recorded_this_month,
                "missing_this_month": missing,
                "category": sample.get("category"),
            }
        )
    rows.sort(key=lambda item: (not item["missing_this_month"], -item["typical_amount"]))
    return rows[:12]


def match_refund_to_expense(refund, expenses):
    """Return the expense a refund most likely reverses, or None.

    Deterministic single-candidate match within 90 days on a matching amount and
    a compatible merchant. Returns None when nothing matches; callers decide
    whether an ambiguous result should be surfaced instead of linked.
    """
    refund_day = tx_day(refund)
    try:
        refund_amount = float(refund.get("amount") or 0)
        refund_date = datetime.strptime(refund_day, "%Y-%m-%d").date() if refund_day else None
    except (TypeError, ValueError):
        return None
    if not refund_date:
        return None
    refund_merchant = normalized_text(refund.get("merchant") or "")
    candidates = []
    for expense in expenses:
        expense_day = tx_day(expense)
        if not expense_day:
            continue
        try:
            expense_date = datetime.strptime(expense_day, "%Y-%m-%d").date()
        except ValueError:
            continue
        if expense_date > refund_date or (refund_date - expense_date).days > 90:
            continue
        expense_amount = safe_amount(expense.get("amount"))
        amount_ok = abs(expense_amount - refund_amount) < 0.01 or (
            expense_amount and abs(expense_amount - refund_amount) / expense_amount < 0.05
        )
        if not amount_ok:
            continue
        expense_merchant = normalized_text(expense.get("merchant") or "")
        merchant_ok = (
            not refund_merchant
            or not expense_merchant
            or refund_merchant in expense_merchant
            or expense_merchant in refund_merchant
        )
        if merchant_ok:
            candidates.append(expense)
    if len(candidates) == 1:
        return candidates[0]
    return None


def unmatched_refunds(transactions, period_rows):
    expenses = [
        tx
        for tx in transactions
        if isinstance(tx, dict) and not tx.get("excluded_from_totals") and tx.get("type") == "expense"
    ]
    rows = []
    for refund in period_rows:
        if not isinstance(refund, dict) or refund.get("excluded_from_totals") or refund.get("type") != "refund":
            continue
        if refund.get("related_transaction_id"):
            continue
        if match_refund_to_expense(refund, expenses) is not None:
            # A confident match exists, so it is not "unmatched"; the caller
            # links it automatically instead of asking the user.
            continue
        rows.append(compact_candidate(refund))
    return rows[:8]


def link_unmatched_refunds(ledger, transactions, period_rows):
    """Attach confident refund->expense relations in place.

    Previously a confident match only suppressed the warning and left
    related_transaction_id empty, so refunds were never actually linked. Linking
    is reversible and does not change type, amount, account, or date, which is
    the same risk class the skill already applies automatically.
    """
    expenses = [
        tx
        for tx in transactions
        if isinstance(tx, dict) and not tx.get("excluded_from_totals") and tx.get("type") == "expense"
    ]
    linked = []
    for refund in period_rows:
        if not isinstance(refund, dict) or refund.get("type") != "refund":
            continue
        if refund.get("related_transaction_id"):
            continue
        expense = match_refund_to_expense(refund, expenses)
        if expense is None or not expense.get("id"):
            continue
        refund["related_transaction_id"] = expense["id"]
        refund["relation"] = "refund"
        linked.append(
            {
                "refund_id": refund.get("id"),
                "expense_id": expense.get("id"),
                "amount": safe_amount(refund.get("amount")),
            }
        )
    return linked


def pending_reimbursements(transactions):
    """Advances still owed back, whether tagged or stored as advance state."""
    rows = []
    for tx in transactions:
        if not isinstance(tx, dict) or tx.get("excluded_from_totals") or tx.get("type") != "expense":
            continue
        if tx.get("advance") in {"settled", "written_off"}:
            continue
        tags = tx_tags(tx)
        if tags & REIMBURSED_TAGS:
            continue
        if tx.get("advance") == "open":
            rows.append(compact_candidate(tx))
            continue
        if tags & REIMBURSE_TAGS:
            rows.append(compact_candidate(tx))
    return rows[:12]


def open_advances(transactions):
    return [
        tx
        for tx in transactions
        if isinstance(tx, dict) and tx.get("type") == "expense" and tx.get("advance") == "open"
    ]


def match_income_to_advance(income, advances):
    """Return the single open advance an income row most likely settles."""
    decision = classify_event_detailed(income.get("note") or "")
    words = income.get("note") or ""
    repayment = bool(re.search(r"报销|还我|还给我|还回来|还清|偿还", words)) and not any(word in words for word in ("工资", "奖金", "利息"))
    if not repayment or decision["type"] != "income":
        return None
    income_day = tx_day(income)
    income_amount = safe_amount(income.get("amount"))
    if not income_day or not income_amount:
        return None
    candidates = []
    for advance in advances:
        advance_day = tx_day(advance)
        if not advance_day or advance_day > income_day:
            continue
        if advance.get("currency") == income.get("currency") and abs(advance_remaining(advance) - income_amount) < 0.01:
            candidates.append(advance)
    if len(candidates) == 1:
        return candidates[0]
    return None


def settle_advances(ledger, transactions, income_rows):
    """Settle open advances with matching reimbursement income rows.

    Kept separate from refund linking because the evidence is different: a
    reimbursement is an income row equal to the outstanding advance amount.
    """
    linked = []
    if not income_rows:
        return linked
    advances = open_advances(transactions)
    for income in income_rows:
        if not isinstance(income, dict) or income.get("type") != "income":
            continue
        if income.get("related_transaction_id"):
            continue
        advance = match_income_to_advance(income, advances)
        if advance is None:
            continue
        if apply_advance_settlement(advance, income):
            linked.append(
                {
                    "income_id": income.get("id"),
                    "advance_id": advance.get("id"),
                    "amount": safe_amount(income.get("amount")),
                    "remaining": advance_remaining(advance),
                }
            )
            if advance.get("advance") != "open":
                advances = [row for row in advances if row is not advance]
    return linked


def month_trend(transactions, end_date=None, months=6):
    today = end_date or (local_today()).date()
    keys = []
    year, month = today.year, today.month
    for offset in range(months - 1, -1, -1):
        item_year, item_month = shift_month(year, month, -offset)
        keys.append(f"{item_year:04d}-{item_month:02d}")
    totals = {key: 0.0 for key in keys}
    for tx in transactions:
        if not counts_as(tx, "expense"):
            continue
        key = month_key(tx.get("occurred_at"))
        if key in totals:
            totals[key] += safe_amount(tx.get("amount"))
    return [{"month": key, "expense": round(totals[key], 2)} for key in keys]


def spending_pace(period, daily_average, budgets, today=None):
    today = (today or local_today()).date()
    month = period.get("month")
    kind = period.get("kind")
    if month:
        year, mon = int(month[:4]), int(month[5:7])
        month_days = last_day_of_month(year, mon)
        month_start = date(year, mon, 1)
        month_end = date(year, mon, month_days)
        cursor = min(max(today, month_start), month_end)
        elapsed = (cursor - month_start).days + 1
        remaining_days = max((month_end - cursor).days, 0)
        projected = round(daily_average * month_days, 2)
        payload = {
            "scope": "month",
            "elapsed_days": elapsed,
            "total_days": month_days,
            "remaining_days": remaining_days,
            "projected_expense": projected,
            "complete": remaining_days == 0 or today >= month_end,
        }
        overall = next((item for item in budgets or [] if item.get("category") == "本月总额"), None)
        if overall:
            payload["budget_limit"] = overall["limit"]
            payload["spent"] = overall["spent"]
            payload["projected_remaining"] = round(overall["limit"] - projected, 2)
            payload["on_track"] = projected <= overall["limit"] + 0.009
            payload["spent_pct"] = overall.get("pct")
        return payload
    if kind == "this-year" and period.get("start"):
        year_start = datetime.strptime(period["start"], "%Y-%m-%d").date()
        year_end = date(year_start.year, 12, 31)
        elapsed = (min(today, year_end) - year_start).days + 1
        remaining_days = max((year_end - today).days, 0)
        projected = round(daily_average * ((year_end - year_start).days + 1), 2)
        return {
            "scope": "year",
            "elapsed_days": elapsed,
            "total_days": (year_end - year_start).days + 1,
            "remaining_days": remaining_days,
            "projected_expense": projected,
            "complete": remaining_days == 0,
        }
    return None


def build_insight_lines(data, currency="CNY"):
    lines = []
    totals = data.get("totals") or {}
    expense = float(totals.get("expense") or 0)
    categories = data.get("by_category") or {}
    if categories and expense:
        top_name, top_amount = next(iter(categories.items()))
        share = top_amount / expense * 100
        lines.append(f"支出最多的是{top_name}，占 {share:.0f}%。")
    comparison = data.get("comparison") or {}
    change = comparison.get("expense_change_pct")
    if change is not None:
        previous = comparison.get("previous_label") or "上期"
        if change > 8:
            lines.append(f"比{previous}多花了 {abs(change):.0f}%。")
        elif change < -8:
            lines.append(f"比{previous}少花了 {abs(change):.0f}%。")
    pace = data.get("pace") or {}
    if pace.get("budget_limit") is not None and not pace.get("complete"):
        if pace.get("on_track") is False:
            lines.append(
                f"按现在日均，月底大约 {money.format_money(pace.get('projected_expense'), currency)}，可能超预算。"
            )
        elif pace.get("projected_remaining", 0) > 0:
            lines.append(
                f"按现在日均，月底大约还能剩 {money.format_money(pace.get('projected_remaining'), currency)}。"
            )
    elif pace and pace.get("scope") == "year" and not pace.get("complete") and expense:
        lines.append(f"按现在日均，今年大约会花 {money.format_money(pace.get('projected_expense'), currency)}。")
    missing = [item for item in data.get("recurring") or [] if item.get("missing_this_month")]
    if missing:
        names = "、".join(item["label"] for item in missing[:3])
        lines.append(f"周期账这月还没记：{names}。")
    pending = data.get("pending_reimbursement") or []
    if pending:
        total = sum(float(item.get("amount") or 0) for item in pending)
        lines.append(f"待报销 {len(pending)} 笔，共 {money.format_money(total, currency)}。")
    unmatched = data.get("unmatched_refunds") or []
    if unmatched:
        lines.append(f"有 {len(unmatched)} 笔退款对不上近期支出。")
    weekday = data.get("weekday") or {}
    weekend = float(weekday.get("weekend") or 0)
    if expense and weekend / expense >= 0.4:
        lines.append(f"周末支出占 {weekend / expense * 100:.0f}%。")
    return lines[:4]


def format_pct(value):
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def render_show(data, currency="CNY"):
    period = data.get("period") or {}
    totals = data["totals"]
    lines = [
        f"## {period.get('label') or '全部'}",
        "",
        f"支出 **{money.format_money(totals['expense'], currency)}** · 收入 {money.format_money(totals['income'], currency)} · 退款 {money.format_money(totals['refund'], currency)} · 净额 **{money.format_money(totals['net'], currency)}**",
        f"共 {data['count']} 笔 · 日均支出 {money.format_money(data['daily_average'], currency)}",
    ]
    comparison = data.get("comparison")
    if comparison:
        lines.append(
            f"较{comparison.get('previous_label') or '上期'}：支出 {format_pct(comparison.get('expense_change_pct'))} · 净额 {format_pct(comparison.get('net_change_pct'))}"
        )
    pace = data.get("pace") or {}
    if pace.get("projected_expense") is not None and not pace.get("complete"):
        if pace.get("budget_limit") is not None:
            status = "预计不超" if pace.get("on_track") else "预计超支"
            lines.append(
                f"按日均推算月底 {money.format_money(pace['projected_expense'], currency)} · {status}"
            )
        elif pace.get("scope") == "year":
            lines.append(f"按日均推算全年 {money.format_money(pace['projected_expense'], currency)}")
    if data.get("findings"):
        lines.extend(["", "### 值得一说"])
        for finding in data["findings"]:
            lines.append(f"- {finding['message']}")
            if finding.get("action"):
                lines.append(f"  - {finding['action']}")
    if data.get("insights"):
        lines.extend(["", "### 观察"])
        for item in data["insights"]:
            lines.append(f"- {item}")
    if data.get("month_trend") and len(data.get("by_month") or {}) >= 2:
        lines.extend(["", "### 月度支出"])
        for item in data["month_trend"]:
            lines.append(f"- {item['month']} {money.format_money(item['expense'], currency)}")
    if data.get("recurring"):
        lines.extend(["", "### 周期账"])
        cadence_label = {"monthly": "每月", "weekly": "每周", "biweekly": "每两周"}
        for item in data["recurring"][:6]:
            status = "这月还没记" if item.get("missing_this_month") else ("这月已记" if item.get("recorded_this_month") else "")
            extra = f" · {status}" if status else ""
            lines.append(
                f"- {item['label']} {money.format_money(item['typical_amount'], currency)} · {cadence_label.get(item['cadence'], item['cadence'])}{extra}"
            )
    if data["by_category"]:
        lines.extend(["", "### 分类支出"])
        for category, amount in data["by_category"].items():
            share = (amount / totals["expense"] * 100) if totals["expense"] else 0
            lines.append(f"- {category} {money.format_money(amount, currency)} ({share:.0f}%)")
    if data.get("top_merchants"):
        lines.extend(["", "### 商户"])
        for item in data["top_merchants"][:5]:
            lines.append(f"- {item['merchant']} {money.format_money(item['amount'], currency)}")
    if data.get("by_method"):
        lines.extend(["", "### 支付渠道"])
        for method, amount in list(data["by_method"].items())[:5]:
            label = METHOD_LABELS.get(method, method)
            lines.append(f"- {label} {money.format_money(amount, currency)}")
    if data.get("accounts"):
        lines.extend(["", "### 账户"])
        for item in data["accounts"]:
            lines.append(f"- {item['name']} {money.format_money(item['balance'], currency)}")
    if data.get("budgets"):
        lines.extend(["", "### 预算"])
        for item in data["budgets"]:
            status = "超支" if item["over"] else "剩余"
            lines.append(
                f"- {item['category']} {money.format_money(item['limit'], currency)} · 已花 {money.format_money(item['spent'], currency)} · {status} {money.format_money(abs(item['remaining']), currency)}"
            )
    if data.get("pending_reimbursement"):
        lines.extend(["", "### 待报销"])
        for item in data["pending_reimbursement"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} {money.format_money(item.get('amount'), currency)}"
            )
    if data.get("unmatched_refunds"):
        lines.extend(["", "### 未对上的退款"])
        for item in data["unmatched_refunds"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} {money.format_money(item.get('amount'), currency)}"
            )
    if data.get("outliers"):
        lines.extend(["", "### 偏大支出"])
        for item in data["outliers"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('category')} {money.format_money(item.get('amount'), currency)}"
            )
    if data.get("needs_review"):
        lines.extend(["", "### 待复核"])
        for item in data["needs_review"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} · {item.get('category')} {money.format_money(item.get('amount'), currency)}"
            )
    if data["count"] == 0 and not data.get("accounts") and not data.get("budgets"):
        return f"{period.get('label') or '这段时间'}没有记账。"
    return "\n".join(lines) + "\n"


def build_summary(ledger, args, default_range=None):
    range_name = getattr(args, "range", None) or default_range
    period = resolve_period(
        month=getattr(args, "month", None),
        range_name=range_name,
        start=getattr(args, "start", None),
        end=getattr(args, "end", None),
    )
    category_parents = category_parent_map(ledger)
    rows = filter_transactions(
        ledger["transactions"],
        query=getattr(args, "query", None),
        tx_type=getattr(args, "type", None),
        category=getattr(args, "category", None),
        method=getattr(args, "method", None),
        start=period["start"],
        end=period["end"],
        account=getattr(args, "account", None),
        category_parents=category_parents,
    )
    previous_rows = None
    previous_info = None
    if getattr(args, "compare", False):
        previous_info = previous_period(period)
        if previous_info:
            previous_rows = filter_transactions(
                ledger["transactions"],
                query=getattr(args, "query", None),
                tx_type=getattr(args, "type", None),
                category=getattr(args, "category", None),
                method=getattr(args, "method", None),
                start=previous_info["start"],
                end=previous_info["end"],
                account=getattr(args, "account", None),
                category_parents=category_parents,
            )
    data = summarize(rows, period=period, previous_transactions=previous_rows, previous_period_info=previous_info, category_parents=category_parents)
    data["coverage"] = {"complete":False, "basis":"recorded transactions only; gaps in source coverage can affect comparisons"}
    data["accounts"] = account_balances(ledger)
    data["budgets"] = budget_progress(ledger, rows, period)
    data["recurring"] = detect_recurring(ledger.get("transactions") or [])
    data["pending_reimbursement"] = pending_reimbursements(rows)
    data["unmatched_refunds"] = unmatched_refunds(ledger.get("transactions") or [], rows)
    data["month_trend"] = month_trend(ledger.get("transactions") or [])
    data["pace"] = spending_pace(period, data.get("daily_average") or 0, data["budgets"])
    data["insights"] = build_insight_lines(data, ledger.get("currency", "CNY"))
    # Butler findings: things worth saying that a totals table cannot express.
    data["findings"] = build_findings(
        ledger,
        currency=default_currency(ledger.get("currency")),
        balances=data["accounts"],
        limit=3,
    )
    return data


def collect_open_items(ledger, currency="CNY"):
    """Everything the user still owes attention to, from every source.

    These used to live behind separate commands (``advance list``,
    ``review next``, ``recurring``...), so the person had to ask seven times to
    find out what needed doing. The butler asks once.
    """
    items = []
    transactions = ledger.get("transactions") or []

    suppressed = {r.get("key") for r in ledger.get("review_queue",[]) if r.get("status") in {"resolved","dismissed","waiting","snoozed"}}
    for advance in open_advances(transactions):
        if "stale_advance:" + advance["id"] in suppressed: continue
        remaining = advance_remaining(advance)
        days = days_outstanding(advance)
        label = advance.get("merchant") or advance.get("note") or advance.get("id")
        age = f"，已经 {days} 天了" if days is not None and days >= 30 else ""
        items.append(
            {
                "priority": 1,
                "kind": "advance_open",
                "transaction_id": advance.get("id"),
                "say": f"{label} 的 {money.format_money(remaining, currency)} 还没收回{age}。",
                "do": "收到就用 advance settle 销掉；要不回来就用 advance writeoff，别一直挂着。",
                "command": f"ledger advance settle --id {advance.get('id')}",
            }
        )

    for refund in unmatched_refunds(transactions, transactions):
        if "unmatched_refund:" + refund["id"] in suppressed: continue
        items.append(
            {
                "priority": 1,
                "kind": "unmatched_refund",
                "say": (
                    f"{refund.get('occurred_at', '')[:10]} 的退款 "
                    f"{money.format_money(refund.get('amount'), currency)} 还没对上原始支出。"
                ),
                "do": "告诉我它退的是哪一笔，我挂回去。",
                "command": f"ledger update --id {refund.get('id')} --related-id <原支出id>",
            }
        )

    queue = ledger.get("review_queue") or []
    for entry in queue:
        if not isinstance(entry, dict) or entry.get("status") != "open":
            continue
        items.append(
            {
                "priority": 1 if entry.get("code") == "category_type_mismatch" else 2,
                "kind": entry.get("code"),
                "say": entry.get("detail") or entry.get("message") or f"{entry.get('code')} 待确认",
                "do": "确认后我改掉。",
                "command": f"ledger review resolve --id {entry.get('transaction_id')} --code {entry.get('code')}",
            }
        )

    for recurring in detect_recurring(transactions):
        if recurring.get("missing_this_month"):
            items.append(
                {
                    "priority": 2,
                    "kind": "recurring_missing",
                    "say": f"{recurring.get('label')} 这个月好像还没记。",
                    "do": "如果是漏了，补一笔；如果不再发生，忽略即可。",
                    "command": None,
                }
            )
    return items


def days_outstanding(transaction, today=None):
    """How long ago the money went out, which is what makes an advance urgent."""
    occurred = transaction.get("occurred_at")
    if not isinstance(occurred, str):
        return None
    try:
        day = date.fromisoformat(occurred[:10])
    except ValueError:
        return None
    today = today or local_today().date()
    return (today - day).days

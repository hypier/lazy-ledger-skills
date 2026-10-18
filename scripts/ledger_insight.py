"""Financial-butler analysis for Lazy Ledger.

The ledger is not only a place to record money; it should notice things. This
module turns raw transactions into *findings*: observations a person would make
about their own spending, ranked by how much they matter.

Every finding carries a ``kind``, a human-readable ``message``, the evidence
behind it, and a suggested action, so the caller can explain the reasoning
instead of emitting a bare number.

Design notes:
  - Findings are computed from transactions only; no ledger state is mutated.
  - Each detector is independent and conservative: it must be able to justify
    itself with numbers, never with a hunch.
  - Detectors are omitted entirely when their precondition is not met, rather
    than emitting an "unknown" row.
"""

from __future__ import annotations

import datetime
from collections import defaultdict

# Severity ordering used when the caller only wants the top few findings.
SEVERITY = {"alert": 0, "warn": 1, "info": 2, "praise": 3}

# Spending that accelerated this much against the previous comparable window.
SURGE_RATIO = 1.5
SURGE_MIN_BASELINE = 3  # ignore tiny baselines where one order skews everything
# An advance outstanding this long is easy to forget about.
ADVANCE_STALE_DAYS = 30
# A single row this many times the typical amount deserves a second look.
OUTLIER_RATIO = 5.0
OUTLIER_MIN_AMOUNT = 500.0
# A merchant this many times in a month is a habit worth naming.
HABIT_MIN_COUNT = 6
# Two rows this close in amount, merchant and date are probably one purchase
# entered twice -- the highest-value thing the butler can catch, because a
# duplicate silently doubles a number the user trusts.
DUPLICATE_WITHIN_HOURS = 48
DUPLICATE_AMOUNT_TOLERANCE = 0.01
# A monthly bill that was paid this many months and then stopped is either
# cancelled (fine) or forgotten (not fine). Two months of silence is the point
# where asking costs less than staying quiet.
MISSING_RECURRING_MIN_HISTORY = 3
MISSING_RECURRING_SILENT_MONTHS = 2


def _day(transaction):
    value = transaction.get("occurred_at")
    return value[:10] if isinstance(value, str) else ""


def _amount(transaction):
    try:
        return float(transaction.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _counts(transaction):
    """Transactions that represent real spending by the user."""
    if transaction.get("type") != "expense":
        return False
    if transaction.get("excluded_from_totals"):
        return False
    # An unreimbursed advance is owed back, so it is not spending.
    if transaction.get("advance") == "open":
        return False
    return True


def _month_bounds(today, offset=0):
    year, month = today.year, today.month + offset
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return f"{year:04d}-{month:02d}"


def _days_since(day_text, today):
    try:
        day = datetime.date.fromisoformat(day_text)
    except (TypeError, ValueError):
        return None
    return (today - day).days


def find_category_surges(transactions, today, currency="CNY"):
    """Categories that sped up against last month, with the driver named."""
    this_month = _month_bounds(today)
    last_month = _month_bounds(today, -1)
    current = defaultdict(list)
    previous = defaultdict(list)
    for transaction in transactions:
        if not _counts(transaction):
            continue
        month = month_key(transaction)
        if month == this_month:
            current[transaction.get("category") or "其他"].append(transaction)
        elif month == last_month:
            previous[transaction.get("category") or "其他"].append(transaction)

    findings = []
    for category, rows in current.items():
        baseline_rows = previous.get(category) or []
        if len(baseline_rows) < SURGE_MIN_BASELINE:
            continue
        ratio = len(rows) / len(baseline_rows)
        if ratio < SURGE_RATIO:
            continue
        # Name the merchant that drives the increase, so the finding is actionable.
        merchants = defaultdict(int)
        for row in rows:
            merchants[row.get("merchant") or "未填商户"] += 1
        driver, driver_count = max(merchants.items(), key=lambda item: item[1])
        baseline_amount = sum(_amount(row) for row in baseline_rows)
        current_amount = sum(_amount(row) for row in rows)
        findings.append(
            {
                "kind": "category_surge",
                "severity": "warn",
                "category": category,
                "message": (
                    f"{category}比上月多了 {ratio:.1f} 倍：{len(baseline_rows)} 笔 → {len(rows)} 笔"
                    f"（{format_money(baseline_amount, currency)} → {format_money(current_amount, currency)}）"
                    f"，主要来自{driver}（{driver_count} 笔）。"
                ),
                "evidence": {
                    "category": category,
                    "previous_count": len(baseline_rows),
                    "current_count": len(rows),
                    "previous_amount": round(baseline_amount, 2),
                    "current_amount": round(current_amount, 2),
                    "top_merchant": driver,
                },
                "action": f"看这些{category}是不是计划内的；如果是长期习惯，可以考虑设{category}月预算。",
            }
        )
    return findings


def find_stale_advances(transactions, today, currency="CNY"):
    """Advances still outstanding long after they were paid out."""
    findings = []
    for transaction in transactions:
        if transaction.get("advance") != "open":
            continue
        days = _days_since(_day(transaction), today)
        if days is None or days < ADVANCE_STALE_DAYS:
            continue
        label = transaction.get("merchant") or transaction.get("note") or transaction.get("id")
        findings.append(
            {
                "kind": "stale_advance",
                "severity": "warn",
                "transaction_id": transaction.get("id"),
                "message": (
                    f"{label} 的 {format_money(_amount(transaction), currency)} 垫付已经 {days} 天没收回。"
                ),
                "evidence": {"days_outstanding": days, "amount": round(_amount(transaction), 2)},
                "action": "确认这笔还能不能报；要不回来就用 advance writeoff 记成损失，别一直挂着。",
            }
        )
    return findings


def find_outliers(transactions, today, currency="CNY"):
    """Single rows far larger than the user's typical purchase.

    A small ledger is exactly when a surprising amount matters most, so the bar
    is low: a handful of rows is enough to say "this is much bigger than
    everything else you recorded". The median keeps one huge row from hiding
    itself.
    """
    recent = [row for row in transactions if _counts(row)]
    if len(recent) < 3:
        return []
    amounts = sorted(_amount(row) for row in recent)
    # Median resists being dragged up by the very outliers we are looking for.
    median = amounts[len(amounts) // 2]
    if median <= 0:
        return []
    findings = []
    for transaction in recent:
        amount = _amount(transaction)
        if amount < OUTLIER_MIN_AMOUNT or amount < median * OUTLIER_RATIO:
            continue
        label = transaction.get("merchant") or transaction.get("note") or transaction.get("id")
        findings.append(
            {
                "kind": "outlier",
                "severity": "info",
                "transaction_id": transaction.get("id"),
                "label": label,
                "message": (
                    f"{_day(transaction)} 的 {label} {format_money(amount, currency)} "
                    f"是平时单笔（约 {format_money(median, currency)}）的 {amount / median:.0f} 倍。"
                ),
                "evidence": {"amount": round(amount, 2), "median": round(median, 2)},
                "action": "确认不是误记或重复；如果是一次性大件，可以在备注里写清楚。",
            }
        )
    return findings[:3]


def find_habit_merchants(transactions, today, currency="CNY"):
    """One place frequent enough that the user may not have noticed the total."""
    this_month = _month_bounds(today)
    totals = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for transaction in transactions:
        if not _counts(transaction) or month_key(transaction) != this_month:
            continue
        key = transaction.get("merchant") or "未填商户"
        totals[key]["count"] += 1
        totals[key]["amount"] += _amount(transaction)
    findings = []
    for merchant, stat in totals.items():
        if stat["count"] < HABIT_MIN_COUNT or merchant == "未填商户":
            continue
        findings.append(
            {
                "kind": "habit_merchant",
                "severity": "info",
                "merchant": merchant,
                "message": (
                    f"这个月已经去{merchant} {stat['count']} 次，共 {format_money(stat['amount'], currency)}。"
                ),
                "evidence": {"count": stat["count"], "amount": round(stat["amount"], 2)},
                "action": "觉得多了就设个上限；觉得正常就不用管，记下来只是为了让你看见。",
            }
        )
    return findings


# A merchant that routinely costs the same amount -- the usual takeaway, the
# usual coffee -- produces pairs of equal payments two days apart all month. Those
# are a habit, not a double entry, and flagging them buries the real ones.
DUPLICATE_HABIT_REPEATS = 4


def _repeated_amount(transactions, subject, amount):
    """Whether ``subject`` is routinely charged this exact amount."""
    hits = [
        row
        for row in transactions
        if _counts(row)
        and (row.get("merchant") == subject or row.get("category") == subject)
        and abs(_amount(row) - amount) <= DUPLICATE_AMOUNT_TOLERANCE
    ]
    return len(hits) >= DUPLICATE_HABIT_REPEATS


def find_duplicate_suspected(transactions, today, currency="CNY"):
    """Rows that look like the same purchase entered twice.

    This outranks every other finding: an outlier might be a real expense, but a
    duplicate is definitely an error, and it inflates a total the user trusts.
    Detection is deliberately strict -- same amount to the cent, compatible
    merchant, within two days, and not a recurring habit -- because a false
    accusation of double-entry is worse than a missed one.
    """
    counted = [
        row for row in transactions if _counts(row) and row.get("type") in (None, "expense")
    ]
    counted.sort(key=lambda row: (_day(row), _amount(row)))
    findings = []
    reported = set()
    for index, row in enumerate(counted):
        if row.get("id") in reported:
            continue
        for other in counted[index + 1 :]:
            if other.get("id") in reported:
                continue
            first, second = _day(row), _day(other)
            if not first or not second:
                continue
            try:
                gap = abs(
                    (datetime.date.fromisoformat(second) - datetime.date.fromisoformat(first)).days
                )
            except ValueError:
                continue
            if gap > DUPLICATE_WITHIN_HOURS // 24:
                continue
            amount = _amount(row)
            if abs(amount - _amount(other)) > DUPLICATE_AMOUNT_TOLERANCE:
                continue
            if not _same_subject(row, other):
                continue
            subject = row.get("merchant") or row.get("category")
            if subject and _repeated_amount(transactions, subject, amount):
                continue
            reported.add(row.get("id"))
            reported.add(other.get("id"))
            label = row.get("merchant") or row.get("note") or row.get("category") or "一笔"
            findings.append(
                {
                    "kind": "duplicate_suspected",
                    "severity": "alert",
                    "transaction_id": other.get("id"),
                    "message": (
                        f"{first} 和 {second} 各有一笔 {format_money(amount, currency)} 的{label}，"
                        "看着像同一笔记了两次。"
                    ),
                    "evidence": {
                        "amount": round(amount, 2),
                        "ids": [row.get("id"), other.get("id")],
                        "days_apart": gap,
                    },
                    "action": "如果确实是两次，忽略即可；如果记重了，删掉一笔，否则支出会虚高一倍。",
                }
            )
            break
    return findings


def _same_subject(first, second):
    """Whether two rows are plausibly the same purchase."""
    first_merchant = (first.get("merchant") or "").strip()
    second_merchant = (second.get("merchant") or "").strip()
    if first_merchant and second_merchant:
        return first_merchant == second_merchant
    # Without a merchant, fall back to the category: two ¥48 meals two days
    # apart at unrelated places are not the same purchase.
    first_category = first.get("category") or ""
    second_category = second.get("category") or ""
    return bool(first_category) and first_category == second_category


def find_missing_recurring(transactions, today, currency="CNY"):
    """A bill that used to arrive every month and then stopped.

    Every other detector looks at what is present. This one looks at what is
    absent, which is the kind of thing a person notices only when the service is
    cancelled for non-payment.
    """
    months = defaultdict(lambda: defaultdict(float))
    for row in transactions:
        if not _counts(row) or row.get("type") not in (None, "expense"):
            continue
        day = _day(row)
        if not day:
            continue
        key = row.get("merchant") or row.get("category") or ""
        if not key:
            continue
        months[key][day[:7]] += _amount(row)

    findings = []
    current = today.strftime("%Y-%m")
    for key, by_month in months.items():
        if len(by_month) < MISSING_RECURRING_MIN_HISTORY or current in by_month:
            continue
        ordered = sorted(by_month)
        # The pattern has to be monthly, not sporadic: consecutive months.
        if not _is_consecutive(ordered):
            continue
        last = ordered[-1]
        silent = _months_between(last, current)
        if silent < MISSING_RECURRING_SILENT_MONTHS:
            continue
        typical = sorted(by_month.values())[len(by_month) // 2]
        findings.append(
            {
                "kind": "missing_recurring",
                "severity": "warn",
                "message": (
                    f"{key} 连续 {len(ordered)} 个月都在 {format_money(typical, currency)} 左右，"
                    f"但 {last} 之后就没再出现过。"
                ),
                "evidence": {"key": key, "months": ordered, "last_month": last, "amount": round(typical, 2)},
                "action": "是取消了就不用管；如果还在扣，就是漏记了，补上或者告诉我我来记。",
            }
        )
    return findings


def _is_consecutive(months):
    """Whether ``months`` (sorted YYYY-MM) runs with no gaps, allowing one."""
    if len(months) < 2:
        return False
    gaps = [_months_between(months[index - 1], months[index]) for index in range(1, len(months))]
    return sum(1 for gap in gaps if gap > 1) <= 1


def _months_between(start, end):
    start_year, start_month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    return (end_year - start_year) * 12 + (end_month - start_month)


def find_category_drift(transactions, today, currency="CNY"):
    """One merchant filed under different categories in different months.

    The user's own bookkeeping became inconsistent, so last month and this month
    do not compare fairly. Only surfaced for merchants with enough history that
    the split is a pattern rather than a one-off mistake.
    """
    by_merchant = defaultdict(lambda: defaultdict(set))
    for row in transactions:
        if not _counts(row) or row.get("type") not in (None, "expense"):
            continue
        merchant = (row.get("merchant") or "").strip()
        category = row.get("category")
        day = _day(row)
        if not merchant or not category or not day:
            continue
        by_merchant[merchant][category].add(day[:7])

    findings = []
    for merchant, categories in by_merchant.items():
        if len(categories) < 2:
            continue
        # Count distinct months, not month occurrences across categories: one
        # merchant seen twice in the same month is still one month of history.
        months = {month for spans in categories.values() for month in spans}
        if len(months) < 2:
            continue
        ordered = sorted(categories.items(), key=lambda item: (len(item[1]), item[0]), reverse=True)
        dominant, dominant_months = ordered[0]
        # A category only counts as drift if it appears in a month the dominant
        # one never covered. Both names in the same month is just a re-tag.
        minority = [
            name for name, spans in ordered[1:] if spans - dominant_months
        ]
        if not minority:
            continue
        findings.append(
            {
                "kind": "category_drift",
                "severity": "info",
                "merchant": merchant,
                "message": (
                    f"{merchant} 大部分记在{dominant}，但有时记成{'、'.join(minority)}，"
                    "这样按月比较会比不出来。"
                ),
                "evidence": {"merchant": merchant, "dominant": dominant, "others": minority},
                "action": "统一成一个类别，趋势才有意义。",
            }
        )
    return findings


def find_negative_balances(balances, currency="CNY"):
    """Accounts whose derived balance went below zero, which means missing data.

    ``balances`` is the output of ``account_balances()``: a mapping of account id
    to a record whose ``balance`` is *computed* from the opening balance plus
    flows. It is never stored on the account, so reading the account row would
    silently find nothing.
    """
    findings = []
    # account_balances() returns a list of non-archived account records.
    records = balances.values() if isinstance(balances, dict) else balances
    for record in records:
        if not isinstance(record, dict) or record.get("archived"):
            continue
        balance = record.get("balance")
        if not isinstance(balance, (int, float)) or balance >= 0:
            continue
        findings.append(
            {
                "kind": "negative_balance",
                "severity": "alert",
                "account": record.get("name"),
                "message": (
                    f"{record.get('name')} 的余额算出来是 {format_money(balance, currency)}，"
                    "真实账户不可能是负的。"
                ),
                "evidence": {"balance": round(float(balance), 2)},
                "action": "多半是有支出记到了这个账户、但期初余额没填。补一下期初或调整账户。",
            }
        )
    return findings


def find_missing_details(transactions, today, currency="CNY"):
    """Rows recorded without a merchant, where the category carries no clue either."""
    this_month = _month_bounds(today)
    missing = [
        row
        for row in transactions
        if _counts(row)
        and month_key(row) == this_month
        and not row.get("merchant")
        and (row.get("category") or "其他") == "其他"
    ]
    if len(missing) < 3:
        return []
    total = sum(_amount(row) for row in missing)
    return [
        {
            "kind": "unspecified_spending",
            "severity": "info",
            "message": (
                f"本月有 {len(missing)} 笔共 {format_money(total, currency)} 既没商户也没分类，"
                "报表里会全都归到「其他」。"
            ),
            "evidence": {"count": len(missing), "amount": round(total, 2)},
            "action": "补上商户或分类，下个月的报表才有意义。",
        }
    ]


def build_findings(ledger, today=None, currency=None, limit=None, balances=None):
    """Run every detector and return findings ordered by importance.

    The result is stable for a given ledger so repeated calls do not churn.
    """
    today = today or datetime.date.today()
    transactions = [row for row in ledger.get("transactions") or [] if isinstance(row, dict)]
    currency = currency or ledger.get("currency") or "CNY"
    findings = []
    findings.extend(find_negative_balances(balances or {}, currency))
    # A possible double-entry comes before everything else: it corrupts the
    # totals that every other finding is measured against.
    findings.extend(find_duplicate_suspected(transactions, today, currency))
    findings.extend(find_stale_advances(transactions, today, currency))
    findings.extend(find_missing_recurring(transactions, today, currency))
    findings.extend(find_category_surges(transactions, today, currency))
    findings.extend(find_outliers(transactions, today, currency))
    findings.extend(find_habit_merchants(transactions, today, currency))
    findings.extend(find_category_drift(transactions, today, currency))
    findings.extend(find_missing_details(transactions, today, currency))
    findings.sort(key=lambda item: SEVERITY.get(item.get("severity"), 9))
    if limit:
        findings = findings[:limit]
    return findings


def month_key(transaction):
    day = _day(transaction)
    return day[:7] if day else ""


def format_money(amount, currency="CNY"):
    symbol = "¥" if currency == "CNY" else f"{currency} "
    return f"{symbol}{float(amount):,.2f}"

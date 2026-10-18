"""The butler's daily briefing.

``show`` renders a table of what the ledger contains. A butler instead answers
one question: *what would I want to know right now?* That means an opinion, the
evidence behind it, and at most a couple of things worth acting on -- not a
transcript of the data.

This module composes the pieces other parts of the tool already compute
(period totals, findings, open items, pace) into that briefing. It never
mutates the ledger.
"""

from __future__ import annotations

import datetime

from ledger_insight import SEVERITY, format_money


def _money(amount, currency):
    return format_money(amount or 0, currency)


def headline(period_data, ledger, currency, findings, open_items):
    """One or two sentences that state how things stand, and why.

    A bare total is not a headline. If a single large purchase distorts the
    month, say what the month looks like without it, because that is the number
    the person actually recognises.
    """
    totals = period_data.get("totals") or {}
    expense = float(totals.get("expense") or 0)
    income = float(totals.get("income") or 0)
    net = float(totals.get("net") or (income - expense))
    count = int(period_data.get("count") or 0)
    label = period_label(period_data)

    if count == 0:
        return "这个月还没有记录。开始记几笔，我就能看出趋势了。"

    big = _dominant_purchase(findings, expense)
    lines = []
    if big and big["share"] >= 0.6:
        rest = expense - big["amount"]
        lines.append(
            f"{label}支出 {_money(expense, currency)}，"
            f"其中 {big['label']} 一笔就占 {big['share'] * 100:.0f}%；"
            f"去掉它，日常支出只有 {_money(rest, currency)}。"
        )
    else:
        # A total with no subject is just a number. Name where most of it went;
        # that is the sentence a person would actually repeat.
        leader = _top_category(period_data)
        if leader:
            name, amount = leader
            lines.append(
                f"{label}支出 {_money(expense, currency)}，"
                f"最多是{name} {_money(amount, currency)}"
                f"（{amount / expense * 100:.0f}%），净额 {_money(net, currency)}。"
            )
        else:
            lines.append(
                f"{label}支出 {_money(expense, currency)}"
                f"，收入 {_money(income, currency)}，净额 {_money(net, currency)}。"
            )

    comparison = period_data.get("comparison") or {}
    change = comparison.get("expense_change_pct")
    previous_label = comparison.get("previous_label")
    previous_expense = (comparison.get("previous_totals") or {}).get("expense")
    if change is not None and previous_label:
        if not previous_expense:
            # A percentage against a near-zero base is noise, not a trend.
            lines.append("这是第一个有记录的月份，还没有可比的上月。")
        elif abs(change) >= 20:
            direction = "多" if change > 0 else "少"
            # When a one-off dominates, a percentage mostly measures that one
            # purchase. Say so instead of printing a meaningless multiple.
            if big:
                lines.append(
                    f"上月只有 {_money(previous_expense, currency)}；"
                    "这个差距主要是那一笔，不代表日常开销涨了。"
                )
            else:
                lines.append(
                    f"比{previous_label}{direction}花了 {abs(change):.0f}%"
                    f"（{_money(previous_expense, currency)} → {_money(expense, currency)}）。"
                )

    if count <= 3:
        lines.append(f"不过目前只有 {count} 笔，结论还不可靠，多记几笔我再做判断。")
    return " ".join(lines)


def period_label(period_data):
    """The human name of the period, which lives under ``period``, not ``label``."""
    period = period_data.get("period") or {}
    return period.get("label") or period_data.get("label") or "本期"


def _top_category(period_data):
    """The category that took the largest share, if one stands out at all."""
    by_category = period_data.get("by_category") or {}
    leaders = [(name, amount) for name, amount in by_category.items() if name != "其他" and amount > 0]
    if not leaders:
        return None
    name, amount = max(leaders, key=lambda item: item[1])
    return name, amount


def _dominant_purchase(findings, expense):
    """The single row that dominates the period, if one does."""
    for finding in findings:
        if finding.get("kind") != "outlier":
            continue
        amount = (finding.get("evidence") or {}).get("amount") or 0
        if not expense or not amount:
            continue
        share = amount / expense
        if share < 0.5:
            continue
        return {"amount": amount, "share": share, "label": finding.get("label") or "一笔大额"}
    return None


def action_items(period_data, ledger, currency, findings, open_items, pending_count=0):
    """Things the butler would raise today, most urgent first.

    Every item names a concrete next command so the person does not need to
    understand the ledger schema to act on it.
    """
    items = []
    for finding in findings:
        items.append(
            {
                "priority": SEVERITY.get(finding.get("severity"), 9),
                "kind": finding.get("kind"),
                "say": finding.get("message"),
                "do": finding.get("action"),
                "command": finding.get("command"),
            }
        )

    for item in open_items:
        items.append(item)

    pace = period_data.get("pace") or {}
    if pace.get("budget_limit") is None and int(period_data.get("count") or 0) >= 8:
        items.append(
            {
                "priority": 2,
                "kind": "no_budget",
                "say": "还没有设月度预算，所以我没法提醒你快超支了。",
                "do": "设一个总额，我就能在接近上限时提前说。",
                "command": "ledger budget set --amount 5000",
            }
        )
    elif pace.get("on_track") is False:
        items.append(
            {
                "priority": 1,
                "kind": "budget_overrun",
                "say": (
                    f"按现在日均，月底大约花 {_money(pace.get('projected_expense'), currency)}，"
                    f"会超过预算 {_money(pace.get('budget_limit'), currency)}。"
                ),
                "do": "要么收一收，要么把预算调到实际水平。",
                "command": None,
            }
        )

    items.sort(key=lambda item: item.get("priority", 9))
    return items


def render_advice(advice):
    """Advice reads differently from problems: an observation plus an offer.

    Keeping the two apart matters. A problem is something to fix; advice is
    something to consider. Mixing them makes every suggestion sound like a
    complaint.
    """
    lines = []
    for item in advice or []:
        lines.append(f"· {item.get('message')}")
        if item.get("option"):
            lines.append(f"  {item['option']}")
        if item.get("command"):
            lines.append(f"  → {item['command']}")
    return lines


def render_brief(period_data, ledger, currency, findings, open_items, limit=4, advice=None):
    """Compose the whole briefing as lines ready to print."""
    count = int(period_data.get("count") or 0)
    lines = [headline(period_data, ledger, currency, findings, open_items)]

    items = action_items(period_data, ledger, currency, findings, open_items)[:limit]
    if items:
        lines.append("")
        labels = {0: "要处理", 1: "留意", 2: "可以做"}
        for index, item in enumerate(items, 1):
            label = labels.get(item.get("priority"), "看看")
            lines.append(f"{index}. [{label}] {item['say']}")
            if item.get("do"):
                lines.append(f"   {item['do']}")
            if item.get("command"):
                lines.append(f"   → {item['command']}")
    elif count:
        lines.append("")
        lines.append("目前没发现需要你操心的事，记下去就好。")

    advice_lines = render_advice(advice)
    if advice_lines:
        lines.append("")
        lines.append("有点想说：")
        lines.extend(advice_lines)

    return lines


def morning_greeting(now=None):
    """A short, warm opener; the butler does not start every message cold."""
    now = now or datetime.datetime.now()
    hour = now.hour
    if hour < 6:
        return "这么晚还没睡。"
    if hour < 11:
        return "早上好。"
    if hour < 14:
        return "中午好。"
    if hour < 19:
        return "下午好。"
    return "晚上好。"


def period_story(period_data, currency, findings=None):
    """The numbers a person would actually repeat to someone else."""
    totals = period_data.get("totals") or {}
    lines = []
    expense = float(totals.get("expense") or 0)
    income = float(totals.get("income") or 0)
    count = int(period_data.get("count") or 0)
    if count == 0:
        return ["这段时间没有记录。"]

    by_category = period_data.get("by_category") or {}
    leaders = [(name, amount) for name, amount in by_category.items() if name != "其他" and amount > 0][:2]
    if leaders:
        parts = "、".join(f"{name} {_money(amount, currency)}" for name, amount in leaders)
        lines.append(f"花得最多的是{parts}。")

    # The weekday/weekend split is only meaningful without a one-off: a laptop
    # bought on a Tuesday makes "weekends cost more" false and slightly absurd.
    weekday = period_data.get("weekday") or {}
    weekday_amount = float(weekday.get("weekday") or 0)
    weekend_amount = float(weekday.get("weekend") or 0)
    dominant = _dominant_purchase(findings, float((period_data.get("totals") or {}).get("expense") or 0))
    if weekday_amount and weekend_amount and not dominant:
        if weekend_amount > weekday_amount * 1.5:
            lines.append(f"周末花了 {_money(weekend_amount, currency)}，比工作日还多。")

    # "¥0.00 income" is noise in a digest; only mention money that moved.
    if income:
        lines.append(f"收入 {_money(income, currency)}，净额 {_money(income - expense, currency)}。")
    return lines


def digest(period_data, ledger, currency, findings, open_items, advice=None):
    """A report the user could forward as-is.

    This is what a scheduled run prints, so it has to stand alone: no commands to
    explain, no schema words, and nothing that assumes the reader just talked to
    the butler. Anything genuinely needing a decision is named at the end.
    """
    label = period_label(period_data)
    lines = [f"【{label} · 账单】"]

    # Rebuilding the headline for a digest: the brief's version ends with a
    # comparison, which in a forwarded report reads as a fragment.
    totals = period_data.get("totals") or {}
    expense = float(totals.get("expense") or 0)
    income = float(totals.get("income") or 0)
    count = int(period_data.get("count") or 0)
    empty = count == 0
    if empty:
        lines.append("这段时间没有记录。")
    else:
        big = _dominant_purchase(findings, expense)
        sentence = f"一共花了 {_money(expense, currency)}"
        if income:
            sentence += f"，收入 {_money(income, currency)}"
        sentence += "。"
        if big:
            sentence += f"其中 {big['label']} 一笔就占了 {big['share'] * 100:.0f}%。"
        lines.append(sentence)

    story = [] if empty else [
        item for item in period_story(period_data, currency, findings=findings) if "¥0.00" not in item
    ]
    if story:
        lines.append("")
        lines.extend(story)

    advice_lines = [
        # A digest is read by a person on a phone, not typed into a shell.
        item for item in render_advice(advice) if not item.startswith("  →")
    ]
    if advice_lines:
        lines.append("")
        lines.extend(advice_lines)

    urgent = [item for item in action_items(period_data, ledger, currency, findings, open_items) if item.get("priority", 9) <= 1]
    if urgent:
        lines.append("")
        lines.append("需要你看一眼：")
        for item in urgent[:3]:
            lines.append(f"· {item['say']}")
    else:
        lines.append("")
        lines.append("没有需要你处理的事。")
    return lines

"""What the butler says back after it has written a row.

``add`` prints a JSON record. That is the right answer for a program and the
wrong answer for a person: it says *what was stored*, not *what it means*.

This module turns the same facts into the three things a person actually wants
to hear:

1. what I wrote down, and why I read it that way
2. what it does to the month
3. whether it turned up anything worth mentioning

It is pure formatting: no ledger access, no mutation, so it can be tested
against plain dicts and reused by ``digest``.
"""

from __future__ import annotations

from ledger_insight import format_money

# How each economic nature reads back to the person. The phrasing is the
# product: "垫付" tells them the money is coming back, "支出" does not.
NATURE_PHRASE = {
    "spending": "支出",
    "earning": "收入",
    "transfer": "转账",
    "not_mine": "不算你的",
    "advance": "垫付",
}

# A one-line explanation of why a row was classified the way it was. Only
# natures where the distinction matters get one; a plain purchase needs none.
NATURE_NOTE = {
    "transfer": "自有账户之间挪钱，不计入收支。",
    "not_mine": "这笔不计入你的收支。",
    "advance": "这笔钱会回来，暂时不算你的支出。",
}


def _money(amount, currency):
    return format_money(amount or 0, currency)


def describe_row(tx, currency):
    """One line: what was written, in the words a person uses."""
    nature = tx.get("nature") or "spending"
    phrase = NATURE_PHRASE.get(nature, "支出")
    amount = _money(tx.get("amount"), currency)
    parts = [f"{phrase} {amount}"]

    note = tx.get("note")
    category = tx.get("category")
    merchant = tx.get("merchant")
    if nature == "transfer":
        # The note already reads "微信提现500"; a merchant carved out of it is
        # just a fragment of the same sentence.
        merchant = None
    # "其他" says nothing worth reading aloud; the merchant does.
    if category and category != "其他" and category not in ("收入", "转账"):
        parts.append(str(category))
    if merchant and merchant not in (category, note) and not str(note or "").startswith(str(merchant)):
        parts.append(str(merchant))

    if nature == "transfer":
        source = tx.get("account") or tx.get("account_id")
        target = tx.get("to_account") or tx.get("to_account_id")
        if source and target:
            parts.append(f"{source} → {target}")
    else:
        account = tx.get("account") or tx.get("account_id")
        if account:
            parts.append(str(account))
    return " · ".join(parts)


def why_line(tx, event=None):
    """Why the butler read it this way, when that is not obvious.

    The event's reasons come from the classifier, so the explanation reflects
    the rule that actually fired rather than a guess made afterwards.
    """
    nature = tx.get("nature") or "spending"
    event = event or {}
    reasons = event.get("reasons") or []

    if nature in ("advance", "transfer", "not_mine") and reasons:
        phrases = [_reason_detail(reason) for reason in reasons]
        phrases = [phrase for phrase in phrases if phrase][:2]
        if phrases:
            # A route like "微信->银行卡" is visible in the line above; quoting it
            # back as a reason is noise. A word from the sentence is not.
            if nature == "transfer" and "->" in phrases[0]:
                return None
            return f"因为提到了「{'」「'.join(phrases)}」。"
    if nature in ("transfer", "not_mine"):
        return NATURE_NOTE[nature]
    if nature == "advance":
        return NATURE_NOTE["advance"]
    return None


def _reason_detail(reason):
    """Pull the human fragment out of a reason entry.

    Reasons are dicts (``{"rule": ..., "match": ...}``) in the payload and
    strings in older callers; accept both rather than assuming one shape.
    """
    if isinstance(reason, dict):
        return str(reason.get("match") or reason.get("rule") or "")
    text = str(reason)
    for separator in ("', 'match': '", ": ", "): ", " - "):
        if separator in text:
            return text.split(separator, 1)[1].rstrip("'}" )
    return text


def impact_line(before, after, currency):
    """What the month looks like now, phrased as a change rather than a total."""
    before = before or {}
    after = after or {}
    if not after.get("count"):
        return None

    before_expense = float(before.get("expense") or 0)
    after_expense = float(after.get("expense") or 0)
    delta = after_expense - before_expense
    label = after.get("label") or "本月"

    if abs(delta) < 0.005:
        # Nothing entered spending; say what that means rather than print ¥0.
        return f"{label}支出没有变化，这笔不进收支。"

    if before_expense:
        return (
            f"{label}支出变成 {_money(after_expense, currency)}"
            f"（多了 {_money(delta, currency)}）。"
        )
    return f"{label}支出现在是 {_money(after_expense, currency)}。"


def extra_lines(payload, currency):
    """Anything the write itself turned up: a settled advance, a matched refund."""
    lines = []
    for link in payload.get("refund_links") or []:
        if not isinstance(link, dict):
            continue
        lines.append(
            f"这笔退款对上了之前的一笔支出（{_money(link.get('amount'), currency)}），"
            "已经挂在一起，两边都算清楚了。"
        )
    for settlement in payload.get("advance_settlements") or []:
        if not isinstance(settlement, dict):
            continue
        remaining = settlement.get("remaining")
        if remaining is None or float(remaining) <= 0.009:
            lines.append(
                f"垫付 {_money(settlement.get('amount'), currency)} 收回来了，已经核销。"
                "这笔从头到尾没有算进你的支出。"
            )
        else:
            lines.append(
                f"收回了 {_money(settlement.get('amount'), currency)}，"
                f"还剩 {_money(remaining, currency)} 没结清。"
            )
    return lines


def compose_record(payload, currency, findings=None, limit=2):
    """The whole reply, as lines ready to print.

    Order matters: what I did, why, what it changed, then what else I noticed.
    Anything unrelated to this row goes last and is capped, so a routine
    purchase stays a one-line answer.
    """
    rows = payload.get("added") or payload.get("rows") or []
    if not rows:
        return ["没有记下任何东西。"]

    lines = []
    if len(rows) == 1:
        lines.append(f"记好了：{describe_row(rows[0], currency)}")
    else:
        lines.append(f"记好了 {len(rows)} 笔：")
        for row in rows:
            lines.append(f"· {describe_row(row, currency)}")

    if len(rows) == 1:
        why = why_line(rows[0], payload.get("event"))
        if why:
            lines.append(why)
        # If the account was changed away from the usual one, say so: silently
        # choosing a different card is the kind of thing that erodes trust.
        reason = rows[0].get("account_reason")
        if reason:
            lines.append(f"{reason}。")

    impact = impact_line(payload.get("before"), payload.get("after"), currency)
    if impact:
        lines.append(impact)

    lines.extend(extra_lines(payload, currency))

    notable = [
        finding
        for finding in (findings or [])
        if finding.get("severity") in ("alert", "warn") and finding.get("message")
    ]
    if notable:
        lines.append("")
        lines.append("顺带一提：")
        for finding in notable[:limit]:
            lines.append(f"· {finding.get('message')}")
    return lines

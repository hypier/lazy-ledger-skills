"""Advice the butler is allowed to give.

The user asked for 温和的消费建议 -- gentle nudges, not lectures. That sets a
hard rule for this module: every suggestion must be anchored to a number in the
ledger and must describe an option, not a verdict. "你这个月外卖 14 次 ¥588，
要不要设个上限" is advice; "建议合理规划消费" is noise wearing advice's clothes.

Advice only fires when there is enough history to be worth hearing. A ledger
with three rows cannot support a claim about habits, and a butler that guesses
loudly is worse than one that stays quiet.
"""

from __future__ import annotations

from collections import defaultdict

from ledger_insight import format_money

# Below this many counted rows in the month, hold the advice. Statistics on a
# handful of purchases describe the handful, not the person.
MIN_ROWS_FOR_ADVICE = 8

# A category has to be both frequent and large before suggesting a cap, else the
# suggestion is about rounding error.
ADVICE_MIN_COUNT = 5
ADVICE_MIN_SHARE = 0.15

# Only suggest a cap when it would plausibly be met: below this the target is so
# far under current behaviour that the suggestion reads as scolding.
MIN_CAP_RATIO = 0.5


def _money(amount, currency):
    return format_money(amount or 0, currency)


def _month_rows(transactions, month):
    from ledger_core.domain.metrics import month_key

    return [
        row
        for row in transactions
        if isinstance(row, dict) and month_key(row.get("occurred_at")) == month
    ]


def suggest_caps(transactions, month, currency, expenses):
    """Offer a ceiling for the one category that most deserves one.

    A cap is only proposed for a category that is frequent *and* a real share of
    spending, and the proposed number is a round one just under current
    behaviour so following it is a small step, not a cut.
    """
    stats = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for row in expenses or []:
        stats[row.get("category") or "其他"]["count"] += 1
        stats[row.get("category") or "其他"]["amount"] += float(row.get("amount") or 0)

    total = sum(stat["amount"] for stat in stats.values())
    if not total:
        return []

    candidates = [
        (category, stat)
        for category, stat in stats.items()
        if category != "其他"
        and stat["count"] >= ADVICE_MIN_COUNT
        and stat["amount"] / total >= ADVICE_MIN_SHARE
    ]
    if not candidates:
        return []
    category, stat = max(candidates, key=lambda item: item[1]["amount"])

    current = stat["amount"]
    # Round down to a friendly number: 588 -> 500, 1240 -> 1200.
    step = 100 if current >= 500 else 50 if current >= 200 else 10
    cap = int(current // step) * step
    if cap < current * MIN_CAP_RATIO or cap <= 0:
        cap = round(current)

    return [
        {
            "kind": "cap_suggestion",
            "tone": "suggest",
            "message": (
                f"这个月{category} {stat['count']} 次，{_money(current, currency)}，"
                f"占了 {stat['amount'] / total * 100:.0f}% 的支出。"
            ),
            "option": f"要不要给{category}设个 {_money(cap, currency)} 的上限？超了我提醒你。",
            "command": f'ledger budget set --category {category} --amount {cap}',
            "evidence": {"category": category, "count": stat["count"], "amount": round(current, 2), "cap": cap},
        }
    ]


def suggest_watch(transactions, month, currency, expenses):
    """Name the one merchant that quietly became a habit.

    Deliberately framed as an observation with an offer, because a merchant the
    user likes is not a problem -- it is only worth seeing the total.
    """
    stats = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for row in expenses or []:
        merchant = row.get("merchant")
        if not merchant:
            continue
        stats[merchant]["count"] += 1
        stats[merchant]["amount"] += float(row.get("amount") or 0)

    if not stats:
        return []
    merchant, stat = max(stats.items(), key=lambda item: (item[1]["count"], item[1]["amount"]))
    if stat["count"] < ADVICE_MIN_COUNT:
        return []
    per = stat["amount"] / stat["count"]
    return [
        {
            "kind": "habit_watch",
            "tone": "observe",
            "message": (
                f"{merchant}去了 {stat['count']} 次，一共 {_money(stat['amount'], currency)}"
                f"（每次约 {_money(per, currency)}）。"
            ),
            "option": "如果这个频率是你想要的，就不用管；不想的话我可以在超出时提醒你。",
            "command": None,
            "evidence": {"merchant": merchant, "count": stat["count"], "amount": round(stat["amount"], 2)},
        }
    ]


def build_advice(ledger, expenses, month, currency, today=None, findings=None):
    """All advice worth giving right now, at most two items.

    Ordered from most actionable to least, and capped: three suggestions in a
    row reads as nagging, and the third is always the weakest.

    A finding and advice can describe the same fact -- "美团 7 次" appears in the
    habit detector and again in the merchant observation. Saying it twice makes
    the butler sound like it is not paying attention, so advice that repeats a
    finding's subject is dropped.
    """
    transactions = ledger.get("transactions") or []
    rows = _month_rows(transactions, month)
    if len(rows) < MIN_ROWS_FOR_ADVICE:
        return []

    claimed = set()
    for finding in findings or []:
        for key in ("category", "merchant"):
            value = finding.get(key)
            if value:
                claimed.add(str(value))

    advice = []
    for item in suggest_caps(transactions, month, currency, expenses) + suggest_watch(
        transactions, month, currency, expenses
    ):
        subject = (item.get("evidence") or {}).get("category") or (item.get("evidence") or {}).get("merchant")
        if subject and str(subject) in claimed:
            # Already raised as a finding; advice would be the second mention.
            continue
        advice.append(item)
    return advice[:2]

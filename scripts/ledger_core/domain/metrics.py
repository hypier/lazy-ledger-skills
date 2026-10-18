"""Shared decimal financial projections used by all presentation surfaces."""
import math
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from .model import ensure_accounts
from .money import decimal_amount
from .semantics import counts_as, nature_of, settlement_of

WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

def safe_amount(value):
    return decimal_amount(value, default=0)

def json_numbers(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: json_numbers(v) for k,v in value.items()}
    if isinstance(value, list):
        return [json_numbers(v) for v in value]
    return value

def check_currencies(transactions, expected=None):
    currencies = {tx.get("currency") for tx in transactions if isinstance(tx, dict) and tx.get("currency")}
    if len(currencies) > 1 or (expected and currencies - {expected}):
        raise ValueError("Mixed currencies cannot be aggregated without an exchange-rate model")


DEFAULT_CATEGORY_PARENTS = {
    "外食": "餐饮",
    "咖啡茶饮": "餐饮",
    "买菜": "餐饮",
    "零食": "餐饮",
    "服饰": "购物",
    "超市": "购物",
    "日用百货": "购物",
    "其他购物": "购物",
    "物业": "居住",
    "加油": "交通",
    "停车通行": "交通",
    "车辆养护": "交通",
    "红包": "人情",
    "人情往来": "人情",
    "保险": "理财",
    "软件服务": "数字服务",
}


def account_balances(ledger):
    check_currencies(ledger.get("transactions", []), ledger.get("currency"))
    accounts = [account for account in ensure_accounts(ledger) if isinstance(account, dict)]
    state = {}
    for account in accounts:
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id:
            continue
        opening = safe_amount(account.get("opening_balance"))
        state[account_id] = {
            "id": account_id,
            "name": account.get("name"),
            "type": account.get("type"),
            "archived": bool(account.get("archived")),
            "opening_balance": round(opening, 2),
            "opening_confirmed": account.get("opening_confirmed", False),
            "balance": opening,
            "inflow": Decimal(0),
            "outflow": Decimal(0),
        }
    name_to_id = {
        account.get("name"): account.get("id")
        for account in accounts
        if isinstance(account.get("name"), str)
        and isinstance(account.get("id"), str)
        and account.get("id") in state
    }

    def resolve_id(tx, key_id, key_name):
        value = tx.get(key_id)
        if isinstance(value, str) and value in state:
            return value
        name = tx.get(key_name)
        if isinstance(name, str) and name in name_to_id:
            return name_to_id[name]
        return None

    for tx in ledger.get("transactions", []):
        if not isinstance(tx, dict) or tx.get("balance_effect") is False:
            continue
        amount = safe_amount(tx.get("amount"))
        if amount <= 0:
            continue
        source_id = resolve_id(tx, "account_id", "account")
        dest_id = resolve_id(tx, "to_account_id", "to_account")
        tx_type = tx.get("type")
        if not isinstance(tx_type, str):
            continue
        if tx_type == "expense" and source_id:
            state[source_id]["balance"] -= amount
            state[source_id]["outflow"] += amount
        elif tx_type in {"income", "refund"} and source_id:
            state[source_id]["balance"] += amount
            state[source_id]["inflow"] += amount
        elif tx_type == "transfer":
            if source_id:
                state[source_id]["balance"] -= amount
                state[source_id]["outflow"] += amount
            if dest_id:
                state[dest_id]["balance"] += amount
                state[dest_id]["inflow"] += amount
    rows = []
    for account in accounts:
        if account.get("id") not in state:
            continue
        item = state[account["id"]]
        item["balance"] = round(item["balance"], 2)
        item["inflow"] = round(item["inflow"], 2)
        item["outflow"] = round(item["outflow"], 2)
        if not item["archived"]:
            rows.append(item)
    return json_numbers(rows)


def budgets_for_month(ledger, month):
    if not month:
        return []
    items = [
        budget
        for budget in ledger.get("budgets", [])
        if isinstance(budget, dict)
        and isinstance(budget.get("category") or "", str)
        and isinstance(budget.get("month") or "", str)
    ]
    specific = [budget for budget in items if budget.get("month") == month]
    templates = [budget for budget in items if not budget.get("month")]
    used = {(budget.get("category") or "") for budget in specific}
    result = list(specific)
    for budget in templates:
        if (budget.get("category") or "") not in used:
            result.append(budget)
    return json_numbers(result)


def budget_progress(ledger, transactions, period=None):
    check_currencies(transactions, ledger.get("currency"))
    month = (period or {}).get("month")
    if not month and (period or {}).get("start"):
        month = period["start"][:7]
        end = period.get("end") or ""
        if end[:7] != month:
            month = None
    budgets = budgets_for_month(ledger, month)
    expense_total = Decimal(0)
    by_category = defaultdict(Decimal)
    category_parents = category_parent_map(ledger)
    for tx in transactions:
        if not counts_as(tx, "expense"):
            continue
        amount = safe_amount(tx.get("amount"))
        expense_total += amount
        category = tx.get("category")
        if not isinstance(category, str) or not category:
            category = "其他"
        by_category[category] += amount
        if category_parents.get(category):
            by_category[category_parents[category]] += amount
    rows = []
    for budget in budgets:
        if not isinstance(budget, dict):
            continue
        try:
            limit = decimal_amount(budget.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(limit):
            continue
        category = budget.get("category") or None
        if category is not None and not isinstance(category, str):
            continue
        spent = by_category[category] if category else expense_total
        remaining = round(limit - spent, 2)
        rows.append(
            {
                "id": budget.get("id"),
                "month": budget.get("month") or month,
                "category": category or "本月总额",
                "limit": round(limit, 2),
                "spent": round(spent, 2),
                "remaining": remaining,
                "pct": round(spent / limit * 100, 1) if limit else None,
                "over": remaining < 0,
            }
        )
    rows.sort(key=lambda item: (item["category"] != "本月总额", item["over"], item["category"]))
    return json_numbers(rows)


def tx_day(tx):
    if not isinstance(tx, dict):
        return ""
    value = tx.get("occurred_at")
    return value[:10] if isinstance(value, str) else ""


def month_key(iso_value):
    return iso_value[:7] if isinstance(iso_value, str) else ""


def compact_candidate(tx):
    if not isinstance(tx, dict):
        tx = {}
    return {
        "id": tx.get("id"),
        "type": tx.get("type"),
        "amount": tx.get("amount"),
        "category": tx.get("category"),
        "merchant": tx.get("merchant"),
        "method": tx.get("method"),
        "account": tx.get("account"),
        "to_account": tx.get("to_account"),
        "note": tx.get("note"),
        "occurred_at": tx.get("occurred_at"),
        "confidence": tx.get("confidence"),
        "wechat_transaction_id": tx.get("wechat_transaction_id"),
    }


def period_day_count(period, transactions):
    start, end = period.get("start"), period.get("end")
    if start and end:
        try:
            start_d = datetime.strptime(start, "%Y-%m-%d").date()
            end_d = datetime.strptime(end, "%Y-%m-%d").date()
            return max((end_d - start_d).days + 1, 1)
        except ValueError:
            return 1
    days = [tx_day(tx) for tx in transactions if isinstance(tx, dict) and tx_day(tx)]
    if not days:
        return 1
    try:
        start_d = datetime.strptime(min(days), "%Y-%m-%d").date()
        end_d = datetime.strptime(max(days), "%Y-%m-%d").date()
        return max((end_d - start_d).days + 1, 1)
    except ValueError:
        return 1


def pct_change(new, old):
    if old == 0:
        return None if new == 0 else 100.0
    return round((new - old) / old * 100, 1)


def summarize(transactions, period=None, previous_transactions=None, previous_period_info=None, category_parents=None):
    check_currencies(list(transactions) + list(previous_transactions or []))
    totals = {"expense": Decimal(0), "income": Decimal(0), "refund": Decimal(0), "transfer": Decimal(0)}
    by_category = defaultdict(Decimal)
    by_merchant = defaultdict(Decimal)
    by_income_merchant = defaultdict(Decimal)
    by_day = defaultdict(Decimal)
    by_method = defaultdict(Decimal)
    by_month = defaultdict(Decimal)
    by_weekday = defaultdict(Decimal)
    expenses = []
    type_counts = Counter()
    needs_review = []
    excluded_count = 0
    invalid_count = 0
    for tx in transactions:
        if not isinstance(tx, dict):
            invalid_count += 1
            continue
        if tx.get("excluded_from_totals"):
            excluded_count += 1
            continue
        tx_type = tx.get("type", "expense")
        if not isinstance(tx_type, str) or not tx_type:
            invalid_count += 1
            continue
        amount = safe_amount(tx.get("amount"))
        # Ask the rule table instead of re-deriving the conditions here.
        nature = nature_of(tx)
        settlement = settlement_of(tx)
        if not counts_as(tx, "expense") and tx_type == "expense":
            type_counts["advance"] += 1
            continue
        if not counts_as(tx, "income") and tx_type == "income":
            type_counts["advance_returned"] = type_counts.get("advance_returned", 0) + 1
            continue
        type_counts[tx_type] += 1
        if tx_type in totals:
            totals[tx_type] += amount
        merchant_value = tx.get("merchant")
        if not isinstance(merchant_value, str) or not merchant_value:
            merchant_value = "未填商户"
        method_value = tx.get("method")
        if not isinstance(method_value, str) or not method_value:
            method_value = "未填渠道"
        category_value = tx.get("category")
        if not isinstance(category_value, str) or not category_value:
            category_value = "其他"
        if tx_type == "income":
            by_income_merchant[merchant_value if merchant_value != "未填商户" else "未填来源"] += amount
        if tx_type == "expense":
            category = (category_parents or {}).get(category_value, category_value)
            merchant = merchant_value
            method = method_value
            day = tx_day(tx)
            by_category[category] += amount
            by_merchant[merchant] += amount
            by_day[day or "未知"] += amount
            by_method[method] += amount
            month = month_key(tx.get("occurred_at"))
            if month:
                by_month[month] += amount
            if day:
                try:
                    by_weekday[datetime.strptime(day, "%Y-%m-%d").weekday()] += amount
                except ValueError:
                    pass
            expenses.append(amount)
        confidence = tx.get("confidence")
        low_confidence = False
        try:
            low_confidence = confidence is not None and float(confidence) < 0.85
        except (TypeError, ValueError):
            low_confidence = True
        if low_confidence or (category_value == "其他"):
            needs_review.append(compact_candidate(tx))
    totals = {key: float(round(value, 2)) for key, value in totals.items()}
    totals["net"] = round(totals["income"] + totals["refund"] - totals["expense"], 2)
    days = period_day_count(period or {}, [tx for tx in transactions if isinstance(tx, dict) and not tx.get("excluded_from_totals")])
    daily_average = round(totals["expense"] / days, 2) if days else Decimal(0)
    outliers = []
    if expenses:
        median = sorted(expenses)[len(expenses) // 2]
        threshold = max(median * 3, 200)
        for tx in transactions:
            if not counts_as(tx, "expense"):
                continue
            amount = safe_amount(tx.get("amount"))
            if amount >= threshold:
                item = compact_candidate(tx)
                item["reason"] = f"偏大支出（阈值 {threshold:.2f}）"
                outliers.append(item)
    result = {
        "period": period or {"label": "全部"},
        "totals": totals,
        "by_category": dict(sorted(by_category.items(), key=lambda item: item[1], reverse=True)),
        "by_merchant": dict(sorted(by_merchant.items(), key=lambda item: item[1], reverse=True)),
        "by_income_merchant": {
            key: round(value, 2)
            for key, value in sorted(by_income_merchant.items(), key=lambda item: item[1], reverse=True)
        },
        "by_day": dict(sorted(by_day.items())),
        "by_method": dict(sorted(by_method.items(), key=lambda item: item[1], reverse=True)),
        "top_merchants": [
            {"merchant": name, "amount": round(amount, 2)}
            for name, amount in sorted(by_merchant.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
        "by_month": {key: round(value, 2) for key, value in sorted(by_month.items())},
        "by_weekday": {
            WEEKDAY_LABELS[index]: round(by_weekday.get(index, Decimal(0)), 2) for index in range(7)
        },
        "weekday": {
            "weekday": round(sum(by_weekday.get(index, Decimal(0)) for index in range(5)), 2),
            "weekend": round(by_weekday.get(5, Decimal(0)) + by_weekday.get(6, Decimal(0)), 2),
        },
        "count": sum(type_counts.values()),
        "excluded_count": excluded_count,
        "invalid_count": invalid_count,
        "income_count": type_counts["income"],
        "refund_count": type_counts["refund"],
        "daily_average": daily_average,
        "needs_review": needs_review[:20],
        "outliers": outliers[:10],
    }
    if previous_transactions is not None:
        previous = summarize(previous_transactions, period=previous_period_info, category_parents=category_parents)
        result["comparison"] = {
            "previous_label": (previous_period_info or {}).get("label"),
            "previous_totals": previous["totals"],
            "previous_count": previous["count"],
            "expense_change_pct": pct_change(totals["expense"], previous["totals"]["expense"]),
            "income_change_pct": pct_change(totals["income"], previous["totals"]["income"]),
            "net_change_pct": pct_change(totals["net"], previous["totals"]["net"]),
        }
    return json_numbers(result)


def category_entries(ledger):
    """Return persisted category definitions while accepting legacy string entries."""
    entries = []
    seen = set()
    for item in ledger.get("categories") or []:
        if isinstance(item, str):
            name, icon, parent = item.strip(), None, None
        elif isinstance(item, dict):
            name, icon, parent = str(item.get("name") or "").strip(), item.get("icon"), item.get("parent")
        else:
            continue
        if not name or name in seen:
            continue
        entries.append({"name": name, "icon": str(icon).strip() if icon else None, "parent": str(parent).strip() if parent else None})
        seen.add(name)
    return entries


def category_parent_map(ledger):
    parents = dict(DEFAULT_CATEGORY_PARENTS)
    for item in category_entries(ledger):
        if item["parent"]:
            parents[item["name"]] = item["parent"]
        else:
            parents.pop(item["name"], None)
    return parents


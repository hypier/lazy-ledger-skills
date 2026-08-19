#!/usr/bin/env python3
import argparse
import csv
import io
import json
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ledger_db import STORE_KIND, STORE_VERSION, atomic_write_json, read_json


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "ledger-viewer-template.html"
VALID_TYPES = {"expense", "income", "refund", "transfer"}
VALID_SOURCES = {"text", "image", "voice", "manual", "import"}
VALID_METHODS = {"wechat", "alipay", "cash", "card", "bank", "other"}
VALID_ACCOUNT_TYPES = {"cash", "wechat", "alipay", "bank", "credit", "other"}
RANGE_PRESETS = ("this-month", "last-month", "last7", "last30")
METHOD_TO_ACCOUNT_TYPE = {
    "wechat": "wechat",
    "alipay": "alipay",
    "cash": "cash",
    "card": "credit",
    "bank": "bank",
}
ACCOUNT_TYPE_TO_METHOD = {value: key for key, value in METHOD_TO_ACCOUNT_TYPE.items()}
ACCOUNT_TYPE_LABELS = {
    "cash": "现金",
    "wechat": "微信零钱",
    "alipay": "支付宝",
    "bank": "银行卡",
    "credit": "信用卡",
    "other": "其他",
}
ACCOUNT_ALIASES = {
    "微信零钱": "wechat",
    "微信支付": "wechat",
    "微信": "wechat",
    "支付宝余额": "alipay",
    "支付宝": "alipay",
    "余额宝": "alipay",
    "花呗": "alipay",
    "现金账户": "cash",
    "现金": "cash",
    "信用卡": "credit",
    "银行卡": "bank",
    "借记卡": "bank",
    "储蓄卡": "bank",
}
TRANSFER_ROUTE_RE = re.compile(
    r"(?:从)?(微信零钱|微信支付|微信|支付宝余额|支付宝|余额宝|现金账户|现金|信用卡|银行卡|借记卡|储蓄卡)"
    r"\s*(?:转到|转给|转入|->|→|到)\s*"
    r"(微信零钱|微信支付|微信|支付宝余额|支付宝|余额宝|现金账户|现金|信用卡|银行卡|借记卡|储蓄卡)"
)
REQUIRED_TX_FIELDS = {
    "id",
    "type",
    "amount",
    "currency",
    "category",
    "occurred_at",
    "source",
    "created_at",
    "updated_at",
}
CATEGORY_KEYWORDS = {
    "咖啡": ["星巴克", "瑞幸", "咖啡", "拿铁", "美式"],
    "餐饮": ["饭", "午饭", "晚饭", "早餐", "外卖", "火锅", "奶茶", "餐厅", "美团", "饿了么"],
    "交通": ["地铁", "公交", "打车", "滴滴", "高铁", "火车", "机票", "停车", "加油"],
    "购物": ["淘宝", "京东", "拼多多", "超市", "便利店", "衣服", "数码"],
    "居住": ["房租", "水电", "燃气", "物业", "宽带"],
    "娱乐": ["电影", "游戏", "演出", "KTV", "会员"],
    "医疗": ["医院", "药", "挂号", "体检"],
    "教育": ["课程", "书", "学费", "培训"],
    "人情": ["红包", "礼物", "请客"],
    "收入": ["工资", "奖金", "报销", "利息"],
}
KNOWN_MERCHANTS = ["星巴克", "瑞幸", "麦当劳", "肯德基", "喜茶", "奈雪", "盒马"]
FALLBACK_MERCHANTS = ["美团", "饿了么", "京东", "淘宝", "拼多多", "滴滴"]
TYPE_KEYWORDS = {
    "income": ["工资", "奖金", "收入", "利息", "报销到账"],
    "refund": ["退款", "退回", "返现"],
    "transfer": ["转账", "转入", "转出", "还信用卡"],
}
TYPE_LINE_MAP = {"支出": "expense", "收入": "income", "退款": "refund", "转账": "transfer"}
METHOD_KEYWORDS = {
    "wechat": ["微信支付", "微信"],
    "alipay": ["支付宝", "花呗", "余额宝"],
    "cash": ["现金"],
    "card": ["信用卡", "刷卡"],
    "bank": ["银行卡", "借记卡", "储蓄卡"],
}
ACCOUNT_KEYWORDS = {
    "微信零钱": ["微信零钱"],
    "支付宝": ["支付宝余额", "余额宝"],
    "现金": ["现金账户", "现金"],
    "信用卡": ["信用卡"],
}
TAG_KEYWORDS = {
    "报销": ["报销", "对公", "公司垫付"],
    "出差": ["出差", "差旅"],
    "订阅": ["订阅"],
}
TOTAL_HINTS = ("一共", "共", "合计", "总计", "券后", "实付", "满减")
DATE_TOKEN_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}月\d{1,2}[日号]?"
    r"|上周[一二三四五六日天]"
    r"|这?[个]?周[一二三四五六日天]"
    r"|今天|昨天|前天"
    r"|(?<!\d)\d{1,2}[/-]\d{1,2}(?!\d)"
)
AMOUNT_RE = re.compile(r"(?<![\d.])(?:¥|￥)?(\d+(?:\.\d+)?)(?:元|块|人民币)?(?![\d.])")
PAID_AMOUNT_RE = re.compile(r"(?:实付(?:金额)?|券后|支付)\s*(?:¥|￥)?(\d+(?:\.\d+)?)")
ORIGINAL_PRICE_RE = re.compile(r"原价\s*(?:¥|￥)?\d+(?:\.\d+)?")
WEEKDAY_ALIASES = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
METHOD_LABELS = {
    "wechat": "微信",
    "alipay": "支付宝",
    "cash": "现金",
    "card": "信用卡",
    "bank": "银行卡",
    "other": "其他",
}
TYPE_LABELS = {"expense": "支出", "income": "收入", "refund": "退款", "transfer": "转账"}


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_currency(currency=None):
    return currency or "CNY"


def local_today():
    return datetime.now().astimezone()


def last_day_of_month(year, month):
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def parse_when(value):
    if not value:
        return now_iso()
    value = value.strip()
    today = local_today()
    aliases = {
        "今天": today,
        "昨天": today - timedelta(days=1),
        "前天": today - timedelta(days=2),
    }
    if value in aliases:
        return aliases[value].replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if value.startswith("上周") and len(value) == 3 and value[-1] in WEEKDAY_ALIASES:
        current_week_start = today - timedelta(days=today.weekday())
        target = current_week_start - timedelta(days=7) + timedelta(days=WEEKDAY_ALIASES[value[-1]])
        return target.replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    weekday_match = re.fullmatch(r"(?:这?个?)周([一二三四五六日天])", value)
    if weekday_match:
        target = WEEKDAY_ALIASES[weekday_match.group(1)]
        delta = (today.weekday() - target) % 7
        day = today - timedelta(days=delta)
        return day.replace(hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    month_day = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日号]?", value)
    if month_day:
        month = int(month_day.group(1))
        day = int(month_day.group(2))
        try:
            parsed = today.replace(month=month, day=day, hour=12, minute=0, second=0, microsecond=0)
        except ValueError as exc:
            raise SystemExit(f"Unsupported date format: {value}") from exc
        if parsed.date() > today.date() + timedelta(days=1):
            parsed = parsed.replace(year=today.year - 1)
        return parsed.isoformat(timespec="seconds")
    slash_day = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", value)
    if slash_day:
        month = int(slash_day.group(1))
        day = int(slash_day.group(2))
        try:
            parsed = today.replace(month=month, day=day, hour=12, minute=0, second=0, microsecond=0)
        except ValueError as exc:
            raise SystemExit(f"Unsupported date format: {value}") from exc
        if parsed.date() > today.date() + timedelta(days=1):
            parsed = parsed.replace(year=today.year - 1)
        return parsed.isoformat(timespec="seconds")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().isoformat(timespec="seconds")
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            parsed = datetime.strptime(value, fmt)
            if "%H" not in fmt:
                parsed = parsed.replace(hour=12)
            return parsed.astimezone().isoformat(timespec="seconds")
        except ValueError:
            pass
    raise SystemExit(f"Unsupported date format: {value}")


def default_accounts(currency="CNY"):
    ts = now_iso()
    return [
        {
            "id": f"acc_{item[0]}",
            "name": item[1],
            "type": item[0],
            "currency": default_currency(currency),
            "opening_balance": 0.0,
            "archived": False,
            "created_at": ts,
        }
        for item in (
            ("cash", "现金"),
            ("wechat", "微信零钱"),
            ("alipay", "支付宝"),
            ("bank", "银行卡"),
            ("credit", "信用卡"),
        )
    ]


def empty_ledger(currency="CNY"):
    ts = now_iso()
    return {
        "schema_version": 1,
        "currency": default_currency(currency),
        "created_at": ts,
        "updated_at": ts,
        "transactions": [],
        "categories": [],
        "accounts": default_accounts(currency),
        "budgets": [],
        "store": STORE_KIND,
        "store_version": STORE_VERSION,
        "preferences": {"merchant_categories": {}, "merchant_aliases": {}, "default_account_id": "acc_wechat"},
    }


def ledger_preferences(ledger):
    prefs = ledger.setdefault("preferences", {})
    if not isinstance(prefs, dict):
        prefs = {}
        ledger["preferences"] = prefs
    prefs.setdefault("merchant_categories", {})
    prefs.setdefault("merchant_aliases", {})
    prefs.setdefault("default_account_id", "acc_wechat")
    if not isinstance(prefs["merchant_categories"], dict):
        prefs["merchant_categories"] = {}
    if not isinstance(prefs["merchant_aliases"], dict):
        prefs["merchant_aliases"] = {}
    return prefs


def ensure_accounts(ledger, currency="CNY"):
    if not isinstance(ledger.get("accounts"), list) or not ledger.get("accounts"):
        ledger["accounts"] = default_accounts(currency or ledger.get("currency"))
    return ledger["accounts"]


def visible_accounts(ledger):
    return [account for account in ensure_accounts(ledger) if isinstance(account, dict) and not account.get("archived")]


def default_account(ledger):
    prefs = ledger_preferences(ledger or {})
    wanted = prefs.get("default_account_id")
    accounts = visible_accounts(ledger or {})
    for account in accounts:
        if account.get("id") == wanted:
            return account
    return accounts[0] if accounts else None


def find_account(ledger, value):
    if not value:
        return None
    needle = str(value).strip()
    accounts = ensure_accounts(ledger or {})
    for account in accounts:
        if account.get("id") == needle or account.get("name") == needle:
            return account
    account_type = ACCOUNT_ALIASES.get(needle) or (needle if needle in VALID_ACCOUNT_TYPES else None)
    if not account_type:
        return None
    for account in visible_accounts(ledger or {}):
        if account.get("type") == account_type:
            return account
    return None


def account_for_method(ledger, method):
    account_type = METHOD_TO_ACCOUNT_TYPE.get(method)
    if not account_type:
        return None
    for account in visible_accounts(ledger or {}):
        if account.get("type") == account_type:
            return account
    return None


def infer_transfer_route(text):
    match = TRANSFER_ROUTE_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    if "还信用卡" in text:
        remainder = text.replace("还信用卡", " ")
        source = None
        for alias in sorted(ACCOUNT_ALIASES, key=len, reverse=True):
            if alias in remainder:
                source = alias
                break
        return source, "信用卡"
    return None


def apply_account_fields(proposal, ledger):
    route = infer_transfer_route(proposal.get("note") or "")
    if route:
        source_name, dest_name = route
        proposal["type"] = "transfer"
        if proposal.get("category") in (None, "其他"):
            proposal["category"] = "转账"
        source = find_account(ledger, source_name) or default_account(ledger)
        dest = find_account(ledger, dest_name)
        if source:
            proposal["account_id"] = source["id"]
            proposal["account"] = source["name"]
            proposal["method"] = proposal.get("method") or ACCOUNT_TYPE_TO_METHOD.get(source.get("type"))
        if dest:
            proposal["to_account_id"] = dest["id"]
            proposal["to_account"] = dest["name"]
        return proposal
    account = find_account(ledger, proposal.get("account_id") or proposal.get("account"))
    if not account and proposal.get("method"):
        account = account_for_method(ledger, proposal.get("method"))
    if not account:
        account = default_account(ledger) if proposal.get("type") != "transfer" else None
    dest = find_account(ledger, proposal.get("to_account_id") or proposal.get("to_account"))
    if account:
        proposal["account_id"] = account["id"]
        proposal["account"] = account["name"]
        if not proposal.get("method"):
            proposal["method"] = ACCOUNT_TYPE_TO_METHOD.get(account.get("type"))
    if dest:
        proposal["to_account_id"] = dest["id"]
        proposal["to_account"] = dest["name"]
    return proposal


def account_balances(ledger):
    accounts = [account for account in ensure_accounts(ledger) if isinstance(account, dict)]
    state = {}
    for account in accounts:
        opening = float(account.get("opening_balance") or 0)
        state[account["id"]] = {
            "id": account.get("id"),
            "name": account.get("name"),
            "type": account.get("type"),
            "archived": bool(account.get("archived")),
            "opening_balance": round(opening, 2),
            "balance": opening,
            "inflow": 0.0,
            "outflow": 0.0,
        }
    name_to_id = {account.get("name"): account.get("id") for account in accounts}

    def resolve_id(tx, key_id, key_name):
        value = tx.get(key_id)
        if value and value in state:
            return value
        name = tx.get(key_name)
        if name and name in name_to_id and name_to_id[name] in state:
            return name_to_id[name]
        return None

    for tx in ledger.get("transactions", []):
        if not isinstance(tx, dict):
            continue
        try:
            amount = float(tx.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        source_id = resolve_id(tx, "account_id", "account")
        dest_id = resolve_id(tx, "to_account_id", "to_account")
        tx_type = tx.get("type")
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
        item = state[account["id"]]
        item["balance"] = round(item["balance"], 2)
        item["inflow"] = round(item["inflow"], 2)
        item["outflow"] = round(item["outflow"], 2)
        if not item["archived"]:
            rows.append(item)
    return rows


def budgets_for_month(ledger, month):
    if not month:
        return []
    items = [budget for budget in ledger.get("budgets", []) if isinstance(budget, dict)]
    specific = [budget for budget in items if budget.get("month") == month]
    templates = [budget for budget in items if not budget.get("month")]
    used = {(budget.get("category") or "") for budget in specific}
    result = list(specific)
    for budget in templates:
        if (budget.get("category") or "") not in used:
            result.append(budget)
    return result


def budget_progress(ledger, transactions, period=None):
    month = (period or {}).get("month")
    if not month and (period or {}).get("start"):
        month = period["start"][:7]
        end = period.get("end") or ""
        if end[:7] != month:
            month = None
    budgets = budgets_for_month(ledger, month)
    expense_total = 0.0
    by_category = defaultdict(float)
    for tx in transactions:
        if tx.get("type") != "expense":
            continue
        amount = float(tx.get("amount") or 0)
        expense_total += amount
        by_category[tx.get("category") or "其他"] += amount
    rows = []
    for budget in budgets:
        try:
            limit = float(budget.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        category = budget.get("category") or None
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
    return rows


def normalize_ledger(data, currency="CNY"):
    if not isinstance(data, dict):
        raise SystemExit("Ledger JSON root must be an object")
    data.setdefault("schema_version", 1)
    if not data.get("currency"):
        data["currency"] = default_currency(currency)
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", now_iso())
    data.setdefault("transactions", [])
    data.setdefault("categories", [])
    data.setdefault("budgets", [])
    data["store"] = STORE_KIND
    data.setdefault("store_version", STORE_VERSION)
    ledger_preferences(data)
    ensure_accounts(data, data.get("currency") or currency)
    if not isinstance(data["transactions"], list):
        raise SystemExit("Ledger transactions must be a list")
    if not isinstance(data["categories"], list):
        data["categories"] = []
    if not isinstance(data["budgets"], list):
        data["budgets"] = []
    return data


def load_ledger(path, currency="CNY", create=True):
    ledger_path = Path(path)
    if not ledger_path.exists():
        if not create:
            raise SystemExit(f"Ledger not found: {ledger_path}")
        return empty_ledger(currency)
    return normalize_ledger(read_json(ledger_path), currency)


def save_ledger(path, ledger):
    ledger_path = Path(path)
    ledger["store"] = STORE_KIND
    ledger["store_version"] = STORE_VERSION
    ledger["updated_at"] = now_iso()
    atomic_write_json(ledger_path, ledger)


def make_id(occurred_at):
    stamp = occurred_at[:10].replace("-", "")
    return f"tx_{stamp}_{uuid.uuid4().hex[:8]}"


def coerce_tags(value):
    if not value:
        return []
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def unique_tags(*groups):
    seen = []
    for group in groups:
        for tag in coerce_tags(group):
            if tag not in seen:
                seen.append(tag)
    return seen


def load_optional_ledger(path, currency="CNY"):
    if not path:
        return empty_ledger(currency)
    ledger_path = Path(path)
    if not ledger_path.exists():
        return empty_ledger(currency)
    return load_ledger(ledger_path, currency=currency, create=False)


def validate_confidence(value):
    if value is None:
        return None
    if value < 0 or value > 1:
        raise SystemExit("Confidence must be between 0 and 1")
    return value


def apply_aliases_to_text(ledger, text):
    aliases = ledger_preferences(ledger or {}).get("merchant_aliases") or {}
    items = sorted(aliases.items(), key=lambda item: len(str(item[0])), reverse=True)
    for alias, canonical in items:
        if alias and canonical and alias in text:
            text = text.replace(alias, canonical)
    return text


def preferred_category(ledger, merchant, fallback):
    if not merchant:
        return fallback
    mapping = ledger_preferences(ledger or {}).get("merchant_categories") or {}
    return mapping.get(merchant) or fallback


def learn_merchant_category(ledger, merchant, category):
    if not merchant or not category or category == "其他":
        return
    ledger_preferences(ledger)["merchant_categories"][merchant] = category


def infer_type(text):
    for tx_type, keywords in TYPE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return tx_type
    return "expense"


def infer_category(text, tx_type="expense"):
    if tx_type == "income":
        return "收入"
    for category in CATEGORY_KEYWORDS:
        if category in text:
            return category
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "其他"


def infer_method(text):
    for method, keywords in METHOD_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return method
    return None


def infer_account(text, method=None):
    for account, keywords in ACCOUNT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return account
    return None


def infer_tags(text):
    tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            tags.append(tag)
    return tags


def extract_date_token(text):
    match = DATE_TOKEN_RE.search(text)
    return match.group(0) if match else None


def parse_amount_line(line):
    stripped = line.replace(",", "").replace(" ", "").replace("元", "")
    stripped = stripped.replace("¥", "").replace("￥", "")
    sign = "+" if stripped.startswith("+") else ("-" if stripped.startswith("-") else None)
    if stripped[:1] in "+-":
        stripped = stripped[1:]
    if re.fullmatch(r"\d+(?:\.\d{1,2})?", stripped):
        return sign, stripped
    return None


def extract_amount(text):
    paid = PAID_AMOUNT_RE.search(text)
    cleaned = ORIGINAL_PRICE_RE.sub(" ", text)
    if paid:
        cleaned = PAID_AMOUNT_RE.sub(" ", cleaned)
        cleaned = AMOUNT_RE.sub(" ", cleaned)
        return round(float(paid.group(1)), 2), cleaned
    matches = list(AMOUNT_RE.finditer(cleaned))
    if not matches:
        return None, cleaned
    values = [match.group(1) for match in matches]
    if len(values) > 1:
        if any(hint in text for hint in TOTAL_HINTS):
            match = matches[-1]
            cleaned = f"{cleaned[:match.start()]} {cleaned[match.end():]}"
            return round(float(match.group(1)), 2), cleaned
        raise SystemExit(f"Multiple amounts found: {', '.join(values)}")
    match = matches[0]
    cleaned = f"{cleaned[:match.start()]} {cleaned[match.end():]}"
    return round(float(match.group(1)), 2), cleaned


def strip_known_tokens(text):
    for category_name, keywords in CATEGORY_KEYWORDS.items():
        text = text.replace(category_name, " ")
        for keyword in keywords:
            if keyword in KNOWN_MERCHANTS:
                continue
            text = text.replace(keyword, " ")
    for keywords in list(TYPE_KEYWORDS.values()) + list(METHOD_KEYWORDS.values()) + list(TAG_KEYWORDS.values()):
        for keyword in keywords:
            text = text.replace(keyword, " ")
    for word in ("支出", "收入", "退款", "转账", "记账", "从", "转到", "转给", "转入"):
        text = text.replace(word, " ")
    return text


def infer_merchant(cleaned_text, category, original_text=""):
    haystack = f"{cleaned_text} {original_text}"
    for merchant in KNOWN_MERCHANTS:
        if merchant in haystack:
            return merchant
    text = strip_known_tokens(cleaned_text)
    text = re.sub(r"[，,。；;:：()（）\[\]【】]", " ", text)
    parts = [part.strip() for part in text.split() if part.strip()]
    parts = [part for part in parts if part != category]
    if parts:
        return parts[0]
    for merchant in FALLBACK_MERCHANTS:
        if merchant in original_text:
            return merchant
    return None


def split_line_by_amounts(line):
    line = line.strip()
    if not line:
        return []
    if PAID_AMOUNT_RE.search(line) or any(hint in line for hint in TOTAL_HINTS):
        return [line]
    date_token = extract_date_token(line)
    working = line.replace(date_token, " ", 1) if date_token else line
    working = ORIGINAL_PRICE_RE.sub(" ", working)
    matches = list(AMOUNT_RE.finditer(working))
    if len(matches) <= 1:
        return [line]
    items = []
    for index, match in enumerate(matches):
        start = 0 if index == 0 else matches[index - 1].end()
        piece = working[start:match.end()].strip()
        if piece:
            items.append(f"{date_token} {piece}".strip() if date_token else piece)
    return items or [line]


def split_text_items(text):
    chunks = [chunk.strip() for chunk in re.split(r"[\n；;]+", text) if chunk.strip()]
    items = []
    for chunk in chunks:
        items.extend(split_line_by_amounts(chunk))
    return items


def is_date_line(line):
    token = extract_date_token(line)
    if not token:
        return False
    remainder = line.replace(token, "").strip()
    remainder = re.sub(r"[\sT:：-]", "", remainder)
    return remainder == "" or bool(re.fullmatch(r"\d{2}", remainder))


def try_parse_stacked_rows(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return None
    amount_lines = [line for line in lines if parse_amount_line(line)]
    if len(amount_lines) < 1:
        return None
    rows = []
    buf = {"merchant": None, "tx_type": None, "amount": None, "date": None, "sign": None}

    def flush():
        if buf["amount"] is None:
            return
        tx_type = buf["tx_type"]
        if not tx_type:
            tx_type = "income" if buf["sign"] == "+" else "expense"
        type_word = TYPE_LABELS.get(tx_type, "")
        parts = [buf["date"] or "", buf["merchant"] or "", buf["amount"], type_word]
        rows.append(" ".join(part for part in parts if part))
        buf.update(merchant=None, tx_type=None, amount=None, date=None, sign=None)

    for line in lines:
        if line in TYPE_LINE_MAP:
            buf["tx_type"] = TYPE_LINE_MAP[line]
            continue
        if is_date_line(line):
            buf["date"] = extract_date_token(line)
            continue
        parsed_amount = parse_amount_line(line)
        if parsed_amount is not None:
            sign, amount = parsed_amount
            if buf["amount"] is not None:
                flush()
            buf["amount"] = amount
            buf["sign"] = sign
            continue
        if buf["amount"] is not None and buf["merchant"]:
            flush()
        buf["merchant"] = line
    flush()
    if len(rows) >= 1 and len(amount_lines) >= 1:
        return rows
    return None


def parse_text_transaction(text, ledger=None, currency="CNY", source="text", confidence=None):
    raw_text = text.strip()
    if not raw_text:
        raise SystemExit("Text is required")
    raw_text = apply_aliases_to_text(ledger, raw_text)
    date_token = extract_date_token(raw_text)
    date_text_removed = raw_text.replace(date_token, " ", 1) if date_token else raw_text
    amount, amount_removed = extract_amount(date_text_removed)
    if amount is None:
        raise SystemExit("Amount is required")
    tx_type = infer_type(raw_text)
    merchant = infer_merchant(amount_removed, infer_category(raw_text, tx_type), raw_text)
    category = preferred_category(ledger, merchant, infer_category(raw_text, tx_type))
    method = infer_method(raw_text)
    account = infer_account(raw_text, method)
    tags = infer_tags(raw_text)
    if confidence is None:
        confidence = 1.0
        if category == "其他":
            confidence = 0.75
        if not merchant and category == "其他":
            confidence = 0.6
    proposal = {
        "type": tx_type,
        "amount": amount,
        "currency": default_currency(currency or (ledger or {}).get("currency")),
        "category": category,
        "occurred_at": parse_when(date_token),
        "source": source,
        "confidence": confidence,
        "tags": tags,
        "note": raw_text,
    }
    if merchant:
        proposal["merchant"] = merchant
    if method:
        proposal["method"] = method
    if account:
        proposal["account"] = account
    return apply_account_fields(proposal, ledger)


def parse_text_transactions(text, ledger=None, currency="CNY", source="text"):
    raw_text = (text or "").strip()
    if not raw_text:
        raise SystemExit("Text is required")
    aliased = apply_aliases_to_text(ledger, raw_text)
    stacked = try_parse_stacked_rows(aliased)
    if stacked:
        items = stacked
        item_source = source if source != "text" else "import"
        confidence = 0.9
    else:
        items = split_text_items(aliased)
        item_source = source
        confidence = None
    return [
        parse_text_transaction(item, ledger=ledger, currency=currency, source=item_source, confidence=confidence)
        for item in items
    ]


def tx_day(tx):
    return (tx.get("occurred_at") or "")[:10]


def month_key(iso_value):
    return (iso_value or "")[:7]


def normalized_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def compact_candidate(tx):
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
    }


def likely_duplicate_candidates(transactions, tx):
    candidates = []
    tx_amount = round(float(tx.get("amount", 0) or 0), 2)
    tx_merchant = normalized_text(tx.get("merchant"))
    tx_note = normalized_text(tx.get("note"))
    for existing in transactions:
        try:
            existing_amount = round(float(existing.get("amount", 0) or 0), 2)
        except (TypeError, ValueError):
            continue
        if existing_amount != tx_amount:
            continue
        if tx_day(existing) != tx_day(tx):
            continue
        existing_merchant = normalized_text(existing.get("merchant"))
        existing_note = normalized_text(existing.get("note"))
        same_merchant = tx_merchant and existing_merchant and tx_merchant == existing_merchant
        same_note = tx_note and existing_note and (tx_note == existing_note or tx_note in existing_note or existing_note in tx_note)
        same_attachment = tx.get("attachment") and tx.get("attachment") == existing.get("attachment")
        if same_merchant or same_note or same_attachment:
            candidates.append(compact_candidate(existing))
    return candidates


def merge_overrides(proposal, args):
    merged = dict(proposal)
    if args.type:
        merged["type"] = args.type
    if args.category:
        merged["category"] = args.category
    if args.merchant is not None:
        merged["merchant"] = args.merchant
    if args.note is not None:
        merged["note"] = args.note
    if args.method:
        merged["method"] = args.method
    if args.account is not None:
        merged["account"] = args.account
    if getattr(args, "to_account", None):
        merged["to_account"] = args.to_account
    if args.tags:
        merged["tags"] = unique_tags(merged.get("tags"), args.tags)
    if args.date or args.occurred_at:
        merged["occurred_at"] = parse_when(args.date or args.occurred_at)
    if args.amount is not None:
        merged["amount"] = args.amount
    if args.source:
        merged["source"] = args.source
    if args.confidence is not None:
        merged["confidence"] = args.confidence
    return merged


def build_transaction_payload(
    ledger,
    *,
    amount_value,
    tx_type,
    currency,
    category,
    occurred_at,
    source,
    confidence,
    tags,
    merchant=None,
    note=None,
    attachment=None,
    method=None,
    account=None,
    account_id=None,
    to_account=None,
    to_account_id=None,
    tx_id=None,
):
    if amount_value is None:
        raise SystemExit("Amount is required unless --text includes one")
    amount = float(amount_value)
    if amount <= 0:
        raise SystemExit("Amount must be positive")
    if tx_type not in VALID_TYPES:
        raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if method and method not in VALID_METHODS:
        raise SystemExit(f"Method must be one of: {', '.join(sorted(VALID_METHODS))}")
    validate_confidence(confidence)
    ts = now_iso()
    tx = {
        "id": tx_id or make_id(occurred_at),
        "type": tx_type,
        "amount": round(amount, 2),
        "currency": default_currency(currency or ledger.get("currency")),
        "category": category or ("收入" if tx_type == "income" else "其他"),
        "occurred_at": occurred_at,
        "source": source,
        "confidence": 1.0 if confidence is None else confidence,
        "tags": coerce_tags(tags),
        "created_at": ts,
        "updated_at": ts,
    }
    if merchant:
        tx["merchant"] = merchant
    if note:
        tx["note"] = note
    if attachment:
        tx["attachment"] = attachment
    if method:
        tx["method"] = method
    if account:
        tx["account"] = account
    if account_id:
        tx["account_id"] = account_id
    if to_account:
        tx["to_account"] = to_account
    if to_account_id:
        tx["to_account_id"] = to_account_id
    return tx


def payload_from_proposal(ledger, proposal, args, tx_id=None):
    filled = apply_account_fields(dict(proposal), ledger)
    return build_transaction_payload(
        ledger,
        amount_value=filled.get("amount"),
        tx_type=filled.get("type") or "expense",
        currency=args.currency or filled.get("currency"),
        category=filled.get("category"),
        occurred_at=filled.get("occurred_at") or now_iso(),
        source=args.source or filled.get("source") or "text",
        confidence=filled.get("confidence", args.confidence),
        tags=filled.get("tags"),
        merchant=filled.get("merchant"),
        note=filled.get("note"),
        attachment=args.attachment,
        method=filled.get("method"),
        account=filled.get("account"),
        account_id=filled.get("account_id"),
        to_account=filled.get("to_account"),
        to_account_id=filled.get("to_account_id"),
        tx_id=tx_id,
    )


def month_snapshot(ledger, month):
    rows = [tx for tx in ledger.get("transactions", []) if month_key(tx.get("occurred_at")) == month]
    period = {"label": month, "start": f"{month}-01" if month else None, "end": None, "month": month}
    data = summarize(rows, period=period)
    data["budgets"] = budget_progress(ledger, rows, period)
    return {
        "key": month,
        "count": data["count"],
        "totals": data["totals"],
        "daily_average": data["daily_average"],
        "budgets": data["budgets"],
    }


def add_transaction(args):
    ledger = load_ledger(args.ledger, args.currency)
    if args.text:
        proposals = parse_text_transactions(
            args.text,
            ledger=ledger,
            currency=args.currency or ledger.get("currency"),
            source=args.source,
        )
        if len(proposals) == 1:
            proposals = [merge_overrides(proposals[0], args)]
        elif any(
            [
                args.amount is not None,
                args.type,
                args.category,
                args.merchant is not None,
                args.note is not None,
                args.method,
                args.account is not None,
                getattr(args, "to_account", None),
                args.date,
                args.occurred_at,
            ]
        ):
            proposals = [merge_overrides(item, args) for item in proposals]
    else:
        occurred_at = parse_when(args.date or args.occurred_at) if (args.date or args.occurred_at) else now_iso()
        proposals = [
            {
                "amount": args.amount,
                "type": args.type or "expense",
                "currency": args.currency,
                "category": args.category,
                "occurred_at": occurred_at,
                "source": args.source,
                "confidence": args.confidence,
                "tags": args.tags,
                "merchant": args.merchant,
                "note": args.note,
                "method": args.method,
                "account": args.account,
                "to_account": getattr(args, "to_account", None),
            }
        ]
    added = []
    known = list(ledger["transactions"])
    for index, proposal in enumerate(proposals):
        tx = payload_from_proposal(ledger, proposal, args, tx_id=args.id if index == 0 else None)
        duplicates = likely_duplicate_candidates(known, tx)
        if duplicates and not args.allow_duplicate:
            print(
                json.dumps(
                    {
                        "error": "likely_duplicate",
                        "message": "Likely duplicate transaction. Pass --allow-duplicate to add anyway.",
                        "candidates": duplicates,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stderr,
            )
            raise SystemExit(2)
        ledger["transactions"].append(tx)
        known.append(tx)
        if args.category and tx.get("merchant"):
            learn_merchant_category(ledger, tx.get("merchant"), tx.get("category"))
        added.append(tx)
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    month = month_key(added[0].get("occurred_at")) if added else month_key(now_iso())
    print(
        json.dumps(
            {"added": added, "count": len(added), "month": month_snapshot(ledger, month)},
            ensure_ascii=False,
            indent=2,
        )
    )


def shift_month(year, month, delta):
    month += delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return year, month


def resolve_period(month=None, range_name=None, start=None, end=None, now=None):
    today = (now or local_today()).date()
    if start:
        start = start[:10]
    if end:
        end = end[:10]
    if month:
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
    if start or end:
        label = f"{start or '…'} ~ {end or '…'}"
        return {"label": label, "start": start, "end": end, "month": None}
    return {"label": "全部", "start": None, "end": None, "month": None}


def previous_period(period):
    start = period.get("start")
    end = period.get("end")
    if period.get("month"):
        year, mon = shift_month(int(period["month"][:4]), int(period["month"][5:7]), -1)
        last = last_day_of_month(year, mon)
        month = f"{year:04d}-{mon:02d}"
        return {"label": month, "start": f"{month}-01", "end": f"{month}-{last:02d}", "month": month}
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
):
    rows = []
    q = (query or "").lower()
    for tx in transactions:
        day = tx_day(tx)
        if month and month_key(tx.get("occurred_at")) != month:
            continue
        if start and day and day < start:
            continue
        if end and day and day > end:
            continue
        if tx_type and tx.get("type") != tx_type:
            continue
        if category and tx.get("category") != category:
            continue
        if method and tx.get("method") != method:
            continue
        if account:
            haystack = {tx.get("account_id"), tx.get("account"), tx.get("to_account_id"), tx.get("to_account")}
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


def filter_from_args(transactions, args):
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
    )
    return rows, period


def period_day_count(period, transactions):
    start, end = period.get("start"), period.get("end")
    if start and end:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        return max((end_d - start_d).days + 1, 1)
    days = [tx_day(tx) for tx in transactions if tx_day(tx)]
    if not days:
        return 1
    start_d = datetime.strptime(min(days), "%Y-%m-%d").date()
    end_d = datetime.strptime(max(days), "%Y-%m-%d").date()
    return max((end_d - start_d).days + 1, 1)


def pct_change(new, old):
    if old == 0:
        return None if new == 0 else 100.0
    return round((new - old) / old * 100, 1)


def summarize(transactions, period=None, previous_transactions=None, previous_period_info=None):
    totals = {"expense": 0.0, "income": 0.0, "refund": 0.0, "transfer": 0.0}
    by_category = defaultdict(float)
    by_merchant = defaultdict(float)
    by_day = defaultdict(float)
    by_method = defaultdict(float)
    expenses = []
    needs_review = []
    for tx in transactions:
        tx_type = tx.get("type", "expense")
        amount = float(tx.get("amount", 0) or 0)
        if tx_type in totals:
            totals[tx_type] += amount
        if tx_type == "expense":
            category = tx.get("category") or "其他"
            merchant = tx.get("merchant") or "未填商户"
            method = tx.get("method") or "未填渠道"
            by_category[category] += amount
            by_merchant[merchant] += amount
            by_day[tx_day(tx) or "未知"] += amount
            by_method[method] += amount
            expenses.append(amount)
        confidence = tx.get("confidence")
        low_confidence = False
        try:
            low_confidence = confidence is not None and float(confidence) < 0.85
        except (TypeError, ValueError):
            low_confidence = True
        if low_confidence or (tx.get("category") == "其他"):
            needs_review.append(compact_candidate(tx))
    totals = {key: round(value, 2) for key, value in totals.items()}
    totals["net"] = round(totals["income"] + totals["refund"] - totals["expense"], 2)
    days = period_day_count(period or {}, transactions)
    daily_average = round(totals["expense"] / days, 2) if days else 0.0
    outliers = []
    if expenses:
        median = sorted(expenses)[len(expenses) // 2]
        threshold = max(median * 3, 200)
        for tx in transactions:
            if tx.get("type") != "expense":
                continue
            amount = float(tx.get("amount", 0) or 0)
            if amount >= threshold:
                item = compact_candidate(tx)
                item["reason"] = f"偏大支出（阈值 {threshold:.2f}）"
                outliers.append(item)
    result = {
        "period": period or {"label": "全部"},
        "totals": totals,
        "by_category": dict(sorted(by_category.items(), key=lambda item: item[1], reverse=True)),
        "by_merchant": dict(sorted(by_merchant.items(), key=lambda item: item[1], reverse=True)),
        "by_day": dict(sorted(by_day.items())),
        "by_method": dict(sorted(by_method.items(), key=lambda item: item[1], reverse=True)),
        "top_merchants": [
            {"merchant": name, "amount": round(amount, 2)}
            for name, amount in sorted(by_merchant.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
        "count": len(transactions),
        "daily_average": daily_average,
        "needs_review": needs_review[:20],
        "outliers": outliers[:10],
    }
    if previous_transactions is not None:
        previous = summarize(previous_transactions, period=previous_period_info)
        result["comparison"] = {
            "previous_label": (previous_period_info or {}).get("label"),
            "previous_totals": previous["totals"],
            "previous_count": previous["count"],
            "expense_change_pct": pct_change(totals["expense"], previous["totals"]["expense"]),
            "income_change_pct": pct_change(totals["income"], previous["totals"]["income"]),
            "net_change_pct": pct_change(totals["net"], previous["totals"]["net"]),
        }
    return result


def format_money(amount, currency="CNY"):
    symbol = "¥" if currency == "CNY" else f"{currency} "
    return f"{symbol}{float(amount):,.2f}"


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
        f"支出 **{format_money(totals['expense'], currency)}** · 收入 {format_money(totals['income'], currency)} · 退款 {format_money(totals['refund'], currency)} · 净额 **{format_money(totals['net'], currency)}**",
        f"共 {data['count']} 笔 · 日均支出 {format_money(data['daily_average'], currency)}",
    ]
    comparison = data.get("comparison")
    if comparison:
        lines.append(
            f"较{comparison.get('previous_label') or '上期'}：支出 {format_pct(comparison.get('expense_change_pct'))} · 净额 {format_pct(comparison.get('net_change_pct'))}"
        )
    if data["by_category"]:
        lines.extend(["", "### 分类支出"])
        for category, amount in data["by_category"].items():
            share = (amount / totals["expense"] * 100) if totals["expense"] else 0
            lines.append(f"- {category} {format_money(amount, currency)} ({share:.0f}%)")
    if data.get("top_merchants"):
        lines.extend(["", "### 商户"])
        for item in data["top_merchants"][:5]:
            lines.append(f"- {item['merchant']} {format_money(item['amount'], currency)}")
    if data.get("by_method"):
        lines.extend(["", "### 支付渠道"])
        for method, amount in list(data["by_method"].items())[:5]:
            label = METHOD_LABELS.get(method, method)
            lines.append(f"- {label} {format_money(amount, currency)}")
    if data.get("accounts"):
        lines.extend(["", "### 账户"])
        for item in data["accounts"]:
            lines.append(f"- {item['name']} {format_money(item['balance'], currency)}")
    if data.get("budgets"):
        lines.extend(["", "### 预算"])
        for item in data["budgets"]:
            status = "超支" if item["over"] else "剩余"
            lines.append(
                f"- {item['category']} {format_money(item['limit'], currency)} · 已花 {format_money(item['spent'], currency)} · {status} {format_money(abs(item['remaining']), currency)}"
            )
    if data.get("outliers"):
        lines.extend(["", "### 偏大支出"])
        for item in data["outliers"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('category')} {format_money(item.get('amount'), currency)}"
            )
    if data.get("needs_review"):
        lines.extend(["", "### 待复核"])
        for item in data["needs_review"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} · {item.get('category')} {format_money(item.get('amount'), currency)}"
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
    rows = filter_transactions(
        ledger["transactions"],
        query=getattr(args, "query", None),
        tx_type=getattr(args, "type", None),
        category=getattr(args, "category", None),
        method=getattr(args, "method", None),
        start=period["start"],
        end=period["end"],
        account=getattr(args, "account", None),
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
            )
    data = summarize(rows, period=period, previous_transactions=previous_rows, previous_period_info=previous_info)
    data["accounts"] = account_balances(ledger)
    data["budgets"] = budget_progress(ledger, rows, period)
    return data


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
        months = [month_key(tx.get("occurred_at")) for tx in ledger.get("transactions", []) if month_key(tx.get("occurred_at"))]
        if months:
            text += f"\n最近有记录的月份是 {max(months)}。"
    print(text)


def format_list_row(tx):
    date = tx_day(tx)
    merchant = tx.get("merchant") or "-"
    account = tx.get("account") or "-"
    dest = f"→{tx.get('to_account')}" if tx.get("to_account") else ""
    method = METHOD_LABELS.get(tx.get("method"), tx.get("method") or "-")
    note = tx.get("note") or ""
    amount = float(tx.get("amount", 0) or 0)
    return (
        f"{tx.get('id')} | {date} | {tx.get('type')} | {tx.get('category')} | "
        f"{amount:.2f} | {merchant} | {account}{dest} | {method} | {note}"
    )


def list_command(args):
    ledger = load_ledger(args.ledger, create=False)
    rows, _period = filter_from_args(ledger["transactions"], args)
    rows.sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    selected = rows[: args.limit]
    if args.json:
        print(json.dumps(selected, ensure_ascii=False, indent=2))
        return
    for tx in selected:
        print(format_list_row(tx))


def find_command(args):
    ledger = load_ledger(args.ledger, create=False)
    if args.text:
        proposal = parse_text_transaction(
            args.text,
            ledger=ledger,
            currency=args.currency or ledger.get("currency", "CNY"),
        )
        scored = []
        for tx in ledger["transactions"]:
            score = 0
            try:
                if round(float(tx.get("amount", 0) or 0), 2) == round(float(proposal["amount"]), 2):
                    score += 2
            except (TypeError, ValueError):
                pass
            if tx_day(tx) == tx_day(proposal):
                score += 2
            if normalized_text(tx.get("merchant")) and normalized_text(tx.get("merchant")) == normalized_text(proposal.get("merchant")):
                score += 2
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


def find_transaction(ledger, tx_id):
    for index, tx in enumerate(ledger["transactions"]):
        if tx.get("id") == tx_id:
            return index, tx
    raise SystemExit(f"Transaction not found: {tx_id}")


def update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    _, tx = find_transaction(ledger, args.id)
    if args.amount is not None:
        amount = float(args.amount)
        if amount <= 0:
            raise SystemExit("Amount must be positive")
        tx["amount"] = round(amount, 2)
    if args.type is not None:
        tx["type"] = args.type
    if args.currency is not None:
        tx["currency"] = args.currency
    if args.category is not None:
        tx["category"] = args.category
        learn_merchant_category(ledger, tx.get("merchant"), args.category)
    if args.merchant is not None:
        if args.merchant == "":
            tx.pop("merchant", None)
        else:
            tx["merchant"] = args.merchant
    if args.note is not None:
        if args.note == "":
            tx.pop("note", None)
        else:
            tx["note"] = args.note
    if args.date or args.occurred_at:
        tx["occurred_at"] = parse_when(args.date or args.occurred_at)
    if args.source is not None:
        tx["source"] = args.source
    if args.confidence is not None:
        validate_confidence(args.confidence)
        tx["confidence"] = args.confidence
    if args.tags is not None:
        tx["tags"] = coerce_tags(args.tags)
    if args.attachment is not None:
        if args.attachment == "":
            tx.pop("attachment", None)
        else:
            tx["attachment"] = args.attachment
    if args.method is not None:
        if args.method == "":
            tx.pop("method", None)
        else:
            tx["method"] = args.method
    if args.account is not None:
        if args.account == "":
            tx.pop("account", None)
            tx.pop("account_id", None)
        else:
            resolved = find_account(ledger, args.account)
            if resolved:
                tx["account"] = resolved["name"]
                tx["account_id"] = resolved["id"]
            else:
                tx["account"] = args.account
                tx.pop("account_id", None)
    if getattr(args, "to_account", None) is not None:
        if args.to_account == "":
            tx.pop("to_account", None)
            tx.pop("to_account_id", None)
        else:
            resolved = find_account(ledger, args.to_account)
            if resolved:
                tx["to_account"] = resolved["name"]
                tx["to_account_id"] = resolved["id"]
            else:
                tx["to_account"] = args.to_account
                tx.pop("to_account_id", None)
    tx["updated_at"] = now_iso()
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    print(json.dumps(tx, ensure_ascii=False, indent=2))


def delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    index, tx = find_transaction(ledger, args.id)
    if not args.yes:
        date = (tx.get("occurred_at") or "")[:10]
        print(f"Refusing to delete without --yes: {tx.get('id')} {date} {tx.get('category')} {tx.get('amount')}")
        return
    removed = ledger["transactions"].pop(index)
    save_ledger(args.ledger, ledger)
    print(json.dumps(removed, ensure_ascii=False, indent=2))


def render_command(args):
    ledger = load_ledger(args.ledger, create=False)
    template_path = Path(args.template) if args.template else DEFAULT_TEMPLATE
    html = template_path.read_text(encoding="utf-8")
    payload = json.dumps(ledger, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    html = html.replace("__LEDGER_DATA__", payload)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    print(str(output.resolve()))


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


def add_issue(issues, code, message, tx=None, **extra):
    issue = {"code": code, "message": message}
    if tx and isinstance(tx, dict):
        issue["id"] = tx.get("id")
    issue.update(extra)
    issues.append(issue)


def doctor_report(ledger):
    issues = []
    transactions = ledger.get("transactions", [])
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            add_issue(issues, "invalid_transaction", "Transaction must be an object", index=index)
            continue
        for field in sorted(REQUIRED_TX_FIELDS):
            if field not in tx:
                add_issue(issues, "missing_field", f"Missing required field: {field}", tx, field=field)
        if tx.get("type") not in VALID_TYPES:
            add_issue(issues, "invalid_type", "Invalid transaction type", tx, value=tx.get("type"))
        if tx.get("method") not in (None, "") and tx.get("method") not in VALID_METHODS:
            add_issue(issues, "invalid_method", "Invalid payment method", tx, value=tx.get("method"))
        if tx.get("account_id") and not find_account(ledger, tx.get("account_id")):
            add_issue(issues, "unknown_account", "Unknown account_id", tx, value=tx.get("account_id"))
        if tx.get("to_account_id") and not find_account(ledger, tx.get("to_account_id")):
            add_issue(issues, "unknown_account", "Unknown to_account_id", tx, value=tx.get("to_account_id"))
        if tx.get("type") == "transfer" and not (tx.get("to_account_id") or tx.get("to_account")):
            add_issue(issues, "missing_transfer_dest", "Transfer is missing destination account", tx)
        try:
            amount = float(tx.get("amount", 0))
            if amount <= 0:
                add_issue(issues, "invalid_amount", "Amount must be positive", tx, value=tx.get("amount"))
        except (TypeError, ValueError):
            add_issue(issues, "invalid_amount", "Amount must be numeric", tx, value=tx.get("amount"))
        confidence = tx.get("confidence")
        if confidence is not None:
            try:
                confidence_value = float(confidence)
                if confidence_value < 0 or confidence_value > 1:
                    add_issue(issues, "invalid_confidence", "Confidence must be between 0 and 1", tx, value=confidence)
            except (TypeError, ValueError):
                add_issue(issues, "invalid_confidence", "Confidence must be numeric", tx, value=confidence)
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            continue
        duplicates = likely_duplicate_candidates(transactions[:index], tx)
        for duplicate in duplicates:
            add_issue(
                issues,
                "likely_duplicate",
                "Likely duplicate transaction",
                tx,
                duplicate_id=duplicate.get("id"),
            )
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}


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


def import_tsv_command(args):
    ledger = load_ledger(args.ledger, args.currency)
    input_path = Path(args.input)
    added_ids = []
    with input_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise SystemExit("TSV header is required")
        for line_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            amount_value = row.get("amount")
            occurred_at = row.get("occurred_at")
            if not amount_value:
                raise SystemExit(f"Missing amount on line {line_number}")
            if not occurred_at:
                raise SystemExit(f"Missing occurred_at on line {line_number}")
            confidence_value = row.get("confidence")
            confidence = float(confidence_value) if confidence_value not in (None, "") else 1.0
            tx = build_transaction_payload(
                ledger,
                amount_value=amount_value,
                tx_type=row.get("type") or "expense",
                currency=row.get("currency") or args.currency,
                category=row.get("category"),
                occurred_at=parse_when(occurred_at),
                source=row.get("source") or "import",
                confidence=confidence,
                tags=row.get("tags"),
                merchant=row.get("merchant"),
                note=row.get("note"),
                attachment=row.get("attachment"),
                method=row.get("method") or None,
                account=row.get("account") or None,
                to_account=row.get("to_account") or None,
                tx_id=row.get("id"),
            )
            apply_account_fields(tx, ledger)
            duplicates = likely_duplicate_candidates(ledger["transactions"], tx)
            if duplicates and not args.allow_duplicate:
                print(
                    json.dumps(
                        {
                            "error": "likely_duplicate",
                            "line": line_number,
                            "message": "Likely duplicate transaction in import. Pass --allow-duplicate to import anyway.",
                            "candidates": duplicates,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    file=sys.stderr,
                )
                raise SystemExit(2)
            ledger["transactions"].append(tx)
            added_ids.append(tx["id"])
    ledger["transactions"].sort(key=lambda item: item.get("occurred_at", ""), reverse=True)
    save_ledger(args.ledger, ledger)
    print(json.dumps({"input": str(input_path), "added": len(added_ids), "ids": added_ids}, ensure_ascii=False, indent=2))


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
        output.write_text(payload, encoding="utf-8")
        print(str(output.resolve()))
        return
    sys.stdout.write(payload if payload.endswith("\n") else payload + "\n")


def prefer_command(args):
    ledger = load_ledger(args.ledger, create=True)
    prefs = ledger_preferences(ledger)
    if args.merchant and args.category:
        prefs["merchant_categories"][args.merchant] = args.category
    if args.alias and args.merchant:
        prefs["merchant_aliases"][args.alias] = args.merchant
    if not ((args.merchant and args.category) or (args.alias and args.merchant)):
        raise SystemExit("Provide --merchant with --category, and/or --alias with --merchant")
    save_ledger(args.ledger, ledger)
    print(json.dumps(prefs, ensure_ascii=False, indent=2))


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


def account_add_command(args):
    ledger = load_ledger(args.ledger, create=True)
    if args.type not in VALID_ACCOUNT_TYPES:
        raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}")
    opening = float(args.opening or 0)
    account = {
        "id": args.id or f"acc_{uuid.uuid4().hex[:8]}",
        "name": args.name,
        "type": args.type,
        "currency": ledger.get("currency") or "CNY",
        "opening_balance": round(opening, 2),
        "archived": False,
        "created_at": now_iso(),
    }
    if any(existing.get("id") == account["id"] for existing in ensure_accounts(ledger)):
        raise SystemExit(f"Account already exists: {account['id']}")
    ledger["accounts"].append(account)
    if args.default:
        ledger_preferences(ledger)["default_account_id"] = account["id"]
    save_ledger(args.ledger, ledger)
    print(json.dumps(account, ensure_ascii=False, indent=2))


def account_update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    account = find_account(ledger, args.id)
    if not account:
        raise SystemExit(f"Account not found: {args.id}")
    if args.name:
        account["name"] = args.name
    if args.type:
        if args.type not in VALID_ACCOUNT_TYPES:
            raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}")
        account["type"] = args.type
    if args.opening is not None:
        account["opening_balance"] = round(float(args.opening), 2)
    if args.archive:
        account["archived"] = True
    if args.unarchive:
        account["archived"] = False
    if args.default:
        ledger_preferences(ledger)["default_account_id"] = account["id"]
    account["updated_at"] = now_iso()
    save_ledger(args.ledger, ledger)
    print(json.dumps(account, ensure_ascii=False, indent=2))


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


def budget_set_command(args):
    ledger = load_ledger(args.ledger, create=True)
    amount = round(float(args.amount), 2)
    if amount <= 0:
        raise SystemExit("Budget amount must be positive")
    month = args.month
    category = args.category or None
    found = None
    for budget in ledger.setdefault("budgets", []):
        if budget.get("month") == month and (budget.get("category") or None) == category:
            found = budget
            break
    ts = now_iso()
    if found:
        found["amount"] = amount
        found["updated_at"] = ts
        budget = found
    else:
        slug = f"{month or 'any'}_{category or 'all'}"
        budget = {
            "id": f"bud_{re.sub(r'[^a-zA-Z0-9]+', '_', slug)}",
            "month": month,
            "category": category,
            "amount": amount,
            "created_at": ts,
            "updated_at": ts,
        }
        ledger["budgets"].append(budget)
    save_ledger(args.ledger, ledger)
    print(json.dumps(budget, ensure_ascii=False, indent=2))


def budget_delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    remaining = []
    removed = None
    for budget in ledger.get("budgets", []):
        if budget.get("id") == args.id:
            removed = budget
        else:
            remaining.append(budget)
    if not removed:
        raise SystemExit(f"Budget not found: {args.id}")
    ledger["budgets"] = remaining
    save_ledger(args.ledger, ledger)
    print(json.dumps(removed, ensure_ascii=False, indent=2))


def init_command(args):
    path = Path(args.ledger)
    if path.exists() and not args.force:
        print(f"Ledger already exists: {path}")
        return
    save_ledger(path, empty_ledger(args.currency))
    print(str(path.resolve()))


def add_common_tx_flags(parser, *, require_type=False, source_default=None, include_text=False):
    parser.add_argument("--amount", default=None)
    if include_text:
        parser.add_argument("--text", default=None, help="Parse casual text, possibly multiple items")
    parser.add_argument("--type", default=None, choices=sorted(VALID_TYPES), required=require_type)
    parser.add_argument("--currency", default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument("--merchant", default=None)
    parser.add_argument("--note", default=None)
    parser.add_argument("--date", default=None)
    parser.add_argument("--occurred-at", default=None)
    parser.add_argument("--source", default=source_default, choices=sorted(VALID_SOURCES))
    parser.add_argument("--confidence", type=float, default=None)
    parser.add_argument("--tags", default=None)
    parser.add_argument("--attachment", default=None)
    parser.add_argument("--method", default=None, choices=sorted(VALID_METHODS))
    parser.add_argument("--account", default=None)
    parser.add_argument("--to-account", dest="to_account", default=None)


def add_filter_flags(parser, *, limit=None, json_flag=False, compare=False):
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--month", default=None, help="YYYY-MM")
    parser.add_argument("--range", default=None, choices=list(RANGE_PRESETS))
    parser.add_argument("--start", default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD")
    parser.add_argument("--query", default=None)
    parser.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
    parser.add_argument("--category", default=None)
    parser.add_argument("--method", default=None, choices=sorted(VALID_METHODS))
    parser.add_argument("--account", default=None, help="Filter by account id or name")
    if json_flag:
        parser.add_argument("--json", action="store_true")
    if compare:
        parser.add_argument("--compare", action="store_true", help="Compare with the previous equal period")
    if limit is not None:
        parser.add_argument("--limit", type=int, default=limit)


def build_parser():
    parser = argparse.ArgumentParser(description="Lazy Ledger JSON bookkeeping tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create an empty ledger")
    p_init.add_argument("--ledger", required=True)
    p_init.add_argument("--currency", default="CNY")
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=init_command)

    p_add = sub.add_parser("add", help="Add one or more transactions")
    p_add.add_argument("--ledger", required=True)
    add_common_tx_flags(p_add, include_text=True)
    p_add.add_argument("--id", default=None)
    p_add.add_argument("--allow-duplicate", action="store_true")
    p_add.set_defaults(func=add_transaction)

    p_parse = sub.add_parser("parse", help="Preview casual text without writing")
    p_parse.add_argument("--text", required=True)
    p_parse.add_argument("--ledger", default=None)
    p_parse.add_argument("--currency", default="CNY")
    p_parse.add_argument("--source", default="text", choices=sorted(VALID_SOURCES))
    p_parse.set_defaults(func=parse_command)

    p_update = sub.add_parser("update", help="Update one transaction by id")
    p_update.add_argument("--ledger", required=True)
    p_update.add_argument("--id", required=True)
    add_common_tx_flags(p_update)
    p_update.set_defaults(func=update_command)

    p_delete = sub.add_parser("delete", help="Delete one transaction by id")
    p_delete.add_argument("--ledger", required=True)
    p_delete.add_argument("--id", required=True)
    p_delete.add_argument("--yes", action="store_true")
    p_delete.set_defaults(func=delete_command)

    p_summary = sub.add_parser("summary", help="Summarize spending with category, merchant, and comparison")
    add_filter_flags(p_summary, json_flag=True, compare=True)
    p_summary.set_defaults(func=summary_command)

    p_show = sub.add_parser("show", help="Human-readable markdown summary for chat")
    add_filter_flags(p_show, json_flag=True, compare=True)
    p_show.set_defaults(func=show_command)

    p_list = sub.add_parser("list", help="List transactions")
    add_filter_flags(p_list, limit=20, json_flag=True)
    p_list.set_defaults(func=list_command)

    p_recent = sub.add_parser("recent", help="List recent transactions")
    add_filter_flags(p_recent, limit=5, json_flag=True)
    p_recent.set_defaults(func=recent_command)

    p_find = sub.add_parser("find", help="Find a transaction from casual text or query")
    add_filter_flags(p_find, limit=10, json_flag=True)
    p_find.add_argument("--text", default=None)
    p_find.add_argument("--currency", default=None)
    p_find.set_defaults(func=find_command)

    p_prefer = sub.add_parser("prefer", help="Remember merchant category or alias")
    p_prefer.add_argument("--ledger", required=True)
    p_prefer.add_argument("--merchant", default=None)
    p_prefer.add_argument("--category", default=None)
    p_prefer.add_argument("--alias", default=None)
    p_prefer.set_defaults(func=prefer_command)

    p_account = sub.add_parser("account", help="List, add, or update accounts")
    acc = p_account.add_subparsers(dest="account_command", required=True)
    p_acc_list = acc.add_parser("list", help="List accounts and computed balances")
    p_acc_list.add_argument("--ledger", required=True)
    p_acc_list.add_argument("--json", action="store_true")
    p_acc_list.set_defaults(func=account_list_command)
    p_acc_add = acc.add_parser("add", help="Add an account")
    p_acc_add.add_argument("--ledger", required=True)
    p_acc_add.add_argument("--name", required=True)
    p_acc_add.add_argument("--type", required=True, choices=sorted(VALID_ACCOUNT_TYPES))
    p_acc_add.add_argument("--opening", default="0")
    p_acc_add.add_argument("--id", default=None)
    p_acc_add.add_argument("--default", action="store_true")
    p_acc_add.set_defaults(func=account_add_command)
    p_acc_update = acc.add_parser("update", help="Update an account")
    p_acc_update.add_argument("--ledger", required=True)
    p_acc_update.add_argument("--id", required=True)
    p_acc_update.add_argument("--name", default=None)
    p_acc_update.add_argument("--type", default=None, choices=sorted(VALID_ACCOUNT_TYPES))
    p_acc_update.add_argument("--opening", default=None)
    p_acc_update.add_argument("--archive", action="store_true")
    p_acc_update.add_argument("--unarchive", action="store_true")
    p_acc_update.add_argument("--default", action="store_true")
    p_acc_update.set_defaults(func=account_update_command)

    p_budget = sub.add_parser("budget", help="Set or inspect monthly budgets")
    bud = p_budget.add_subparsers(dest="budget_command", required=True)
    p_bud_list = bud.add_parser("list", help="Show budget progress")
    p_bud_list.add_argument("--ledger", required=True)
    p_bud_list.add_argument("--month", default=None, help="YYYY-MM")
    p_bud_list.add_argument("--json", action="store_true")
    p_bud_list.set_defaults(func=budget_list_command)
    p_bud_set = bud.add_parser("set", help="Create or replace a budget")
    p_bud_set.add_argument("--ledger", required=True)
    p_bud_set.add_argument("--amount", required=True)
    p_bud_set.add_argument("--month", default=None, help="YYYY-MM; omit for every month")
    p_bud_set.add_argument("--category", default=None, help="Omit for overall monthly spend")
    p_bud_set.set_defaults(func=budget_set_command)
    p_bud_del = bud.add_parser("delete", help="Delete a budget by id")
    p_bud_del.add_argument("--ledger", required=True)
    p_bud_del.add_argument("--id", required=True)
    p_bud_del.set_defaults(func=budget_delete_command)

    p_doctor = sub.add_parser("doctor", help="Validate ledger data and report likely duplicates")
    p_doctor.add_argument("--ledger", required=True)
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.set_defaults(func=doctor_command)

    p_import_tsv = sub.add_parser("import-tsv", help="Import multiple transactions from a TSV file")
    p_import_tsv.add_argument("--ledger", required=True)
    p_import_tsv.add_argument("--input", required=True)
    p_import_tsv.add_argument("--currency", default=None)
    p_import_tsv.add_argument("--allow-duplicate", action="store_true")
    p_import_tsv.set_defaults(func=import_tsv_command)

    p_export = sub.add_parser("export", help="Export filtered transactions as CSV or JSON")
    add_filter_flags(p_export)
    p_export.add_argument("--format", default="csv", choices=["csv", "json"])
    p_export.add_argument("--output", default=None)
    p_export.set_defaults(func=export_command)

    p_render = sub.add_parser("render", help="Generate a self-contained HTML dashboard")
    p_render.add_argument("--ledger", required=True)
    p_render.add_argument("--output", required=True)
    p_render.add_argument("--template", default=None)
    p_render.set_defaults(func=render_command)

    p_serve = sub.add_parser("serve", help="Open a local live page to view and edit the ledger")
    p_serve.add_argument("--ledger", required=True)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--open", action="store_true")
    p_serve.set_defaults(func=serve_command)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])

#!/usr/bin/env python3
import argparse
import csv
import functools
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ledger_db import (
    STORE_KIND,
    STORE_VERSION,
    atomic_write_json,
    ledger_lock,
    read_json,
    write_text_secure,
)
from ledger_core.domain.model import (now_iso, default_currency, default_accounts, empty_ledger, ledger_preferences, ensure_accounts, normalize_ledger)
from ledger_core.domain.metrics import (account_balances, budgets_for_month, budget_progress, summarize, period_day_count, pct_change, tx_day, month_key, compact_candidate, category_entries, category_parent_map, DEFAULT_CATEGORY_PARENTS)
from ledger_core.domain.semantics import counts_as, nature_of, settlement_of, settled_amount, remaining_amount, settle
from ledger_core.domain.money import decimal_amount
from ledger_core.storage import repository
from ledger_core.services.profile import active_rule
from ledger_advice import build_advice
from ledger_brief import action_items, digest, render_brief
from ledger_insight import build_findings, find_negative_balances
from ledger_voice import compose_record


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_TEMPLATE = SKILL_DIR / "assets" / "ledger-viewer-template.html"
DEFAULT_BILL_TEMPLATE = SKILL_DIR / "assets" / "ledger-bill-canvas.html"
BILL_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
VALID_TYPES = {"expense", "income", "refund", "transfer"}
VALID_RELATIONS = {"refund", "reimbursement", "repayment", "split", "writeoff"}
VALID_SOURCES = {"text", "image", "voice", "manual", "import"}
VALID_METHODS = {"wechat", "alipay", "cash", "card", "bank", "other"}
VALID_ACCOUNT_TYPES = {"cash", "wechat", "alipay", "bank", "credit", "other"}
RANGE_PRESETS = (
    "this-month",
    "last-month",
    "last7",
    "last30",
    "this-quarter",
    "last-quarter",
    "this-year",
    "last-year",
)
REIMBURSE_TAGS = {"报销", "待报销", "对公"}
REIMBURSED_TAGS = {"已报销", "报销已回", "报销到账"}
WEEKDAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")
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
_ACCOUNT_TOKEN = (
    r"(微信零钱|微信支付|微信|支付宝余额|支付宝|余额宝|现金账户|现金|信用卡|银行卡|借记卡|储蓄卡)"
)
TRANSFER_ROUTE_RE = re.compile(
    r"(?:从)?"
    + _ACCOUNT_TOKEN
    # The verb may be followed directly by the connector ("转到"), or an amount
    # may sit between them ("从微信转100到支付宝").
    + r"\s*(?:转账|转到|转给|转入|转|->|→)"
    + r"\s*(?:\d+(?:\.\d{1,2})?\s*元?\s*)?"
    + r"(?:到|至|进|给)?\s*"
    + _ACCOUNT_TOKEN
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
    "咖啡茶饮": ["星巴克", "瑞幸", "咖啡", "拿铁", "美式", "奶茶", "喜茶", "茶饮"],
    "外食": ["饭", "午饭", "晚饭", "早餐", "外卖", "火锅", "餐厅", "美团", "饿了么", "烧腊", "烘焙"],
    "买菜": ["买菜", "蔬菜", "水果", "海鲜", "买虾", "菜市场"],
    "零食": ["零食", "赵一鸣"],
    "加油": ["加油", "中国石化", "中国石油"],
    "停车通行": ["停车", "停车费", "过路费", "高速费"],
    "车辆养护": ["养车", "洗车", "修车", "保养", "途虎"],
    "交通": ["地铁", "公交", "打车", "滴滴", "高铁", "火车", "机票"],
    "服饰": ["衣服", "服饰", "URBAN REVIVO"],
    "超市": ["超市", "百佳汇"],
    "日用百货": ["百货", "日用品", "便利店"],
    "其他购物": ["淘宝", "京东", "拼多多", "数码"],
    "物业": ["房租", "水电", "燃气", "物业", "宽带"],
    "娱乐": ["电影", "游戏", "演出", "KTV", "会员"],
    "医疗": ["医院", "药", "挂号", "体检"],
    "教育": ["课程", "书", "学费", "培训"],
    "红包": ["红包"],
    "人情往来": ["礼物", "请客"],
    "保险": ["保险", "保费"],
    "软件服务": ["软件", "订阅", "DeepSeek", "深度求索"],
    "收入": ["工资", "奖金", "报销", "利息"],
}
KNOWN_MERCHANTS = ["星巴克", "瑞幸", "麦当劳", "肯德基", "喜茶", "奈雪", "盒马"]
FALLBACK_MERCHANTS = ["美团", "饿了么", "京东", "淘宝", "拼多多", "滴滴"]
TYPE_KEYWORDS = {
    "income": ["工资", "奖金", "收入", "利息", "报销到账"],
    "refund": ["退款", "退回", "返现"],
    "transfer": ["还信用卡"],
}
# Reimbursement income phrases. The literal "报销到账" was the only recognised
# form, so the far more common "报销回来"/"报销了" fell through to expense and
# silently inflated spending.
REIMBURSE_INCOME_KEYWORDS = (
    "报销到账",
    "报销回来",
    "报销了",
    "报销打款",
    "报销入账",
    # "打过来/转过来了" is money arriving. The position carries the tense: "公司
    # 打过来" is an arrival, "公司报销" with no arrival word is an expectation.
    "打过来了",
    "打过来",
    "转过来了",
    "转过来",
    "转给我了",
    "打款了",
    "到账了",
)
# A bare "报销" almost never means "the money is here" -- it usually means "this
# will be reimbursed". Treating it as arrival was invisible but wrong: the taxi
# fare a user just paid got booked as income, so spending fell instead of rising.
# Only these explicit past-tense forms count as arrival.
REIMBURSE_EXPECTED_KEYWORDS = ("报销", "公司报", "要报销", "能报销", "可以报销", "待报销", "事后报销")
# Money coming *back* to the user, said in ordinary speech rather than accounting
# words. "垫的800还我了" is the same economic event as "报销回来800": the earlier
# advance is settled, not a new expense. Without these the row was booked as
# fresh spending and the period total grew by the amount instead of shrinking.
REPAYMENT_INCOME_KEYWORDS = (
    "还我了",
    "还给我了",
    "还给我",
    "退给我了",
    "退给我",
    "退回来了",
    "把钱还了",
    "已还清",
    "还清了",
    "打给我了",
    "打给我",
    "转给我",
    "还回来了",
    "还回来",
)
# Advance/loan language marks money expected back, so it must not be counted as
# ordinary spending even though cash leaves the account.
ADVANCE_KEYWORDS = ("垫付", "垫钱", "代付", "代垫", "借出", "借给", "应收")
# Categories whose meaning is inherently income. Used by the type/category
# consistency invariant in doctor_report().
INCOME_ONLY_CATEGORIES = {"收入", "红包收入"}
# Advance state: money paid out that is expected back (垫付/代付/借出).
VALID_ADVANCE_STATES = ("open", "settled", "written_off")
NO_ITEMIZED_PREFIXES = ("外卖", "打车", "超市", "买菜", "海淘", "网购", "订单")
# Receiving a red packet is income; sending one is 人情 spending. The verb
# decides, so both directions are not collapsed into one rule.
RED_PACKET_INCOME_KEYWORDS = ("收到红包", "收红包", "红包收到", "领到红包", "抢到红包")
RED_PACKET_EXPENSE_KEYWORDS = ("发红包", "红包给", "给红包", "发了红包", "包了红包")
# Own-account movement verbs that indicate a transfer rather than spending.
TRANSFER_VERBS = ("还信用卡", "提现", "转到", "转入", "内部划转", "还款", "还花呗", "还白条")
TYPE_LINE_MAP = {"支出": "expense", "收入": "income", "退款": "refund", "转账": "transfer"}
METHOD_KEYWORDS = {
    "wechat": ["微信支付", "微信"],
    "alipay": ["支付宝", "花呗", "余额宝"],
    "cash": ["现金"],
    "card": ["信用卡", "刷卡"],
    "bank": ["银行卡", "借记卡", "储蓄卡"],
}
ACCOUNT_KEYWORDS = {
    # "微信付的" names a wallet just as clearly as "微信零钱付的". Without the
    # short forms, the account falls back to the learned default and a stated
    # account is silently replaced by habit.
    "微信零钱": ["微信零钱", "微信里", "微信付", "微信买了"],
    "支付宝": ["支付宝余额", "余额宝", "支付宝付", "支付宝买"],
    "现金": ["现金账户", "现金付", "现金买", "现金"],
    "信用卡": ["信用卡"],
}
TAG_KEYWORDS = {
    "报销": ["报销", "对公", "公司垫付"],
    "出差": ["出差", "差旅"],
    "订阅": ["订阅"],
}
TOTAL_HINTS = ("一共", "共", "合计", "总计", "券后", "实付", "满减")
NOISY_MERCHANT_RE = re.compile(r"有限公司|股份有限|分公司|信息科技|集团|销售有限")
MAX_LEARNABLE_PHRASE = 8
MEMORY_TX_GAP = 10
MEMORY_MAX_AGE_DAYS = 7
HABIT_PHRASES = (
    "早饭",
    "早餐",
    "午饭",
    "午餐",
    "晚饭",
    "晚餐",
    "夜宵",
    "地铁",
    "公交",
    "打车",
    "外卖",
    "奶茶",
    "咖啡",
    "停车",
    "加油",
)
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


def safe_amount(value, default=0.0):
    """Convert a stored amount without letting one corrupt row crash a report."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return default
    if amount != amount or amount in (float("inf"), float("-inf")):
        return default
    return amount


def tx_sort_key(tx):
    return str(tx.get("occurred_at") or "") if isinstance(tx, dict) else ""


def parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是"}


def json_command(func):
    @functools.wraps(func)
    def cli(args):
        result = func(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    cli.result = func
    return cli


def mutating(func):
    """Hold the ledger lock across a command's read-modify-write cycle."""

    @functools.wraps(func)
    def wrapper(args):
        ledger_path = getattr(args, "ledger", None)
        if not ledger_path:
            return func(args)
        with ledger_lock(ledger_path):
            return func(args)

    return wrapper


def validate_month(value, *, required=False):
    if value in (None, ""):
        if required:
            raise SystemExit("--month must be YYYY-MM")
        return None
    value = str(value).strip()
    if not BILL_MONTH_RE.fullmatch(value):
        raise SystemExit(f"Unsupported month format: {value}. Use YYYY-MM.")
    year, month = (int(part) for part in value.split("-"))
    if year < 1900 or not 1 <= month <= 12:
        raise SystemExit(f"Unsupported month: {value}. Use a real YYYY-MM.")
    return value


def validate_day(value, *, label="date"):
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"Unsupported {label}: {value}. Use YYYY-MM-DD.") from exc
    return text


def known_category_names(ledger=None):
    names = set(CATEGORY_KEYWORDS)
    names.update(DEFAULT_CATEGORY_PARENTS)
    names.update(DEFAULT_CATEGORY_PARENTS.values())
    names.update({"其他", "转账", "收入", "坏账损失"})
    if ledger:
        for item in category_entries(ledger):
            names.add(item["name"])
            if item.get("parent"):
                names.add(item["parent"])
    return names


def validate_category(ledger, category):
    if not category:
        return category
    if category not in known_category_names(ledger):
        raise SystemExit(
            f"Unknown category: {category}. "
            "Run `ledger category list` or add it first with `ledger category set`."
        )
    return category


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


def visible_accounts(ledger):
    return [account for account in ensure_accounts(ledger) if isinstance(account, dict) and not account.get("archived")]


def default_account(ledger):
    prefs = ledger_preferences(ledger or {})
    wanted = prefs.get("default_account_id")
    accounts = visible_accounts(ledger or {})
    for account in accounts:
        if account.get("id") == wanted:
            return account
    return None


def find_account(ledger, value):
    if not value:
        return None
    needle = str(value).strip()
    accounts = ensure_accounts(ledger or {})
    for account in accounts:
        if isinstance(account, dict) and (account.get("id") == needle or account.get("name") == needle):
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
    if "提现" in text:
        # Withdrawing moves money to a bank account. Capture an explicit
        # destination ("提现到银行卡") and treat the rest as the source, so the
        # destination account is not mistaken for the source.
        destination = None
        destination_match = re.search(r"提现\s*(?:到|至|进)\s*" + _ACCOUNT_TOKEN, text)
        remainder = text
        if destination_match:
            destination = destination_match.group(1)
            remainder = text[: destination_match.start()] + " " + text[destination_match.end() :]
        else:
            remainder = text.replace("提现", " ")
        source = None
        for alias in sorted(ACCOUNT_ALIASES, key=len, reverse=True):
            if alias in remainder:
                source = alias
                break
        if destination is None and "银行卡" in remainder:
            destination = "银行卡"
        return source, destination or "银行卡"
    return None


def account_names(ledger):
    return ", ".join(account.get("name") or account.get("id") for account in visible_accounts(ledger)) or "(no accounts)"


def apply_account_fields(proposal, ledger):
    route = infer_transfer_route(proposal.get("note") or "")
    if route:
        source_name, dest_name = route
        proposal["type"] = "transfer"
        if proposal.get("category") in (None, "其他"):
            proposal["category"] = "转账"
        source = find_account(ledger, source_name) if source_name else default_account(ledger)
        if source_name and not source:
            raise SystemExit(f"Unknown transfer source account: {source_name}. Available: {account_names(ledger)}")
        dest = find_account(ledger, dest_name)
        if not dest:
            raise SystemExit(f"Unknown transfer destination account: {dest_name}. Available: {account_names(ledger)}")
        if source:
            proposal["account_id"] = source["id"]
            proposal["account"] = source["name"]
            proposal["method"] = proposal.get("method") or ACCOUNT_TYPE_TO_METHOD.get(source.get("type"))
        proposal["to_account_id"] = dest["id"]
        proposal["to_account"] = dest["name"]
        return proposal
    explicit = proposal.get("account_id") or proposal.get("account")
    account = find_account(ledger, explicit)
    if explicit and not account:
        raise SystemExit(f"Unknown account: {explicit}. Available: {account_names(ledger)}")
    if not account and proposal.get("method"):
        account = account_for_method(ledger, proposal.get("method"))
    if not account and not explicit:
        account = default_account(ledger) if proposal.get("type") != "transfer" else None
    explicit_dest = proposal.get("to_account_id") or proposal.get("to_account")
    dest = find_account(ledger, explicit_dest)
    if explicit_dest and not dest:
        raise SystemExit(f"Unknown destination account: {explicit_dest}. Available: {account_names(ledger)}")
    if account:
        proposal["account_id"] = account["id"]
        proposal["account"] = account["name"]
        if not proposal.get("method"):
            proposal["method"] = ACCOUNT_TYPE_TO_METHOD.get(account.get("type"))
    if dest:
        proposal["to_account_id"] = dest["id"]
        proposal["to_account"] = dest["name"]
    return proposal


def load_ledger(path, currency="CNY", create=True):
    try:
        return repository.load(path, currency, create)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Cannot load ledger: {exc}") from exc


def save_ledger(path, ledger):
    try:
        repository.save(path, ledger)
        status = getattr(ledger, "history_status", {})
        if status.get("state") == "pending":
            print("Ledger saved; Git history pending. Retry with history save.", file=sys.stderr)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Cannot save ledger: {exc}") from exc


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
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Confidence must be numeric: {value}") from exc
    if not math.isfinite(confidence) or confidence < 0 or confidence > 1:
        raise SystemExit("Confidence must be between 0 and 1")
    return confidence


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


def habit_id_for(phrase):
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "_", str(phrase or "").strip()).strip("_")
    if not slug:
        raise SystemExit("Habit phrase is required")
    return f"hab_{slug}"[:48]


def ensure_habits(ledger):
    rows = ledger.setdefault("habits", [])
    if not isinstance(rows, list):
        rows = []
        ledger["habits"] = rows
    return rows


def find_habit(ledger, value):
    if not value:
        return None
    needle = str(value).strip()
    for habit in ensure_habits(ledger):
        if not isinstance(habit, dict):
            continue
        if habit.get("id") == needle or habit.get("phrase") == needle:
            return habit
    return None


def typical_amount(recent, *, locked_amount=None, amount_locked=False):
    if amount_locked and locked_amount is not None:
        return round(float(locked_amount), 2)
    values = [round(float(value), 2) for value in recent if value is not None]
    if len(values) < 2:
        return None
    value, count = Counter(values).most_common(1)[0]
    if count >= 2 and count >= (len(values) + 1) // 2:
        return value
    return None


def is_learnable_phrase(phrase):
    text = str(phrase or "").strip()
    if not text:
        return False
    if text in HABIT_PHRASES:
        return True
    if NOISY_MERCHANT_RE.search(text):
        return False
    if len(text) > MAX_LEARNABLE_PHRASE:
        return False
    return True


def habit_phrases_from_tx(tx):
    phrases = []
    merchant = str(tx.get("merchant") or "").strip()
    if is_learnable_phrase(merchant):
        phrases.append(merchant)
    blob = " ".join(str(part) for part in (tx.get("note"), tx.get("merchant"), tx.get("category")) if part)
    for phrase in HABIT_PHRASES:
        if phrase in blob and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def habit_match_score(habit, text, merchant=None):
    phrase = str(habit.get("phrase") or "").strip()
    if not phrase:
        return None
    in_text = phrase in (text or "")
    merchant_hit = bool(merchant) and (
        habit.get("merchant") == merchant or phrase == merchant
    )
    if not in_text and not merchant_hit:
        return None
    return (
        1 if habit.get("pinned") else 0,
        len(phrase) if in_text else 0,
        habit.get("count") or 0,
    )


def match_habit(ledger, text, merchant=None):
    ranked = []
    for habit in ensure_habits(ledger or {}):
        if not isinstance(habit, dict) or not active_rule(habit):
            continue
        score = habit_match_score(habit, text, merchant)
        if score is not None:
            ranked.append((score, habit))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def apply_habit_to_proposal(proposal, habit, *, amount_from_habit=False):
    if not habit or habit.get("source") != "manual":
        return proposal
    phrase = habit.get("phrase")
    if amount_from_habit and not is_learnable_phrase(phrase) and habit.get("source") != "manual":
        amount_from_habit = False
    proposal["_habit"] = {
        "id": habit.get("id"),
        "phrase": phrase,
        "amount_from_habit": bool(amount_from_habit),
    }
    if amount_from_habit and habit.get("amount") is not None:
        proposal["amount"] = round(float(habit["amount"]), 2)
        confidence = proposal.get("confidence")
        if confidence is None or confidence > 0.9:
            proposal["confidence"] = 0.9
    if not proposal.get("merchant") and habit.get("merchant") and is_learnable_phrase(habit.get("merchant")):
        proposal["merchant"] = habit["merchant"]
    if (not proposal.get("category") or proposal.get("category") == "其他") and habit.get("category"):
        proposal["category"] = habit["category"]
    if not proposal.get("method") and habit.get("method"):
        proposal["method"] = habit["method"]
    if not proposal.get("account") and not proposal.get("account_id"):
        if habit.get("account") or habit.get("account_id"):
            proposal["account"] = habit.get("account")
            if habit.get("account_id"):
                proposal["account_id"] = habit["account_id"]
    return proposal


def affordable_account(ledger, amount):
    """The visible account with the most headroom, if any can cover ``amount``.

    Legacy query helper only. It is never used to infer a transaction account.
    Computed balances may be incomplete and do not establish purchasing power.
    """
    if not ledger:
        return None
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    candidates = [
        row
        for row in account_balances(ledger)
        if not row.get("archived")
        and row.get("balance", 0) >= amount
        # A credit card has no balance to overdraw; it is a valid payer.
        and row.get("type") in {"wechat", "alipay", "cash", "bank", "card", "other"}
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda row: row.get("balance", 0))


def apply_usage_defaults(proposal, ledger):
    # Only an explicitly configured default is eligible. Balances are not evidence
    # of which account actually paid, even for a large purchase.
    configured = default_account(ledger) if ledger is not None else None
    if configured and not proposal.get("account") and not proposal.get("account_id") and not proposal.get("method"):
        proposal["account"] = configured["name"]
        proposal["account_id"] = configured["id"]
        proposal["account_reason"] = "使用已确认的默认账户"
    return proposal


def is_shortcut_habit(habit):
    if not isinstance(habit, dict) or not habit.get("phrase"):
        return False
    if habit.get("pinned") or habit.get("source") == "manual":
        return True
    return (habit.get("count") or 0) >= 2


def sort_habits(habits):
    return sorted(
        [habit for habit in habits if isinstance(habit, dict)],
        key=lambda habit: (
            not habit.get("pinned"),
            -(habit.get("count") or 0),
            0 if habit.get("amount") is None else -1,
            habit.get("phrase") or "",
        ),
    )


def visible_habits(ledger, limit=8):
    return sort_habits([habit for habit in ensure_habits(ledger) if is_shortcut_habit(habit)])[:limit]


def _mode(values):
    counted = Counter(value for value in values if value)
    if not counted:
        return None, 0
    value, count = counted.most_common(1)[0]
    return value, count


def build_usage_profile(ledger, summary=None):
    txs = [tx for tx in ledger.get("transactions", []) if isinstance(tx, dict)]
    expenses = [tx for tx in txs if counts_as(tx, "expense")]
    account, _ = _mode([tx.get("account") for tx in expenses])
    account_id, _ = _mode([tx.get("account_id") for tx in expenses if tx.get("account") == account])
    method, method_n = _mode([tx.get("method") for tx in expenses])
    configured_default = default_account(ledger)
    default_name = configured_default.get("name") if configured_default else None
    default_id = configured_default.get("id") if configured_default else None
    default_method = (
        ACCOUNT_TYPE_TO_METHOD.get(configured_default.get("type"))
        if configured_default
        else None
    )
    top_categories = [name for name, _count in Counter((tx.get("category") or "其他") for tx in expenses).most_common(4)]
    stable = []
    variable = []
    for habit in sort_habits(ensure_habits(ledger)):
        phrase = habit.get("phrase")
        if not is_learnable_phrase(phrase) and habit.get("source") != "manual":
            continue
        if (habit.get("count") or 0) < 2 and habit.get("source") != "manual":
            continue
        row = {
            "phrase": phrase,
            "category": habit.get("category"),
            "merchant": habit.get("merchant"),
            "amount": habit.get("amount"),
            "count": habit.get("count") or 0,
        }
        if habit.get("amount") is not None and habit.get("source") == "manual" and habit.get("amount_locked"):
            stable.append(row)
        else:
            variable.append(row)
    lunch_hours = 0
    for tx in expenses:
        try:
            hour = datetime.fromisoformat(tx.get("occurred_at") or "").hour
        except ValueError:
            continue
        if 11 <= hour <= 14 and (tx.get("category") in {"餐饮", "外食", "咖啡茶饮", "买菜", "零食"} or "饭" in str(tx.get("note") or "")):
            lunch_hours += 1
    parts = []
    if default_name:
        method_label = METHOD_LABELS.get(default_method) if default_method else None
        extra = f"（{method_label}）" if method_label else ""
        parts.append(f"记账默认走{default_name}{extra}，缺账户时不必再问。")
    if top_categories:
        parts.append("支出主要记在" + "、".join(top_categories[:3]) + "。")
    if lunch_hours >= 3:
        parts.append("已记录的午间交易中餐饮较多；这是观察，不作为自动分类规则。")
    for item in stable[:3]:
        category = item.get("category") or "支出"
        parts.append(f"{item['phrase']}常记{category}，金额大约 ¥{item['amount']:.0f}。")
    if variable:
        names = "、".join(item["phrase"] for item in variable[:3] if item.get("phrase"))
        if names:
            parts.append(f"{names}去得勤但金额不固定，缺金额时要问，不要猜。")
    if not parts:
        parts.append("按原始信息记账；没有确认的默认账户时保留未分配，缺金额时询问。")
    portrait = summary if summary else "".join(parts)
    return {
        "updated_at": now_iso(),
        "tx_count": len(txs),
        "summary": portrait,
        "defaults": {
            "account": default_name,
            "account_id": default_id,
            "method": default_method,
        },
        "top_categories": top_categories,
        "stable_amounts": stable[:8],
        "variable_merchants": variable[:8],
        "do_not": [
            "不要把长商户名、支付公司名做成快捷按钮",
            "金额不稳定时不要自动填金额",
            "不要向用户展示常用按钮或要求存为常用",
        ],
    }


def refresh_usage_profile(ledger, *, force=False, summary=None):
    prefs = ledger_preferences(ledger or {})
    current = prefs.get("usage_profile")
    tx_count = len(ledger.get("transactions") or [])
    token = repository.fingerprint({"transactions": ledger.get("transactions"), "habits": ledger.get("habits"), "default": prefs.get("default_account_id")})
    if (
        not force
        and not summary
        and isinstance(current, dict)
        and current.get("input_token") == token
        and current.get("summary")
    ):
        return False
    kept_summary = summary if summary else (current.get("summary") if isinstance(current, dict) and current.get("tx_count") == tx_count else None)
    prefs["usage_profile"] = build_usage_profile(ledger, summary=kept_summary if summary else None)
    prefs["usage_profile"]["input_token"] = token
    if summary:
        prefs["usage_profile"]["summary"] = summary
    return True


def ledger_stem(ledger_path):
    """Stable document stem: ledger.json -> ledger, lazy-ledger.json -> lazy-ledger."""
    return Path(ledger_path).stem or "ledger"


def habit_memory_path(ledger_path):
    path = Path(ledger_path)
    target = path.parent / f"{ledger_stem(path)}-memory.md"
    return target


def parse_habit_memory_meta(text):
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text or "", re.DOTALL)
    if not match:
        return {}
    meta = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def habit_memory_is_stale(memory_path, ledger):
    path = Path(memory_path)
    if not path.exists():
        return True
    try:
        meta = parse_habit_memory_meta(path.read_text(encoding="utf-8"))
    except OSError:
        return True
    tx_count = len(ledger.get("transactions") or [])
    remembered = int(float(meta["tx_count"])) if meta.get("tx_count") not in (None, "") else -1
    if remembered < 0 or tx_count - remembered >= MEMORY_TX_GAP:
        return True
    updated = meta.get("updated_at") or ""
    try:
        written = datetime.fromisoformat(updated)
        if written.tzinfo is None:
            written = written.astimezone()
        if datetime.now().astimezone() - written >= timedelta(days=MEMORY_MAX_AGE_DAYS):
            return True
    except ValueError:
        return True
    return False


def render_habit_memory(ledger, profile=None):
    prefs = ledger_preferences(ledger or {})
    profile = profile or prefs.get("usage_profile") or build_usage_profile(ledger)
    defaults = profile.get("defaults") or {}
    method = defaults.get("method")
    method_label = METHOD_LABELS.get(method, method) if method else ""
    lines = [
        "---",
        f"updated_at: {profile.get('updated_at') or now_iso()}",
        f"tx_count: {profile.get('tx_count') if profile.get('tx_count') is not None else len(ledger.get('transactions') or [])}",
        "kind: lazy-ledger-memory",
        "---",
        "",
        "# 记账习惯",
        "",
        "给记账助手读的定期总结。流水仍以账本 JSON 为准；这里只写默认怎么记。",
        "",
        "## 画像",
        "",
        (profile.get("summary") or "按原始信息记账；没有确认的默认账户时保留未分配，缺金额时询问。").strip(),
        "",
        "## 默认",
        "",
        f"- 账户：{defaults.get('account') or '微信零钱'}",
    ]
    if method_label:
        lines.append(f"- 支付：{method_label}")
    categories = profile.get("top_categories") or []
    if categories:
        lines.append(f"- 常见分类：{'、'.join(categories[:4])}")
    lines.extend(["", "## 稳定金额", "", "仅下列短说法在用户没写金额时可以默认。不要猜这里没有的金额。", ""])
    stable = profile.get("stable_amounts") or []
    if stable:
        for item in stable:
            amount = item.get("amount")
            price = f"¥{amount:.0f}" if isinstance(amount, (int, float)) else ""
            bits = [item.get("phrase"), item.get("category"), price]
            lines.append("- " + " · ".join(str(bit) for bit in bits if bit))
    else:
        lines.append("- （还没有稳定金额）")
    lines.extend(["", "## 不要猜金额", ""])
    variable = profile.get("variable_merchants") or []
    if variable:
        for item in variable:
            lines.append(f"- {item.get('phrase')}：常去但金额不固定")
    else:
        lines.append("- （暂无）")
    mapping = prefs.get("merchant_categories") or {}
    lines.extend(["", "## 商户分类", ""])
    if mapping:
        for merchant, category in list(mapping.items())[:20]:
            lines.append(f"- {merchant} → {category}")
    else:
        lines.append("- （还没有显式规则）")
    lines.extend(
        [
            "",
            "## 助手怎么用",
            "",
            "- 缺账户、缺支付方式时用「默认」",
            "- 只有「稳定金额」里的短说法可以补金额",
            "- 「不要猜金额」和支付公司全称一律问金额",
            "- 不要向用户展示常用按钮，也不要要求存为常用",
            "",
        ]
    )
    return "\n".join(lines)


def write_habit_memory(ledger_path, ledger, *, force=False, summary=None):
    path = habit_memory_path(ledger_path)
    refresh_usage_profile(ledger, force=force or bool(summary), summary=summary)
    if not force and not habit_memory_is_stale(path, ledger):
        return path, False
    profile = ledger_preferences(ledger).get("usage_profile") or build_usage_profile(ledger)
    write_text_secure(path, render_habit_memory(ledger, profile))
    return path, True


def habits_payload(ledger):
    refresh_usage_profile(ledger)
    rows = sort_habits(ensure_habits(ledger))
    profile = ledger_preferences(ledger).get("usage_profile") or {}
    return {"profile": profile, "habits": rows}


def prune_noisy_learned_habits(ledger):
    kept = []
    changed = False
    for habit in ensure_habits(ledger):
        if not isinstance(habit, dict):
            changed = True
            continue
        if habit.get("source") == "manual" or is_learnable_phrase(habit.get("phrase")):
            kept.append(habit)
        else:
            changed = True
    ledger["habits"] = kept
    return changed


def rebuild_learned_habits(ledger):
    kept = [
        habit
        for habit in ensure_habits(ledger)
        if isinstance(habit, dict) and habit.get("source") == "manual"
    ]
    ledger["habits"] = kept
    rows = [tx for tx in ledger.get("transactions", []) if isinstance(tx, dict)]
    rows.sort(key=lambda tx: tx.get("occurred_at") or "")
    for tx in rows:
        if tx.get("source") == "import" or tx.get("type") == "transfer":
            continue
        learn_habits_from_transaction(ledger, tx)
    refresh_usage_profile(ledger, force=True)
    return ensure_habits(ledger)


def seed_habits_if_needed(ledger):
    prefs = ledger_preferences(ledger or {})
    pruned = prune_noisy_learned_habits(ledger)
    if prefs.get("habits_seeded") and not pruned:
        return refresh_usage_profile(ledger)
    if ledger.get("transactions"):
        rebuild_learned_habits(ledger)
    prefs["habits_seeded"] = True
    refresh_usage_profile(ledger, force=True)
    return True


def upsert_habit(ledger, phrase, tx=None, **fields):
    phrase = str(phrase or "").strip()
    if not phrase:
        return None
    habit = find_habit(ledger, phrase)
    if habit and habit.get("source") == "manual" and fields.get("source") == "learned":
        return habit
    ts = now_iso()
    created = False
    if habit is None:
        habit = {
            "id": fields.pop("id", None) or habit_id_for(phrase),
            "phrase": phrase,
            "count": 0,
            "recent_amounts": [],
            "pinned": False,
            "amount_locked": False,
            "source": fields.pop("source", "learned"),
            "created_at": ts,
        }
        if any(existing.get("id") == habit["id"] for existing in ensure_habits(ledger) if isinstance(existing, dict)):
            habit["id"] = f"hab_{uuid.uuid4().hex[:8]}"
        ensure_habits(ledger).append(habit)
        created = True
    overwrite = created or fields.get("source") == "manual"
    for key in ("merchant", "category", "type", "method", "account", "account_id"):
        value = fields.get(key)
        if value in (None, ""):
            continue
        if overwrite or not habit.get(key):
            habit[key] = value
    if fields.get("pinned") is True:
        habit["pinned"] = True
    if fields.get("pinned") is False:
        habit["pinned"] = False
    if fields.get("source") == "manual":
        habit["source"] = "manual"
    if tx:
        amount = tx.get("amount")
        habit["count"] = int(habit.get("count") or 0) + 1
        habit["last_used_at"] = tx.get("occurred_at") or ts
        if amount is not None:
            recent = [round(float(value), 2) for value in (habit.get("recent_amounts") or []) if value is not None]
            recent.append(round(float(amount), 2))
            habit["recent_amounts"] = recent[-8:]
        if not habit.get("merchant") and tx.get("merchant"):
            habit["merchant"] = tx.get("merchant")
        if (not habit.get("category") or habit.get("category") == "其他") and tx.get("category") and tx.get("category") != "其他":
            habit["category"] = tx.get("category")
        if not habit.get("method") and tx.get("method"):
            habit["method"] = tx.get("method")
        if not habit.get("account") and tx.get("account"):
            habit["account"] = tx.get("account")
            if tx.get("account_id"):
                habit["account_id"] = tx.get("account_id")
        if not habit.get("type") and tx.get("type") and tx.get("type") != "transfer":
            habit["type"] = tx.get("type")
    if "amount" in fields:
        if fields["amount"] is None:
            habit["amount"] = None
            habit["amount_locked"] = False
        else:
            habit["amount"] = round(float(fields["amount"]), 2)
            if fields.get("amount_locked") or habit.get("source") == "manual":
                habit["amount_locked"] = True
    elif not habit.get("amount_locked"):
        habit["amount"] = typical_amount(
            habit.get("recent_amounts") or [],
            locked_amount=habit.get("amount"),
            amount_locked=False,
        )
    habit["updated_at"] = ts
    return habit


def learn_habits_from_transaction(ledger, tx):
    if not isinstance(tx, dict):
        return []
    if tx.get("source") == "import" or tx.get("type") == "transfer":
        return []
    updated = []
    for phrase in habit_phrases_from_tx(tx):
        updated.append(upsert_habit(ledger, phrase, tx=tx, source="learned"))
    return updated


def classify_event_detailed(text):
    """Decide the economic event once and say *why*.

    Returns a dict with ``event``, ``type``, ``category``, ``flags``,
    ``confidence`` and ``reasons``. Type and category are derived together so
    they cannot contradict each other, and every branch records the evidence
    that fired, which lets the assistant explain a decision instead of
    asserting it.

    ``reasons`` is a list of ``{"rule": ..., "match": ...}`` entries in firing
    order; the last one is the decisive rule.
    """
    reasons = []

    def decide(event, tx_type, category, flags=None, confidence=1.0, rule="", match=""):
        if rule:
            reasons.append({"rule": rule, "match": match})
        return {
            "event": event,
            "type": tx_type,
            "category": category,
            "flags": flags or {},
            "confidence": confidence,
            "reasons": reasons,
        }

    for keyword in REPAYMENT_INCOME_KEYWORDS:
        if keyword in text:
            return decide("repayment_arrival", "income", "收入", {"reimburse": True}, 0.9,
                          "REPAYMENT_INCOME_KEYWORDS", keyword)
    for keyword in REIMBURSE_INCOME_KEYWORDS:
        if keyword in text:
            # Reimbursement arriving is income, and it settles an earlier advance.
            return decide("reimbursement_arrival", "income", "收入", {"reimburse": True}, 0.95,
                          "REIMBURSE_INCOME_KEYWORDS", keyword)
    for keyword in REIMBURSE_EXPECTED_KEYWORDS:
        if keyword in text:
            # "打车26，公司报销" is money going out that will come back: an
            # advance, not income. Getting this backwards moves the period total
            # in the wrong direction, which is worse than any mis-labelled row.
            return decide("advance_expected", "expense", "其他", {"advance": True}, 0.75,
                          "REIMBURSE_EXPECTED_KEYWORDS", keyword)
    for keyword in RED_PACKET_INCOME_KEYWORDS:
        if keyword in text:
            return decide("red_packet_received", "income", "红包", {}, 0.9,
                          "RED_PACKET_INCOME_KEYWORDS", keyword)
    for keyword in RED_PACKET_EXPENSE_KEYWORDS:
        if keyword in text:
            return decide("red_packet_sent", "expense", "红包", {}, 0.9,
                          "RED_PACKET_EXPENSE_KEYWORDS", keyword)
    for keyword in ADVANCE_KEYWORDS:
        if keyword in text:
            # Advance/loan: cash leaves now but is owed back, so it must not count
            # as ordinary spending.
            return decide("advance_out", "expense", "其他", {"advance": True}, 0.7,
                          "ADVANCE_KEYWORDS", keyword)
    # An explicit route between two of the user's own accounts is a transfer,
    # even when the wording is "转100到" rather than the literal "还信用卡".
    route = infer_transfer_route(text)
    if route:
        return decide("own_account_transfer", "transfer", "转账", {}, 0.9,
                      "TRANSFER_ROUTE_RE", "->".join(str(part) for part in route if part))
    for tx_type, keywords in TYPE_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                if tx_type == "income":
                    return decide("income", tx_type, ("红包" if "红包" in text else "收入"), {}, 1.0,
                                  "TYPE_KEYWORDS[income]", keyword)
                if tx_type == "transfer":
                    return decide("own_account_transfer", tx_type, "转账", {}, 1.0,
                                  "TYPE_KEYWORDS[transfer]", keyword)
                return decide(tx_type, tx_type, None, {}, 1.0, f"TYPE_KEYWORDS[{tx_type}]", keyword)
    for verb in TRANSFER_VERBS:
        if verb in text:
            return decide("own_account_transfer", "transfer", "转账", {}, 0.8, "TRANSFER_VERBS", verb)
    for category in CATEGORY_KEYWORDS:
        if category in text:
            return decide("spending", "expense", category, {}, 1.0, "CATEGORY_KEYWORDS[exact]", category)
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                # A category-only cue such as "报销" without arrival language is an
                # advance awaiting settlement, not a finished expense.
                if category in INCOME_ONLY_CATEGORIES:
                    return decide("advance_expected", "expense", "其他", {"advance": True}, 0.7,
                                  "CATEGORY_KEYWORDS[income-only]", keyword)
                return decide("spending", "expense", category, {}, 1.0, f"CATEGORY_KEYWORDS[{category}]", keyword)
    # Nothing matched: the row is still spending, but we say so honestly.
    return decide("unknown_spending", "expense", "其他", {}, 0.6, "fallback", "no keyword matched")


def classify_event(text):
    """Backwards-compatible tuple form of :func:`classify_event_detailed`."""
    decision = classify_event_detailed(text)
    return decision["type"], decision["category"], decision["flags"], decision["confidence"]


def infer_type(text):
    tx_type, _category, _flags, _confidence = classify_event(text)
    return tx_type


def infer_category(text, tx_type="expense"):
    _event_type, category, _flags, _confidence = classify_event(text)
    if category is None:
        category = "其他"
    if tx_type == "income":
        return "红包" if "红包" in text else "收入"
    # Never let a type/category mismatch leak out through this legacy helper.
    if category in INCOME_ONLY_CATEGORIES and tx_type != "income":
        return "其他"
    return category


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


CHINESE_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def chinese_amount(value):
    """Convert the common short Chinese money forms used in chat."""
    if value.isdigit():
        return float(value)
    total = 0
    current = 0
    units = {"十": 10, "百": 100, "千": 1000}
    for char in value:
        if char in CHINESE_DIGITS:
            current = CHINESE_DIGITS[char]
        elif char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            return None
    return float(total + current) if total or current else None


def extract_amount(text):
    chinese = re.search(r"(?:实付|券后|合计|一共|总计|总共|共计|花了|用了)\s*(?:[为是:：]?\s*)?([零〇一二两三四五六七八九十百千]+)\s*(?:元|块|块钱)", text)
    if chinese:
        value = chinese_amount(chinese.group(1))
        if value is not None:
            cleaned = f"{text[:chinese.start()]} {text[chinese.end():]}"
            return round(value, 2), cleaned
    # A labeled total is semantic evidence, so prefer its number over a
    # nearby quantity (for example, “合计38元，买了2杯咖啡”).
    labeled_total = re.search(
        r"(?:实付|券后|合计|一共|总计|总共|共计)(?:花了|用了)?\s*(?:[为是:：]?\s*)?(\d+(?:\.\d{1,2})?)\s*(?:元|块|块钱)?",
        text,
    )
    if labeled_total and not re.search(r"(?:实付|券后)", text[: labeled_total.start()]):
        value = round(float(labeled_total.group(1)), 2)
        cleaned = f"{text[:labeled_total.start()]} {text[labeled_total.end():]}"
        # Remove other numeric tokens only after retaining the labeled total.
        cleaned = AMOUNT_RE.sub(" ", cleaned)
        return value, cleaned
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
    # A single leftover character is a fragment of the original phrase (给, 的,
    # 买), not a merchant name; prefer the fallback list over it.
    parts = [part for part in parts if len(part) > 1]
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


def parse_text_transaction(text, ledger=None, currency="CNY", source="text", confidence=None, require_amount=True):
    """Build a proposal from casual text.

    `require_amount=False` is for lookup paths such as `find`, where the caller
    only needs the merchant/category/date signals and has no amount to give.
    """
    raw_text = text.strip()
    if not raw_text:
        raise SystemExit("Text is required")
    raw_text = apply_aliases_to_text(ledger, raw_text)
    if ledger is not None:
        seed_habits_if_needed(ledger)
    date_token = extract_date_token(raw_text)
    date_text_removed = raw_text.replace(date_token, " ", 1) if date_token else raw_text
    amount, amount_removed = extract_amount(date_text_removed)
    decision = classify_event_detailed(raw_text)
    tx_type = decision["type"]
    event_category = decision["category"]
    event_flags = decision["flags"]
    merchant = infer_merchant(amount_removed, infer_category(raw_text, tx_type), raw_text)
    category = preferred_category(ledger, merchant, event_category or infer_category(raw_text, tx_type))
    if tx_type == "expense" and re.search(r"(?:转账-?转给|转账给|转给)", raw_text):
        category = "人情往来"
    method = infer_method(raw_text)
    account = infer_account(raw_text, method)
    tags = infer_tags(raw_text)
    habit = match_habit(ledger, raw_text, merchant)
    amount_from_habit = False
    if amount is None:
        can_fill = (
            habit
            and habit.get("amount") is not None
            and habit.get("source") == "manual"
            and habit.get("amount_locked", False)
        )
        if can_fill:
            amount = round(float(habit["amount"]), 2)
            amount_from_habit = True
        elif require_amount:
            raise SystemExit("Amount is required")
    if confidence is None:
        confidence = 1.0
        if category == "其他":
            confidence = 0.75
        if not merchant and category == "其他":
            confidence = 0.6
        if amount_from_habit:
            confidence = min(confidence, 0.9)
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
    # Say why this event was read the way it was. Without this a user who meant
    # "a taxi fare my company will reimburse" gets silently booked as income.
    proposal["event"] = {
        "name": decision["event"],
        "confidence": decision["confidence"],
        "reasons": decision["reasons"],
    }
    if decision["reasons"]:
        last = decision["reasons"][-1]
        proposal["explained_by"] = f"{last['rule']}: {last['match']}"
    if merchant:
        proposal["merchant"] = merchant
    if method:
        proposal["method"] = method
    if account:
        proposal["account"] = account
        # Remember that the account came from the sentence, not from habit: a
        # stated account is a fact and must not be second-guessed later.
        proposal["account_from_text"] = True
    if event_flags.get("advance"):
        proposal["advance"] = "open"
    apply_habit_to_proposal(proposal, habit, amount_from_habit=amount_from_habit)
    apply_usage_defaults(proposal, ledger)
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


def normalized_text(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def likely_duplicate_candidates(transactions, tx):
    candidates = []
    if not isinstance(tx, dict):
        return candidates
    tx_external_id = normalized_text(tx.get("wechat_transaction_id"))
    tx_amount = round(safe_amount(tx.get("amount")), 2)
    tx_merchant = normalized_text(tx.get("merchant"))
    tx_note = normalized_text(tx.get("note"))
    for existing in transactions:
        if not isinstance(existing, dict):
            continue
        existing_external_id = normalized_text(existing.get("wechat_transaction_id"))
        # Distinct authoritative statement references are separate source rows,
        # even when date, amount, and merchant happen to match.
        tx_statement_ref = normalized_text(tx.get("credit_statement_ref") or tx.get("bank_statement_ref"))
        existing_statement_ref = normalized_text(existing.get("credit_statement_ref") or existing.get("bank_statement_ref"))
        if tx_statement_ref and existing_statement_ref and tx_statement_ref != existing_statement_ref:
            continue
        if tx_external_id and existing_external_id:
            if tx_external_id == existing_external_id:
                candidates.append(compact_candidate(existing))
            continue
        if tx.get("type") and existing.get("type") and tx.get("type") != existing.get("type"):
            continue
        existing_amount = round(safe_amount(existing.get("amount")), 2)
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
    if getattr(args, "related_transaction_id", None) is not None:
        merged["related_transaction_id"] = args.related_transaction_id
    if getattr(args, "relation", None) is not None:
        merged["relation"] = args.relation
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
    if getattr(args, "exclude_from_totals", False) or getattr(args, "not_mine", False):
        merged["excluded_from_totals"] = True
    if getattr(args, "include_in_totals", False):
        merged.pop("excluded_from_totals", None)
    if getattr(args, "advance", None):
        merged["advance"] = args.advance
    elif getattr(args, "not_mine", False):
        # Fronting money for someone else is not a receivable in the ledger's
        # sense: it is simply not the user's spending, so it must not also be
        # listed as an open advance.
        merged.pop("advance", None)
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
    related_transaction_id=None,
    relation=None,
    excluded_from_totals=False,
    advance=None,
    account_reason=None,
):
    if advance and advance != "open":
        raise SystemExit("Create an open advance, then settle or write off using the linked operation")
    if advance and tx_type != "expense":
        raise SystemExit("An advance must be an expense cash movement")
    if amount_value is None:
        raise SystemExit("Amount is required unless --text includes one")
    try:
        amount = float(decimal_amount(amount_value))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Amount must be numeric: {amount_value}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise SystemExit("Amount must be a finite positive number")
    if tx_type not in VALID_TYPES:
        raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if source not in VALID_SOURCES:
        raise SystemExit(f"Source must be one of: {', '.join(sorted(VALID_SOURCES))}")
    if method and method not in VALID_METHODS:
        raise SystemExit(f"Method must be one of: {', '.join(sorted(VALID_METHODS))}")
    if tx_type == "transfer" and not (account or account_id):
        raise SystemExit("A transfer needs both source and destination accounts (--account)")
    if tx_type == "transfer" and not (to_account or to_account_id):
        raise SystemExit("A transfer needs both source and destination accounts (--to-account)")
    tx_currency = default_currency(currency or ledger.get("currency"))
    ledger_currency = default_currency(ledger.get("currency"))
    if tx_currency != ledger_currency:
        raise SystemExit(
            f"Ledger currency is {ledger_currency}; per-transaction currency {tx_currency} is not supported. "
            "Convert the amount or create a ledger in that currency."
        )
    confidence = validate_confidence(confidence)
    category = category or ("收入" if tx_type == "income" else "转账" if tx_type == "transfer" else "其他")
    validate_category(ledger, category)
    ts = now_iso()
    tx = {
        "id": tx_id or make_id(occurred_at),
        "type": tx_type,
        "amount": round(amount, 2),
        "currency": tx_currency,
        "category": category,
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
    if account_reason:
        # Where the account came from, when it was not simply the default. Kept
        # on the transaction row so the reply can explain the choice.
        tx["account_reason"] = account_reason
    if account_id:
        tx["account_id"] = account_id
    if to_account:
        tx["to_account"] = to_account
    if to_account_id:
        tx["to_account_id"] = to_account_id
    if related_transaction_id:
        tx["related_transaction_id"] = related_transaction_id
    if relation:
        if relation not in VALID_RELATIONS:
            raise SystemExit(f"Relation must be one of: {', '.join(sorted(VALID_RELATIONS))}")
        if not related_transaction_id:
            raise SystemExit("--relation requires --related-id")
        tx["relation"] = relation
    if excluded_from_totals:
        tx["excluded_from_totals"] = True
    # Advance state: money paid out that is expected back. Stored as a first
    # class field so it can be queried and settled, rather than living only in
    # the parser's transient flags.
    advance_state = normalize_advance(advance)
    if advance_state:
        tx["advance"] = advance_state
    return tx


def normalize_advance(value):
    """Coerce an advance state value; return None when there is no state."""
    if value is None or value is False or value == "":
        return None
    if value is True:
        return "open"
    state = str(value).strip().lower()
    if state not in VALID_ADVANCE_STATES:
        raise SystemExit(f"Advance state must be one of: {', '.join(VALID_ADVANCE_STATES)}")
    return state


def advance_settled_amount(tx):
    return float(settled_amount(tx))


def advance_remaining(tx):
    return float(remaining_amount(tx))


def apply_advance_settlement(advance_tx, income_tx):
    try:
        return settle(advance_tx, income_tx)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


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
        related_transaction_id=filled.get("related_transaction_id"),
        relation=filled.get("relation"),
        excluded_from_totals=bool(filled.get("excluded_from_totals")),
        advance=filled.get("advance"),
        account_reason=filled.get("account_reason"),
        tx_id=tx_id,
    )


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


@mutating
def add_transaction_result(args):
    ledger = load_ledger(args.ledger, args.currency)
    operation_id = getattr(args, "operation_id", None)
    request = {k: v for k, v in vars(args).items() if k not in {"func", "ledger", "json", "command", "expected_revision", "operation_id"}}
    request_hash = repository.fingerprint(request)
    if operation_id and operation_id in ledger.get("operations", {}):
        receipt = ledger["operations"][operation_id]
        if receipt["request_hash"] != request_hash:
            raise SystemExit("Operation ID already used with different input")
        return receipt["result"]
    expected = getattr(args, "expected_revision", None)
    if expected is not None and ledger["revision"] != expected:
        raise SystemExit("Stale proposal revision; reload context")
    seed_habits_if_needed(ledger)
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
                getattr(args, "exclude_from_totals", False),
                getattr(args, "include_in_totals", False),
                getattr(args, "not_mine", False),
                getattr(args, "advance", None),
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
                "related_transaction_id": getattr(args, "related_transaction_id", None),
                "relation": getattr(args, "relation", None),
                "excluded_from_totals": getattr(args, "exclude_from_totals", False) or getattr(args, "not_mine", False),
                "advance": getattr(args, "advance", None),
            }
        ]
    added = []
    known = list(ledger["transactions"])
    for index, proposal in enumerate(proposals):
        tx = payload_from_proposal(ledger, proposal, args, tx_id=args.id if index == 0 else None)
        if any(isinstance(row, dict) and row.get("id") == tx["id"] for row in ledger["transactions"]):
            raise SystemExit(f"Transaction id already exists: {tx['id']}")
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
        if getattr(args, "remember", False):
            if not tx.get("merchant"):
                raise SystemExit("--remember needs a transaction with a merchant")
            learn_merchant_category(ledger, tx.get("merchant"), tx.get("category"))
        learn_habits_from_transaction(ledger, tx)
        added.append(tx)
    ledger["transactions"].sort(key=tx_sort_key, reverse=True)
    refresh_usage_profile(ledger, force=True)
    # Link refunds that now have a confident counterpart, so the relation is
    # persisted rather than merely suppressing a warning.
    refund_links = link_unmatched_refunds(ledger, ledger["transactions"], added)
    # Settle an open advance when the reimbursement that pays it back arrives.
    advance_settlements = settle_advances(ledger, ledger["transactions"], added)
    month = month_key(added[0].get("occurred_at")) if added else month_key(now_iso())
    payload = {"added": added, "count": len(added), "month": month_snapshot(ledger, month)}
    if refund_links:
        payload["refund_links"] = refund_links
    if advance_settlements:
        payload["advance_settlements"] = advance_settlements
    used = [item.get("_habit") for item in proposals if item.get("_habit")]
    if used:
        payload["used_habits"] = used
    if operation_id:
        ledger.setdefault("operations", {})[operation_id] = {"request_hash": request_hash, "result": payload}
    save_ledger(args.ledger, ledger)
    payload["history"] = getattr(ledger, "history_status", {"state": "disabled"})
    return payload


@json_command
def add_transaction(args):
    return add_transaction_result(args)


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
        prev_end = prev_start + timedelta(days=(end_d - start_d).days)
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
                f"按现在日均，月底大约 {format_money(pace.get('projected_expense'), currency)}，可能超预算。"
            )
        elif pace.get("projected_remaining", 0) > 0:
            lines.append(
                f"按现在日均，月底大约还能剩 {format_money(pace.get('projected_remaining'), currency)}。"
            )
    elif pace and pace.get("scope") == "year" and not pace.get("complete") and expense:
        lines.append(f"按现在日均，今年大约会花 {format_money(pace.get('projected_expense'), currency)}。")
    missing = [item for item in data.get("recurring") or [] if item.get("missing_this_month")]
    if missing:
        names = "、".join(item["label"] for item in missing[:3])
        lines.append(f"周期账这月还没记：{names}。")
    pending = data.get("pending_reimbursement") or []
    if pending:
        total = sum(float(item.get("amount") or 0) for item in pending)
        lines.append(f"待报销 {len(pending)} 笔，共 {format_money(total, currency)}。")
    unmatched = data.get("unmatched_refunds") or []
    if unmatched:
        lines.append(f"有 {len(unmatched)} 笔退款对不上近期支出。")
    weekday = data.get("weekday") or {}
    weekend = float(weekday.get("weekend") or 0)
    if expense and weekend / expense >= 0.4:
        lines.append(f"周末支出占 {weekend / expense * 100:.0f}%。")
    return lines[:4]


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
    pace = data.get("pace") or {}
    if pace.get("projected_expense") is not None and not pace.get("complete"):
        if pace.get("budget_limit") is not None:
            status = "预计不超" if pace.get("on_track") else "预计超支"
            lines.append(
                f"按日均推算月底 {format_money(pace['projected_expense'], currency)} · {status}"
            )
        elif pace.get("scope") == "year":
            lines.append(f"按日均推算全年 {format_money(pace['projected_expense'], currency)}")
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
            lines.append(f"- {item['month']} {format_money(item['expense'], currency)}")
    if data.get("recurring"):
        lines.extend(["", "### 周期账"])
        cadence_label = {"monthly": "每月", "weekly": "每周", "biweekly": "每两周"}
        for item in data["recurring"][:6]:
            status = "这月还没记" if item.get("missing_this_month") else ("这月已记" if item.get("recorded_this_month") else "")
            extra = f" · {status}" if status else ""
            lines.append(
                f"- {item['label']} {format_money(item['typical_amount'], currency)} · {cadence_label.get(item['cadence'], item['cadence'])}{extra}"
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
    if data.get("pending_reimbursement"):
        lines.extend(["", "### 待报销"])
        for item in data["pending_reimbursement"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} {format_money(item.get('amount'), currency)}"
            )
    if data.get("unmatched_refunds"):
        lines.extend(["", "### 未对上的退款"])
        for item in data["unmatched_refunds"][:5]:
            date = (item.get("occurred_at") or "")[:10]
            lines.append(
                f"- {date} {item.get('merchant') or item.get('note') or item.get('id')} {format_money(item.get('amount'), currency)}"
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

    for advance in open_advances(transactions):
        remaining = advance_remaining(advance)
        days = days_outstanding(advance)
        label = advance.get("merchant") or advance.get("note") or advance.get("id")
        age = f"，已经 {days} 天了" if days is not None and days >= 30 else ""
        items.append(
            {
                "priority": 1,
                "kind": "advance_open",
                "transaction_id": advance.get("id"),
                "say": f"{label} 的 {format_money(remaining, currency)} 还没收回{age}。",
                "do": "收到就用 advance settle 销掉；要不回来就用 advance writeoff，别一直挂着。",
                "command": f"ledger advance settle --id {advance.get('id')}",
            }
        )

    for refund in unmatched_refunds(transactions, transactions):
        items.append(
            {
                "priority": 1,
                "kind": "unmatched_refund",
                "say": (
                    f"{refund.get('occurred_at', '')[:10]} 的退款 "
                    f"{format_money(refund.get('amount'), currency)} 还没对上原始支出。"
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
    findings = find_negative_balances(balances, currency)
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
    template = Path(template_path) if template_path else DEFAULT_BILL_TEMPLATE
    html = template.read_text(encoding="utf-8")
    payload = json.dumps(bill_canvas_payload(pack), ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("__BILL_DATA__", payload)
    output = Path(output_path)
    write_text_secure(output, html)
    return output.resolve()


def render_bill_html(ledger, ledger_path, month=None, output_path=None, template_path=None):
    pack = build_bill_pack(ledger, month)
    dest = Path(output_path) if output_path else default_bill_html_path(ledger_path, pack["month"])
    path = write_bill_html(pack, dest, template_path=template_path)
    return pack, path


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


@json_command
@mutating
def bill_save_command(args):
    ledger = load_ledger(args.ledger, create=True)
    month = default_bill_month(ledger, args.month)
    body = (args.body or "").strip()
    if not body:
        raise SystemExit("Bill body is required")
    if len(body) > 20000:
        raise SystemExit("Bill body is too long (maximum 20000 characters)")
    title = (args.title or "").strip()
    if len(title) > 200:
        raise SystemExit("Bill title is too long (maximum 200 characters)")
    existing = find_bill(ledger, month)
    ts = now_iso()
    if existing:
        existing["title"] = title or existing.get("title") or f"{month} 月度账单"
        existing["body"] = body
        existing["updated_at"] = ts
        bill = existing
    else:
        bill = {
            "id": f"bill_{month.replace('-', '')}",
            "month": month,
            "title": title or f"{month} 月度账单",
            "body": body,
            "created_at": ts,
            "updated_at": ts,
        }
        ledger.setdefault("bills", []).append(bill)
    save_ledger(args.ledger, ledger)
    payload = dict(bill)
    return payload


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


def find_transaction(ledger, tx_id):
    for index, tx in enumerate(ledger["transactions"]):
        if isinstance(tx, dict) and tx.get("id") == tx_id:
            return index, tx
    raise SystemExit(f"Transaction not found: {tx_id}")


@json_command
@mutating
def update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    _, tx = find_transaction(ledger, args.id)
    linked = tx.get("advance_settlements") or tx.get("writeoff_transaction_id") or any(
        r.get("related_transaction_id") == tx["id"] for r in ledger["transactions"] if isinstance(r, dict))
    linked = linked or tx.get("related_transaction_id")
    if linked and any(getattr(args, name, None) is not None for name in ("amount", "type", "date", "occurred_at", "currency", "related_transaction_id", "relation")):
        raise SystemExit("Linked transaction needs a coordinated correction; cannot change its financial fields alone")
    if args.amount is not None:
        try:
            amount = float(decimal_amount(args.amount))
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Amount must be numeric: {args.amount}") from exc
        if not math.isfinite(amount) or amount <= 0:
            raise SystemExit("Amount must be a finite positive number")
        tx["amount"] = round(amount, 2)
    if args.type is not None:
        if args.type not in VALID_TYPES:
            raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_TYPES))}")
        tx["type"] = args.type
    if args.currency is not None:
        ledger_currency = default_currency(ledger.get("currency"))
        if default_currency(args.currency) != ledger_currency:
            raise SystemExit(
                f"Ledger currency is {ledger_currency}; per-transaction currency {args.currency} is not supported."
            )
        tx["currency"] = ledger_currency
    if args.category is not None:
        if args.category == "":
            tx["category"] = "其他"
        else:
            validate_category(ledger, args.category)
            tx["category"] = args.category
    if getattr(args, "remember", False):
        if not tx.get("merchant"):
            raise SystemExit("--remember needs a transaction with a merchant")
        learn_merchant_category(ledger, tx.get("merchant"), tx.get("category"))
    if getattr(args, "related_transaction_id", None) is not None:
        tx["related_transaction_id"] = args.related_transaction_id
    if getattr(args, "relation", None) is not None:
        tx["relation"] = args.relation
    excluded = (
        True
        if getattr(args, "exclude_from_totals", False) or getattr(args, "not_mine", False)
        else False
        if getattr(args, "include_in_totals", False)
        else None
    )
    if excluded is not None:
        if excluded:
            tx["excluded_from_totals"] = True
        else:
            tx.pop("excluded_from_totals", None)
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
        tx["confidence"] = validate_confidence(args.confidence)
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
        elif args.method not in VALID_METHODS:
            raise SystemExit(f"Method must be one of: {', '.join(sorted(VALID_METHODS))}")
        else:
            tx["method"] = args.method
    if args.account is not None:
        if args.account == "":
            tx.pop("account", None)
            tx.pop("account_id", None)
        else:
            resolved = find_account(ledger, args.account)
            if not resolved:
                raise SystemExit(f"Unknown account: {args.account}. Available: {account_names(ledger)}")
            tx["account"] = resolved["name"]
            tx["account_id"] = resolved["id"]
    if getattr(args, "to_account", None) is not None:
        if args.to_account == "":
            tx.pop("to_account", None)
            tx.pop("to_account_id", None)
        else:
            resolved = find_account(ledger, args.to_account)
            if not resolved:
                raise SystemExit(
                    f"Unknown destination account: {args.to_account}. Available: {account_names(ledger)}"
                )
            tx["to_account"] = resolved["name"]
            tx["to_account_id"] = resolved["id"]
    if tx.get("type") == "transfer" and not (tx.get("account") or tx.get("account_id")):
        raise SystemExit("A transfer needs a source account (--account)")
    if tx.get("type") == "transfer" and not (tx.get("to_account") or tx.get("to_account_id")):
        raise SystemExit("A transfer needs a destination account (--to-account)")
    tx["updated_at"] = now_iso()
    ledger["transactions"].sort(key=tx_sort_key, reverse=True)
    learn_habits_from_transaction(ledger, tx)
    refresh_usage_profile(ledger, force=True)
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=False)
    return tx


@json_command
@mutating
def delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    index, tx = find_transaction(ledger, args.id)
    if not args.yes:
        date = (tx.get("occurred_at") or "")[:10]
        print(f"Refusing to delete without --yes: {tx.get('id')} {date} {tx.get('category')} {tx.get('amount')}", file=sys.stderr)
        raise SystemExit(2)
    if tx.get("advance_settlements") or tx.get("writeoff_transaction_id") or tx.get("related_transaction_id") or any(r.get("related_transaction_id") == tx["id"] for r in ledger["transactions"] if isinstance(r, dict)):
        raise SystemExit("Linked transaction needs a coordinated correction before deletion")
    removed = ledger["transactions"].pop(index)
    save_ledger(args.ledger, ledger)
    return removed


VIEWER_PRIVATE_FIELDS = (
    "attachment",
    "wechat_transaction_id",
    "credit_statement_ref",
    "bank_statement_ref",
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


def render_command(args):
    ledger = load_ledger(args.ledger, create=False)
    template_path = Path(args.template) if args.template else DEFAULT_TEMPLATE
    html = template_path.read_text(encoding="utf-8")
    payload = json.dumps(viewer_ledger_data(ledger), ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    html = html.replace("__LEDGER_DATA__", payload)
    output = Path(args.output)
    write_text_secure(output, html)
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
    seen_ids = {}
    for index, tx in enumerate(transactions):
        if not isinstance(tx, dict):
            add_issue(issues, "invalid_transaction", "Transaction must be an object", index=index)
            continue
        tx_id = tx.get("id")
        if not isinstance(tx_id, str) or not tx_id:
            add_issue(issues, "invalid_id", "Transaction id must be a non-empty string", tx, value=tx_id)
        elif tx_id in seen_ids:
            add_issue(issues, "duplicate_id", "Duplicate transaction id", tx, value=tx_id, first_index=seen_ids[tx_id])
        else:
            seen_ids[tx_id] = index
        for field in sorted(REQUIRED_TX_FIELDS):
            if field not in tx:
                add_issue(issues, "missing_field", f"Missing required field: {field}", tx, field=field)
        tx_type = tx.get("type")
        if not isinstance(tx_type, str) or tx_type not in VALID_TYPES:
            add_issue(issues, "invalid_type", "Invalid transaction type", tx, value=tx_type)
        tx_category = tx.get("category")
        if tx_category and (not isinstance(tx_category, str) or tx_category not in known_category_names(ledger)):
            add_issue(issues, "unknown_category", "Unknown category", tx, value=tx_category)
        # Accounting invariant: an income-only category on a non-income row means
        # the event was classified inconsistently and would distort totals
        # silently. Flag it instead of reporting a clean ledger.
        if (
            isinstance(tx_category, str)
            and tx_category in INCOME_ONLY_CATEGORIES
            and tx_type in VALID_TYPES
            and tx_type != "income"
        ):
            add_issue(
                issues,
                "category_type_mismatch",
                f"Category {tx_category} requires type=income but the row is {tx_type}",
                tx,
                category=tx_category,
                type=tx_type,
            )
        tx_method = tx.get("method")
        if tx_method not in (None, "") and (not isinstance(tx_method, str) or tx_method not in VALID_METHODS):
            add_issue(issues, "invalid_method", "Invalid payment method", tx, value=tx_method)
        if tx.get("account_id") and not find_account(ledger, tx.get("account_id")):
            add_issue(issues, "unknown_account", "Unknown account_id", tx, value=tx.get("account_id"))
        if tx.get("to_account_id") and not find_account(ledger, tx.get("to_account_id")):
            add_issue(issues, "unknown_account", "Unknown to_account_id", tx, value=tx.get("to_account_id"))
        if tx_type == "transfer" and not (tx.get("account_id") or tx.get("account")):
            add_issue(issues, "missing_transfer_source", "Transfer is missing source account", tx)
        if tx_type == "transfer" and not (tx.get("to_account_id") or tx.get("to_account")):
            add_issue(issues, "missing_transfer_dest", "Transfer is missing destination account", tx)
        if "excluded_from_totals" in tx and not isinstance(tx.get("excluded_from_totals"), bool):
            add_issue(issues, "invalid_exclusion", "excluded_from_totals must be true or false", tx, value=tx.get("excluded_from_totals"))
        if tx.get("relation"):
            if tx.get("relation") not in VALID_RELATIONS:
                add_issue(issues, "invalid_relation", "Invalid transaction relation", tx, value=tx.get("relation"))
            related_id = tx.get("related_transaction_id")
            if not related_id:
                add_issue(issues, "missing_related_transaction", "Relation is missing related transaction id", tx)
            elif not any(row.get("id") == related_id for row in transactions if isinstance(row, dict)):
                add_issue(issues, "unknown_related_transaction", "Related transaction was not found", tx, value=related_id)
        try:
            amount = float(tx.get("amount", 0))
            if not math.isfinite(amount):
                add_issue(issues, "invalid_amount", "Amount must be finite", tx, value=tx.get("amount"))
            elif amount <= 0:
                add_issue(issues, "invalid_amount", "Amount must be positive", tx, value=tx.get("amount"))
        except (TypeError, ValueError):
            add_issue(issues, "invalid_amount", "Amount must be numeric", tx, value=tx.get("amount"))
        confidence = tx.get("confidence")
        if confidence is not None:
            try:
                confidence_value = float(confidence)
                if not math.isfinite(confidence_value) or confidence_value < 0 or confidence_value > 1:
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
    account_ids = set()
    account_names = set()
    for index, account in enumerate(ledger.get("accounts", []) or []):
        if not isinstance(account, dict):
            add_issue(issues, "invalid_account", "Account must be an object", index=index)
            continue
        account_id = account.get("id")
        if not isinstance(account_id, str) or not account_id:
            add_issue(issues, "invalid_account", "Account id must be a non-empty string", index=index, value=account_id)
        elif account_id in account_ids:
            add_issue(issues, "duplicate_account_id", "Duplicate account id", value=account_id)
        else:
            account_ids.add(account_id)
        account_name = account.get("name")
        if not isinstance(account_name, str) or not account_name:
            add_issue(issues, "invalid_account", "Account name must be a non-empty string", index=index, value=account_name)
        elif account_name in account_names:
            add_issue(issues, "duplicate_account_name", "Duplicate account name", value=account_name)
        else:
            account_names.add(account_name)
        if account.get("type") not in VALID_ACCOUNT_TYPES:
            add_issue(issues, "invalid_account", "Invalid account type", value=account.get("type"))
    budget_ids = set()
    budget_keys = set()
    for index, budget in enumerate(ledger.get("budgets", []) or []):
        if not isinstance(budget, dict):
            add_issue(issues, "invalid_budget", "Budget must be an object", index=index)
            continue
        budget_id = budget.get("id")
        if not isinstance(budget_id, str) or not budget_id:
            add_issue(issues, "invalid_budget", "Budget id must be a non-empty string", index=index, value=budget_id)
        elif budget_id in budget_ids:
            add_issue(issues, "duplicate_budget_id", "Duplicate budget id", value=budget_id)
        else:
            budget_ids.add(budget_id)
        budget_category = budget.get("category")
        if budget_category and not isinstance(budget_category, str):
            add_issue(issues, "invalid_budget", "Budget category must be a string", index=index, value=budget_category)
        elif budget_category and (not isinstance(budget_category, str) or budget_category not in known_category_names(ledger)):
            add_issue(issues, "unknown_category", "Unknown budget category", value=budget_category)
        budget_month = budget.get("month")
        if budget_month and not isinstance(budget_month, str):
            add_issue(issues, "invalid_budget_month", "Budget month must be YYYY-MM", value=budget_month)
        key = (budget_month if isinstance(budget_month, str) else None, budget_category if isinstance(budget_category, str) else None)
        if key in budget_keys:
            add_issue(issues, "duplicate_budget", "Duplicate budget month/category", value=f"{key[0] or 'any'}/{key[1] or 'all'}")
        else:
            budget_keys.add(key)
        try:
            limit = float(budget.get("amount") or 0)
            if not math.isfinite(limit) or limit <= 0:
                add_issue(issues, "invalid_budget_amount", "Budget amount must be finite and positive", value=budget.get("amount"))
        except (TypeError, ValueError):
            add_issue(issues, "invalid_budget_amount", "Budget amount must be numeric", value=budget.get("amount"))
    bill_months = set()
    for index, bill in enumerate(ledger.get("bills", []) or []):
        if not isinstance(bill, dict):
            add_issue(issues, "invalid_bill", "Bill must be an object", index=index)
            continue
        month = bill.get("month")
        if not isinstance(month, str) or not month:
            add_issue(issues, "invalid_bill_month", "Bill month must be YYYY-MM", value=month)
        else:
            try:
                validate_month(month)
            except SystemExit:
                add_issue(issues, "invalid_bill_month", "Bill month must be YYYY-MM", value=month)
            if month in bill_months:
                add_issue(issues, "duplicate_bill", "Duplicate bill month", value=month)
            else:
                bill_months.add(month)
    return {"ok": not issues, "issue_count": len(issues), "issues": issues}

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


@mutating
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
            try:
                confidence = float(confidence_value) if confidence_value not in (None, "") else 1.0
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"Invalid confidence on line {line_number}: {confidence_value}") from exc
            proposal = {
                "amount": amount_value,
                "type": row.get("type") or "expense",
                "currency": row.get("currency") or args.currency,
                "category": row.get("category"),
                "occurred_at": parse_when(occurred_at),
                "source": row.get("source") or "import",
                "confidence": confidence,
                "tags": row.get("tags"),
                "merchant": row.get("merchant"),
                "note": row.get("note"),
                "method": row.get("method") or None,
                "account": row.get("account") or None,
                "to_account": row.get("to_account") or None,
                "related_transaction_id": row.get("related_transaction_id") or None,
                "relation": row.get("relation") or None,
            }
            apply_account_fields(proposal, ledger)
            tx = build_transaction_payload(
                ledger,
                amount_value=proposal["amount"],
                tx_type=proposal["type"],
                currency=proposal.get("currency"),
                category=proposal.get("category"),
                occurred_at=proposal["occurred_at"],
                source=proposal["source"],
                confidence=proposal.get("confidence"),
                tags=proposal.get("tags"),
                merchant=proposal.get("merchant"),
                note=proposal.get("note"),
                attachment=row.get("attachment"),
                method=proposal.get("method"),
                account=proposal.get("account"),
                account_id=proposal.get("account_id"),
                to_account=proposal.get("to_account"),
                to_account_id=proposal.get("to_account_id"),
                related_transaction_id=proposal.get("related_transaction_id"),
                relation=proposal.get("relation"),
                excluded_from_totals=parse_bool(row.get("excluded_from_totals")),
                tx_id=row.get("id"),
            )
            if any(isinstance(existing, dict) and existing.get("id") == tx["id"] for existing in ledger["transactions"]):
                raise SystemExit(f"Duplicate transaction id on line {line_number}: {tx['id']}")
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
    ledger["transactions"].sort(key=tx_sort_key, reverse=True)
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
        write_text_secure(output, payload)
        print(str(output.resolve()))
        return
    sys.stdout.write(payload if payload.endswith("\n") else payload + "\n")


@mutating
def prefer_command(args):
    ledger = load_ledger(args.ledger, create=True)
    prefs = ledger_preferences(ledger)
    if args.merchant and args.category:
        prefs["merchant_categories"][args.merchant] = args.category
        upsert_habit(
            ledger,
            args.merchant,
            source="manual",
            merchant=args.merchant,
            category=args.category,
        )
    if args.alias and args.merchant:
        prefs["merchant_aliases"][args.alias] = args.merchant
    if not ((args.merchant and args.category) or (args.alias and args.merchant)):
        raise SystemExit("Provide --merchant with --category, and/or --alias with --merchant")
    save_ledger(args.ledger, ledger)
    print(json.dumps(prefs, ensure_ascii=False, indent=2))


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


@json_command
@mutating
def category_set_command(args):
    ledger = load_ledger(args.ledger, create=True)
    old_name = str(getattr(args, "old_name", "") or "").strip()
    name = str(getattr(args, "name", "") or "").strip()
    icon = str(getattr(args, "icon", "") or "").strip() or None
    parent = str(getattr(args, "parent", "") or "").strip() or None
    if not name:
        raise SystemExit("Category name is required")
    if parent == name:
        raise SystemExit("Category cannot be its own parent")
    entries = category_entries(ledger)
    names = {item["name"] for item in entries}
    if old_name and old_name not in names:
        referenced = any(
            item.get("category") == old_name
            for collection in (ledger.get("transactions") or [], ledger.get("budgets") or [], ledger.get("habits") or [])
            for item in collection
            if isinstance(item, dict)
        ) or old_name in (ledger_preferences(ledger).get("merchant_categories") or {}).values()
        if not referenced:
            raise SystemExit("Category not found")
        entries.append({"name": old_name, "icon": None, "parent": None})
        names.add(old_name)
    if parent and parent not in names:
        referenced = any(
            item.get("category") == parent
            for collection in (ledger.get("transactions") or [], ledger.get("budgets") or [], ledger.get("habits") or [])
            for item in collection
            if isinstance(item, dict)
        )
        if not referenced:
            raise SystemExit("Parent category not found")
        entries.append({"name": parent, "icon": None, "parent": None})
        names.add(parent)
    if parent and next(item for item in entries if item["name"] == parent)["parent"]:
        raise SystemExit("A secondary category cannot contain another category")
    if name != old_name and name in names:
        raise SystemExit("Category name already exists")
    if old_name:
        for item in entries:
            if item["name"] == old_name:
                item["name"], item["icon"], item["parent"] = name, icon, parent
            elif item["parent"] == old_name:
                item["parent"] = name
        for tx in ledger.get("transactions") or []:
            if isinstance(tx, dict) and tx.get("category") == old_name:
                tx["category"] = name
        for budget in ledger.get("budgets") or []:
            if isinstance(budget, dict) and budget.get("category") == old_name:
                budget["category"] = name
        for habit in ledger.get("habits") or []:
            if isinstance(habit, dict) and habit.get("category") == old_name:
                habit["category"] = name
        prefs = ledger_preferences(ledger)
        prefs["merchant_categories"] = {
            merchant: name if category == old_name else category
            for merchant, category in prefs["merchant_categories"].items()
        }
    else:
        entries.append({"name": name, "icon": icon, "parent": parent})
    ledger["categories"] = entries
    save_ledger(args.ledger, ledger)
    category = next(item for item in entries if item["name"] == name)
    payload = {"name": category["name"], "icon": category["icon"]}
    if category["parent"]:
        payload["parent"] = category["parent"]
    return {"category": payload}


def build_review_queue(ledger, *, include_handled=False):
    """Derive the review queue from current ledger state.

    Items are keyed by finding type + transaction id so repeated runs are
    idempotent, and a resolved item is not recreated by a later scan.
    """
    transactions = [tx for tx in ledger.get("transactions") or [] if isinstance(tx, dict)]
    items = []

    def add(code, tx, detail, action):
        key = f"{code}:{tx.get('id')}"
        if any(item["key"] == key for item in items):
            return
        items.append(
            {
                "key": key,
                "code": code,
                "transaction_id": tx.get("id"),
                "occurred_at": tx.get("occurred_at"),
                "amount": tx.get("amount"),
                "merchant": tx.get("merchant"),
                "note": tx.get("note"),
                "detail": detail,
                "action": action,
                "status": "open",
            }
        )

    for tx in transactions:
        category = tx.get("category")
        tx_type = tx.get("type")
        if isinstance(category, str) and category in INCOME_ONLY_CATEGORIES and tx_type != "income":
            add("category_type_mismatch", tx, f"分类 {category} 需要收入类型，当前是 {tx_type}", "修正类型或分类")
        if tx_type == "expense" and tx.get("advance") == "open" and not tx.get("tags"):
            continue
        confidence = tx.get("confidence")
        try:
            low = confidence is not None and float(confidence) < 0.85
        except (TypeError, ValueError):
            low = True
        if low and tx_type == "expense":
            add("low_confidence", tx, f"置信度 {confidence}，分类 {category}", "确认分类")
    for refund in unmatched_refunds(transactions, transactions):
        source = next((tx for tx in transactions if tx.get("id") == refund.get("id")), None)
        if source:
            add("unmatched_refund", source, "没有找到可对应的原单", "指定原单或确认是独立退款")
    from ledger_core.services.findings import reconcile
    queue = reconcile(ledger, items)
    return queue if include_handled else [item for item in queue if item.get("status") == "open"]


@mutating
def review_refresh_command(args):
    ledger = load_ledger(args.ledger, create=True)
    queue = build_review_queue(ledger, include_handled=True)
    known = {item.get("key"): item.get("evidence") for item in ledger.get("review_queue") or [] if isinstance(item, dict)}
    items = [item for item in queue if known.get(item["key"]) != item.get("evidence")]
    ledger["review_queue"] = queue
    save_ledger(args.ledger, ledger)
    open_items = [item for item in queue if item.get("status") == "open"]
    if args.json:
        print(json.dumps({"added": len(items), "open": open_items, "open_count": len(open_items)}, ensure_ascii=False, indent=2))
        return
    print(f"新增 {len(items)} 项待复核，当前共 {len(open_items)} 项。")
    for item in open_items[:10]:
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


@mutating
def review_resolve_command(args):
    ledger = load_ledger(args.ledger, create=True)
    _index, tx = find_transaction(ledger, args.id)
    resolved_key = f"{args.code}:{tx.get('id')}"
    queue = build_review_queue(ledger, include_handled=True)
    if not any(item.get("key") == resolved_key for item in queue):
        queue.append(
            {
                "key": resolved_key,
                "code": args.code,
                "transaction_id": tx.get("id"),
                "status": "open",
            }
        )
    for item in queue:
        if item.get("key") == resolved_key:
            from ledger_core.services.findings import transition
            transition(item, getattr(args, "status", "resolved"), args.note or "", getattr(args, "until", None))
    ledger["review_queue"] = queue
    save_ledger(args.ledger, ledger)
    print(json.dumps({"resolved": resolved_key, "note": args.note or ""}, ensure_ascii=False, indent=2))


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
        f"待收垫付 {len(open_items)} 笔，合计 {format_money(sum(item['remaining'] for item in open_items), ledger.get('currency'))}"
    ]
    states = {"open": "待收", "settled": "已收回", "written_off": "已核销"}
    for item in items:
        date = (item.get("occurred_at") or "")[:10]
        label = item.get("merchant") or item.get("note") or item.get("id")
        state = states.get(item["advance"], item["advance"])
        if item["advance"] == "open":
            detail = f"待收 {format_money(item['remaining'], ledger.get('currency'))}"
        else:
            detail = f"{state} {format_money(item['settled'], ledger.get('currency'))}"
        lines.append(f"- {date} {label} {detail} · {item.get('id')}")
    print("\n".join(lines))


@mutating
def advance_settle_command(args):
    ledger = load_ledger(args.ledger, create=True)
    _index, advance = find_transaction(ledger, args.id)
    if advance.get("advance") not in VALID_ADVANCE_STATES:
        raise SystemExit(f"Transaction {args.id} is not an advance")
    by_id = args.by
    if by_id:
        _income_index, income = find_transaction(ledger, by_id)
        if income.get("type") != "income":
            raise SystemExit(f"Transaction {by_id} is not income")
        if apply_advance_settlement(advance, income) is None:
            raise SystemExit(f"Transaction {by_id} is already recorded as settling this advance")
    else:
        raise SystemExit("Settlement needs --by with the actual repayment transaction")
    advance["updated_at"] = now_iso()
    save_ledger(args.ledger, ledger)
    payload = compact_candidate(advance)
    payload["advance"] = advance.get("advance")
    payload["remaining"] = advance_remaining(advance)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


@mutating
def advance_writeoff_command(args):
    ledger = load_ledger(args.ledger, create=True)
    _index, advance = find_transaction(ledger, args.id)
    if advance.get("advance") not in VALID_ADVANCE_STATES:
        raise SystemExit(f"Transaction {args.id} is not an advance")
    if advance.get("advance") != "open":
        raise SystemExit("Only an open advance can be written off")
    loss = float(remaining_amount(advance))
    if loss <= 0:
        raise SystemExit("No outstanding amount to write off")
    occurred_at = parse_when(args.date) if getattr(args, "date", None) else now_iso()
    if occurred_at[:10] < advance.get("occurred_at", "")[:10]:
        raise SystemExit("Writeoff cannot precede the advance")
    row = {"id": "tx_" + uuid.uuid4().hex, "type": "expense", "amount": loss,
           "currency": advance.get("currency"), "category": "坏账损失", "occurred_at": occurred_at,
           "created_at": now_iso(), "updated_at": now_iso(), "balance_effect": False,
           "relation": "writeoff", "related_transaction_id": advance["id"],
           "source": "manual", "note": "垫付核销", "confidence": 1.0, "tags": []}
    advance.update(advance="written_off", written_off_amount=loss,
                   writeoff_transaction_id=row["id"], updated_at=now_iso())
    ledger["transactions"].append(row)
    save_ledger(args.ledger, ledger)
    print(json.dumps({"advance": compact_candidate(advance), "loss": row}, ensure_ascii=False, indent=2))


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


@mutating
def habit_profile_command(args):
    ledger = load_ledger(args.ledger, create=True)
    seed_habits_if_needed(ledger)
    summary = (args.summary or "").strip() or None
    refresh_usage_profile(ledger, force=True, summary=summary)
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=True, summary=summary)
    payload = habits_payload(ledger)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print((payload.get("profile") or {}).get("summary") or "")


@mutating
def habit_memory_command(args):
    ledger = load_ledger(args.ledger, create=True)
    seed_habits_if_needed(ledger)
    summary = (getattr(args, "summary", None) or "").strip() or None
    refresh_usage_profile(ledger, force=True, summary=summary)
    save_ledger(args.ledger, ledger)
    path, written = write_habit_memory(args.ledger, ledger, force=True, summary=summary)
    print(
        json.dumps(
            {
                "path": str(path.resolve()),
                "written": written,
                "tx_count": len(ledger.get("transactions") or []),
                "summary": (ledger_preferences(ledger).get("usage_profile") or {}).get("summary"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


@json_command
@mutating
def habit_set_command(args):
    ledger = load_ledger(args.ledger, create=True)
    seed_habits_if_needed(ledger)
    phrase = (args.phrase or "").strip() or None
    habit = find_habit(ledger, args.id) if args.id else find_habit(ledger, phrase)
    if not phrase:
        phrase = (habit or {}).get("phrase")
    if not phrase:
        raise SystemExit("Provide --phrase, or --id of an existing habit")
    account = find_account(ledger, args.account) if args.account else None
    fields = {
        "source": "manual",
        "merchant": args.merchant,
        "category": args.category,
        "type": args.type,
        "method": args.method,
        "account": account["name"] if account else args.account,
        "account_id": account["id"] if account else None,
    }
    if args.pin:
        fields["pinned"] = True
    if args.unpin:
        fields["pinned"] = False
    if args.clear_amount:
        fields["amount"] = None
        fields["amount_locked"] = False
    elif args.amount is not None:
        fields["amount"] = args.amount
        fields["amount_locked"] = True
    if args.id and habit:
        fields["id"] = habit["id"]
    habit = upsert_habit(ledger, phrase, **{key: value for key, value in fields.items() if value is not None or key == "amount" and args.clear_amount})
    if args.merchant and args.category:
        learn_merchant_category(ledger, args.merchant, args.category)
    refresh_usage_profile(ledger, force=True)
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=True)
    return habit


@json_command
@mutating
def habit_delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    remaining = []
    removed = None
    needle = args.id or args.phrase
    if not needle:
        raise SystemExit("Provide --id or --phrase")
    for habit in ensure_habits(ledger):
        if habit.get("id") == needle or habit.get("phrase") == needle:
            removed = habit
        else:
            remaining.append(habit)
    if not removed:
        raise SystemExit(f"Habit not found: {needle}")
    ledger["habits"] = remaining
    save_ledger(args.ledger, ledger)
    return removed


@mutating
def habit_rebuild_command(args):
    ledger = load_ledger(args.ledger, create=True)
    rebuild_learned_habits(ledger)
    ledger_preferences(ledger)["habits_seeded"] = True
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=True)
    print(json.dumps(habits_payload(ledger), ensure_ascii=False, indent=2))


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


@json_command
@mutating
def account_add_command(args):
    ledger = load_ledger(args.ledger, create=True)
    if args.type not in VALID_ACCOUNT_TYPES:
        raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}")
    name = (args.name or "").strip()
    if not name:
        raise SystemExit("Account name is required")
    try:
        opening = float(args.opening or 0)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Opening balance must be numeric: {args.opening}") from exc
    if not math.isfinite(opening):
        raise SystemExit("Opening balance must be a finite number")
    account = {
        "id": args.id or f"acc_{uuid.uuid4().hex[:8]}",
        "name": name,
        "type": args.type,
        "currency": ledger.get("currency") or "CNY",
        "opening_balance": float(decimal_amount(opening)),
        "opening_confirmed": args.opening is not None,
        "archived": False,
        "created_at": now_iso(),
    }
    if any(isinstance(existing, dict) and existing.get("id") == account["id"] for existing in ensure_accounts(ledger)):
        raise SystemExit(f"Account already exists: {account['id']}")
    if any(existing.get("name") == name for existing in ensure_accounts(ledger) if isinstance(existing, dict)):
        raise SystemExit(f"Account name already exists: {name}")
    ledger["accounts"].append(account)
    if args.default:
        ledger_preferences(ledger)["default_account_id"] = account["id"]
    save_ledger(args.ledger, ledger)
    return account


@json_command
@mutating
def account_update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    account = find_account(ledger, args.id)
    if not account:
        raise SystemExit(f"Account not found: {args.id}")
    if args.name:
        name = args.name.strip()
        if not name:
            raise SystemExit("Account name cannot be empty")
        if any(
            isinstance(existing, dict) and existing.get("name") == name and existing.get("id") != account["id"]
            for existing in ensure_accounts(ledger)
        ):
            raise SystemExit(f"Account name already exists: {name}")
        account["name"] = name
    if args.type:
        if args.type not in VALID_ACCOUNT_TYPES:
            raise SystemExit(f"Type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}")
        account["type"] = args.type
    if args.opening is not None:
        try:
            opening = float(args.opening)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"Opening balance must be numeric: {args.opening}") from exc
        if not math.isfinite(opening):
            raise SystemExit("Opening balance must be a finite number")
        account["opening_balance"] = float(decimal_amount(opening))
        account["opening_confirmed"] = True
    if args.archive:
        account["archived"] = True
    if args.unarchive:
        account["archived"] = False
    if args.default:
        ledger_preferences(ledger)["default_account_id"] = account["id"]
    account["updated_at"] = now_iso()
    save_ledger(args.ledger, ledger)
    return account


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


@json_command
@mutating
def budget_set_command(args):
    ledger = load_ledger(args.ledger, create=True)
    try:
        amount = round(float(args.amount), 2)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Budget amount must be numeric: {args.amount}") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise SystemExit("Budget amount must be a finite positive number")
    month = validate_month(args.month)
    category = validate_category(ledger, args.category or None) or None
    found = None
    for budget in ledger.setdefault("budgets", []):
        if not isinstance(budget, dict):
            continue
        if budget.get("month") == month and (budget.get("category") or None) == category:
            found = budget
            break
    ts = now_iso()
    if found:
        found["amount"] = amount
        found["updated_at"] = ts
        budget = found
    else:
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff-]+", "_", f"{month or 'any'}_{category or 'all'}").strip("_")
        base_id = f"bud_{slug or 'budget'}"
        used_ids = {item.get("id") for item in ledger.get("budgets", []) if isinstance(item, dict)}
        budget_id = base_id
        if budget_id in used_ids:
            budget_id = f"{base_id}_{hashlib.sha1(base_id.encode('utf-8')).hexdigest()[:6]}"
        budget = {
            "id": budget_id,
            "month": month,
            "category": category,
            "amount": amount,
            "created_at": ts,
            "updated_at": ts,
        }
        ledger["budgets"].append(budget)
    save_ledger(args.ledger, ledger)
    return budget


@json_command
@mutating
def budget_delete_command(args):
    ledger = load_ledger(args.ledger, create=False)
    remaining = []
    removed = None
    for budget in ledger.get("budgets", []):
        if not isinstance(budget, dict):
            remaining.append(budget)
            continue
        if removed is None and budget.get("id") == args.id:
            removed = budget
        else:
            remaining.append(budget)
    if not removed:
        raise SystemExit(f"Budget not found: {args.id}")
    ledger["budgets"] = remaining
    save_ledger(args.ledger, ledger)
    return removed
    if any(isinstance(item, dict) and item.get("id") == args.id for item in remaining):
        print("Warning: another budget still has the same id; run `ledger doctor`.", file=sys.stderr)


def backup_command(args):
    src = Path(args.ledger)
    if not src.is_file():
        raise SystemExit(f"Ledger not found: {src}")
    try:
        text = src.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"Could not read ledger {src}: {exc}") from exc
    if args.output:
        dest = Path(args.output)
        if dest.exists() and not getattr(args, "force", False):
            raise SystemExit(f"Backup already exists: {dest}. Pass --force to overwrite it.")
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = src.parent / "backups" / f"{src.stem}-{stamp}{src.suffix or '.json'}"
        counter = 1
        while dest.exists():
            dest = dest.with_name(f"{src.stem}-{stamp}-{counter}{src.suffix or '.json'}")
            counter += 1
    write_text_secure(dest, text)
    print(json.dumps({"ok": True, "ledger": str(src.resolve()), "backup": str(dest.resolve())}, ensure_ascii=False, indent=2))


@mutating
def init_command(args):
    path = Path(args.ledger)
    if path.exists() and not args.force:
        print(f"Ledger already exists: {path}")
        return
    if path.exists():
        backup = path.parent / "backups" / f"{path.stem}-preinit-{datetime.now().strftime('%Y%m%d-%H%M%S')}{path.suffix or '.json'}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        counter = 1
        while backup.exists():
            backup = backup.with_name(f"{path.stem}-preinit-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{counter}{path.suffix or '.json'}")
            counter += 1
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
        print(f"Backed up existing ledger to {backup}")
    from ledger_core.services import profile, runtime, history
    if path.exists():
        fresh = load_ledger(path)
        identity, revision, git_mode = fresh.get("ledger_id"), fresh["revision"], fresh.get("git_preference", "undecided")
        for name, source in {"user-profile.md": profile.profile_path(path), "runtime.json": profile.runtime_dir(path) / "runtime.json"}.items():
            if source.exists():
                write_text_secure(backup.with_name(backup.name + "." + name), source.read_text(encoding="utf-8"))
        fresh.clear()
        fresh.update(empty_ledger(args.currency))
        fresh.update(revision=revision, git_preference=git_mode)
        if identity:
            fresh["ledger_id"] = identity
        fresh.profile_override = profile.empty_profile()
        fresh.runtime = runtime.initial(fresh["schema_version"])
    else:
        fresh = empty_ledger(args.currency)
    chosen_mode = getattr(args, "git_mode", "undecided")
    if chosen_mode != "undecided":
        fresh["git_preference"] = chosen_mode
        if chosen_mode == "off":
            history.configure(path, "off")
    save_ledger(path, fresh)
    if chosen_mode == "local":
        history.configure(path, "local")
        history.record(path)
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
    parser.add_argument("--related-id", dest="related_transaction_id", default=None)
    parser.add_argument("--relation", choices=["refund", "reimbursement", "repayment", "split"], default=None)
    parser.add_argument("--remember", action="store_true", help="Save an explicit merchant preference")
    parser.add_argument("--exclude-from-totals", action="store_true", help="Keep the row but exclude it from financial totals")
    parser.add_argument("--include-in-totals", action="store_true", help="Override a learned exclusion and count the row in totals")
    # Plain-language aliases, so the agent does not have to speak in storage terms.
    parser.add_argument("--not-mine", action="store_true", help="Not my spending: keep the row but leave it out of totals")
    parser.add_argument("--advance", nargs="?", const="open", choices=list(VALID_ADVANCE_STATES), default=None,
                        help="Money expected back (垫付/借出). Defaults to open")


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
    from ledger_core.adapters.runtime_cli import add_commands
    add_commands(sub)
    from ledger_core.adapters.workflow_cli import add_commands as add_workflows
    add_workflows(sub)
    from ledger_core.adapters.extensions_cli import add_commands as add_extensions
    add_extensions(sub)

    p_init = sub.add_parser("init", help="Create an empty ledger")
    p_init.add_argument("--ledger", required=True)
    p_init.add_argument("--currency", default="CNY")
    p_init.add_argument("--force", action="store_true")
    p_init.add_argument("--git-mode", choices=["undecided", "off", "local"], default="undecided")
    p_init.set_defaults(func=init_command)

    p_add = sub.add_parser("add", help="Add one or more transactions")
    p_add.add_argument("--ledger", required=True)
    add_common_tx_flags(p_add, include_text=True)
    p_add.add_argument("--id", default=None)
    p_add.add_argument("--operation-id", default=None)
    p_add.add_argument("--expected-revision", type=int, default=None)
    p_add.add_argument("--allow-duplicate", action="store_true")
    p_add.set_defaults(func=add_transaction)

    p_record = sub.add_parser("record", help="Record a transaction and explain what it means")
    p_record.add_argument("--ledger", required=True)
    add_common_tx_flags(p_record, include_text=True)
    p_record.add_argument("--id", default=None)
    p_record.add_argument("--operation-id", default=None)
    p_record.add_argument("--expected-revision", type=int, default=None)
    p_record.add_argument("--allow-duplicate", action="store_true")
    p_record.add_argument("--json", action="store_true", help="Print the full payload instead of the reply")
    p_record.set_defaults(func=record_command)

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

    p_bill = sub.add_parser("bill", help="Monthly statement: facts plus an AI-written letter")
    bill_sub = p_bill.add_subparsers(dest="bill_command", required=True)
    p_bill_show = bill_sub.add_parser("show", help="Facts and saved letter for one month")
    p_bill_show.add_argument("--ledger", required=True)
    p_bill_show.add_argument("--month", default=None, help="YYYY-MM")
    p_bill_show.add_argument("--json", action="store_true")
    p_bill_show.set_defaults(func=bill_show_command)
    p_bill_list = bill_sub.add_parser("list", help="List saved monthly bills")
    p_bill_list.add_argument("--ledger", required=True)
    p_bill_list.add_argument("--json", action="store_true")
    p_bill_list.set_defaults(func=bill_list_command)
    p_bill_save = bill_sub.add_parser("save", help="Save the AI-written monthly letter")
    p_bill_save.add_argument("--ledger", required=True)
    p_bill_save.add_argument("--month", default=None, help="YYYY-MM")
    p_bill_save.add_argument("--title", default=None)
    p_bill_save.add_argument("--body", required=True)
    p_bill_save.set_defaults(func=bill_save_command)
    p_bill_render = bill_sub.add_parser("render", help="Write a standalone Canvas HTML bill")
    p_bill_render.add_argument("--ledger", required=True)
    p_bill_render.add_argument("--month", default=None, help="YYYY-MM")
    p_bill_render.add_argument("--output", default=None, help="Defaults to {ledger-stem}-bill-YYYY-MM.html")
    p_bill_render.add_argument("--template", default=None)
    p_bill_render.set_defaults(func=bill_render_command)

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

    p_category = sub.add_parser("category", help="List or define categories")
    cat = p_category.add_subparsers(dest="category_command", required=True)
    p_cat_list = cat.add_parser("list", help="List custom categories")
    p_cat_list.add_argument("--ledger", required=True)
    p_cat_list.add_argument("--json", action="store_true")
    p_cat_list.set_defaults(func=category_list_command)
    p_cat_set = cat.add_parser("set", help="Create, rename, or reparent a category")
    p_cat_set.add_argument("--ledger", required=True)
    p_cat_set.add_argument("--name", required=True)
    p_cat_set.add_argument("--old-name", dest="old_name", default=None)
    p_cat_set.add_argument("--icon", default=None)
    p_cat_set.add_argument("--parent", default=None)
    p_cat_set.set_defaults(func=category_set_command)

    p_review = sub.add_parser("review", help="Persisted review queue for data-quality findings")
    rev = p_review.add_subparsers(dest="review_command", required=True)
    p_rev_refresh = rev.add_parser("refresh", help="Scan the ledger and add new findings to the queue")
    p_rev_refresh.add_argument("--ledger", required=True)
    p_rev_refresh.add_argument("--json", action="store_true")
    p_rev_refresh.set_defaults(func=review_refresh_command)
    p_rev_next = rev.add_parser("next", help="Show the highest-priority open finding")
    p_rev_next.add_argument("--ledger", required=True)
    p_rev_next.add_argument("--json", action="store_true")
    p_rev_next.set_defaults(func=review_next_command)
    p_rev_notify = rev.add_parser("notifications", help="Read unacknowledged findings; acknowledge only after delivery")
    p_rev_notify.add_argument("--ledger", required=True)
    p_rev_notify.add_argument("--ack", help="Previously delivered JSON receipt")
    p_rev_notify.set_defaults(func=review_notifications_command)
    p_rev_resolve = rev.add_parser("resolve", help="Mark a finding as handled")
    p_rev_resolve.add_argument("--ledger", required=True)
    p_rev_resolve.add_argument("--id", required=True)
    p_rev_resolve.add_argument("--code", required=True)
    p_rev_resolve.add_argument("--note", default=None)
    p_rev_resolve.add_argument("--status", choices=["open", "waiting", "resolved", "dismissed", "snoozed"], default="resolved")
    p_rev_resolve.add_argument("--until", help="Snooze through YYYY-MM-DD")
    p_rev_resolve.set_defaults(func=review_resolve_command)

    p_adv = sub.add_parser("advance", help="List, settle, or write off money owed back")
    adv = p_adv.add_subparsers(dest="advance_command", required=True)
    p_adv_list = adv.add_parser("list", help="Show advances still owed")
    p_adv_list.add_argument("--ledger", required=True)
    p_adv_list.add_argument("--all", action="store_true", help="Include settled and written-off advances")
    p_adv_list.add_argument("--json", action="store_true")
    p_adv_list.set_defaults(func=advance_list_command)
    p_adv_settle = adv.add_parser("settle", help="Mark an advance as paid back")
    p_adv_settle.add_argument("--ledger", required=True)
    p_adv_settle.add_argument("--id", required=True)
    p_adv_settle.add_argument("--by", default=None, help="Transaction id of the repayment income row")
    p_adv_settle.set_defaults(func=advance_settle_command)
    p_adv_writeoff = adv.add_parser("writeoff", help="Give up on an advance that will not be repaid")
    p_adv_writeoff.add_argument("--ledger", required=True)
    p_adv_writeoff.add_argument("--id", required=True)
    p_adv_writeoff.add_argument("--date", default=None)
    p_adv_writeoff.set_defaults(func=advance_writeoff_command)

    p_habit = sub.add_parser("habit", help="Summarize usage habits from the ledger")
    hab = p_habit.add_subparsers(dest="habit_command", required=True)
    p_hab_list = hab.add_parser("list", help="Show the usage portrait used when recording")
    p_hab_list.add_argument("--ledger", required=True)
    p_hab_list.add_argument("--json", action="store_true")
    p_hab_list.set_defaults(func=habit_list_command)
    p_hab_profile = hab.add_parser("profile", help="Refresh or write the usage portrait")
    p_hab_profile.add_argument("--ledger", required=True)
    p_hab_profile.add_argument("--json", action="store_true")
    p_hab_profile.add_argument("--summary", default=None, help="Optional 2-4 sentence portrait written by the agent")
    p_hab_profile.set_defaults(func=habit_profile_command)
    p_hab_memory = hab.add_parser("memory", help="Write the periodic habit memory markdown next to the ledger")
    p_hab_memory.add_argument("--ledger", required=True)
    p_hab_memory.add_argument("--summary", default=None, help="Optional 2-4 sentence portrait written by the agent")
    p_hab_memory.set_defaults(func=habit_memory_command)
    p_hab_set = hab.add_parser("set", help="Remember an explicit user rule")
    p_hab_set.add_argument("--ledger", required=True)
    p_hab_set.add_argument("--phrase", default=None)
    p_hab_set.add_argument("--id", default=None)
    p_hab_set.add_argument("--amount", type=float, default=None)
    p_hab_set.add_argument("--clear-amount", action="store_true")
    p_hab_set.add_argument("--category", default=None)
    p_hab_set.add_argument("--merchant", default=None)
    p_hab_set.add_argument("--account", default=None)
    p_hab_set.add_argument("--type", default=None, choices=sorted(VALID_TYPES))
    p_hab_set.add_argument("--method", default=None, choices=sorted(VALID_METHODS))
    p_hab_set.add_argument("--pin", action="store_true")
    p_hab_set.add_argument("--unpin", action="store_true")
    p_hab_set.set_defaults(func=habit_set_command)
    p_hab_del = hab.add_parser("delete", help="Delete a habit by id or phrase")
    p_hab_del.add_argument("--ledger", required=True)
    p_hab_del.add_argument("--id", default=None)
    p_hab_del.add_argument("--phrase", default=None)
    p_hab_del.set_defaults(func=habit_delete_command)
    p_hab_rebuild = hab.add_parser("rebuild", help="Rebuild learned habits from existing transactions")
    p_hab_rebuild.add_argument("--ledger", required=True)
    p_hab_rebuild.set_defaults(func=habit_rebuild_command)

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
    p_acc_add.add_argument("--opening", default=None)
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

    p_brief = sub.add_parser("brief", help="How things stand, and what needs doing")
    p_brief.add_argument("--ledger", required=True)
    for flag, kwargs in (
        ("--range", {"choices": RANGE_PRESETS}),
        ("--month", {}),
        ("--start", {}),
        ("--end", {}),
    ):
        p_brief.add_argument(flag, **kwargs)
    p_brief.add_argument("--compare", action="store_true", default=True)
    p_brief.add_argument("--no-compare", dest="compare", action="store_false")
    p_brief.add_argument("--json", action="store_true")
    p_brief.set_defaults(func=brief_command)

    p_digest = sub.add_parser("digest", help="A report ready to forward, for scheduled runs")
    p_digest.add_argument("--ledger", required=True)
    for flag, kwargs in (
        ("--range", {"choices": RANGE_PRESETS}),
        ("--month", {}),
        ("--start", {}),
        ("--end", {}),
    ):
        p_digest.add_argument(flag, **kwargs)
    p_digest.add_argument("--json", action="store_true")
    p_digest.set_defaults(func=digest_command)

    p_insight = sub.add_parser("insight", help="What a careful person would notice about this ledger")
    p_insight.add_argument("--ledger", required=True)
    p_insight.add_argument("--limit", type=int, default=None)
    p_insight.add_argument("--json", action="store_true")
    p_insight.set_defaults(func=insight_command)

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

    p_backup = sub.add_parser("backup", help="Copy the ledger JSON to a timestamped backup")
    p_backup.add_argument("--ledger", required=True)
    p_backup.add_argument("--output", default=None)
    p_backup.add_argument("--force", action="store_true", help="Overwrite an explicit --output file")
    p_backup.set_defaults(func=backup_command)

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

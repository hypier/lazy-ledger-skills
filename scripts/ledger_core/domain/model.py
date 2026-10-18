"""Ledger document shape and neutral defaults, independent of command handlers."""
from datetime import datetime
import uuid

STORE_KIND = "lazy-ledger-docs"
STORE_VERSION = 1
SCHEMA_VERSION = 2
COLLECTIONS = ("transactions", "accounts", "budgets", "categories", "habits", "bills", "review_queue")

def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_currency(currency=None):
    return currency or "CNY"


def default_accounts(currency="CNY"):
    ts = now_iso()
    return [
        {
            "id": f"acc_{item[0]}",
            "name": item[1],
            "type": item[0],
            "currency": default_currency(currency),
            "opening_balance": 0.0,
            "opening_confirmed": False,
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
        "schema_version": SCHEMA_VERSION,
        "ledger_id": "ledger_" + uuid.uuid4().hex,
        "revision": 0,
        "git_preference": "undecided",
        "currency": default_currency(currency),
        "created_at": ts,
        "updated_at": ts,
        "transactions": [],
        "categories": [],
        "accounts": default_accounts(currency),
        "budgets": [],
        "habits": [],
        "bills": [],
        "review_queue": [],
        "store": STORE_KIND,
        "store_version": STORE_VERSION,
        "preferences": {"merchant_categories": {}, "merchant_aliases": {}, "default_account_id": None},
    }


def ledger_preferences(ledger):
    prefs = ledger.setdefault("preferences", {})
    if not isinstance(prefs, dict):
        raise ValueError("Ledger preferences must be an object")
    prefs.setdefault("merchant_categories", {})
    prefs.setdefault("merchant_aliases", {})
    prefs.setdefault("default_account_id", None)
    prefs.setdefault("habits_seeded", False)
    if not isinstance(prefs["merchant_categories"], dict):
        raise ValueError("Merchant categories must be an object")
    if not isinstance(prefs["merchant_aliases"], dict):
        raise ValueError("Merchant aliases must be an object")
    return prefs


def ensure_accounts(ledger, currency="CNY"):
    if "accounts" not in ledger:
        ledger["accounts"] = default_accounts(currency or ledger.get("currency"))
    if not isinstance(ledger["accounts"], list):
        raise ValueError("Ledger accounts must be a list")
    return ledger["accounts"]


def normalize_ledger(data, currency="CNY"):
    if not isinstance(data, dict):
        raise ValueError("Ledger JSON root must be an object")
    version = data.setdefault("schema_version", 1)
    if type(version) is not int or version not in (1, SCHEMA_VERSION):
        raise ValueError(f"Unsupported ledger schema version: {version}")
    data.setdefault("revision", 0)
    if type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("Ledger revision must be a nonnegative integer")
    if not data.get("currency"):
        data["currency"] = default_currency(currency)
    data.setdefault("created_at", now_iso())
    data.setdefault("updated_at", now_iso())
    data.setdefault("transactions", [])
    data.setdefault("categories", [])
    data.setdefault("budgets", [])
    data.setdefault("habits", [])
    data.setdefault("bills", [])
    data.setdefault("review_queue", [])
    data["store"] = STORE_KIND
    data.setdefault("store_version", STORE_VERSION)
    ledger_preferences(data)
    ensure_accounts(data, data.get("currency") or currency)
    for name in COLLECTIONS:
        if not isinstance(data[name], list):
            raise ValueError(f"Ledger {name} must be a list; repair explicitly")
    return data

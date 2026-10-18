"""Shared validation, account routing and persistence."""
import functools
import math
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path
from ledger_core.storage.documents import ledger_lock
from ledger_core.domain.model import now_iso, default_currency, empty_ledger, ledger_preferences, ensure_accounts
from ledger_core.domain.metrics import category_entries, DEFAULT_CATEGORY_PARENTS
from ledger_core.domain.semantics import settled_amount, remaining_amount, settle
from ledger_core.domain.money import decimal_amount
from ledger_core.storage import repository

_operation = ContextVar("ledger_operation", default=None)

from ledger_core.domain.vocabulary import (ACCOUNT_ALIASES, ACCOUNT_TYPE_TO_METHOD, BILL_MONTH_RE, CATEGORY_KEYWORDS, METHOD_TO_ACCOUNT_TYPE, TRANSFER_ROUTE_RE, VALID_ACCOUNT_TYPES, VALID_ADVANCE_STATES, VALID_METHODS, VALID_RELATIONS, VALID_SOURCES, VALID_TYPES, WEEKDAY_ALIASES, _ACCOUNT_TOKEN)


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


def mutating(func):
    """Hold the ledger lock across a command's read-modify-write cycle."""

    @functools.wraps(func)
    def wrapper(args):
        ledger_path = getattr(args, "ledger", None)
        if not ledger_path:
            return func(args)
        with ledger_lock(ledger_path):
            token = _operation.set({"id": getattr(args, "operation_id", None) or "op_" + uuid.uuid4().hex,
                                    "reason": func.__name__, "evidence": {"kind": "command", "input": {
                                        k: v for k, v in vars(args).items() if k not in {"func", "ledger"}}}})
            try:
                return func(args)
            finally:
                _operation.reset(token)

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
        if not isinstance(ledger, repository.Ledger):
            ledger = repository.Ledger(ledger)
        from ledger_core.domain.validation import validate
        validate(ledger)
        if _operation.get():
            ledger.operation = _operation.get()
        from .inspections import build_review_queue
        ledger['review_queue'] = build_review_queue(ledger, include_handled=True)
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


def find_transaction(ledger, tx_id):
    tx_id = ledger.get("identity_aliases", {}).get(tx_id, tx_id)
    for index, tx in enumerate(ledger["transactions"]):
        if isinstance(tx, dict) and tx.get("id") == tx_id:
            return index, tx
    raise SystemExit(f"Transaction not found: {tx_id}")

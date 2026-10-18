"""Shared mutation services used by CLI and HTTP."""
import hashlib
import json
import math
import re
import sys
import uuid
from datetime import date
from ledger_core.domain.model import now_iso, default_currency, ledger_preferences, ensure_accounts
from ledger_core.domain.metrics import month_key, category_entries
from ledger_core.domain.money import decimal_amount
from ledger_core.storage import repository

from ledger_core.domain.vocabulary import (VALID_ACCOUNT_TYPES, VALID_METHODS, VALID_TYPES)
from ledger_core.services.analysis import (link_unmatched_refunds, month_snapshot, settle_advances)
from ledger_core.services.common import (account_names, coerce_tags, find_account, find_transaction, learn_merchant_category, load_ledger, mutating, parse_when, payload_from_proposal, save_ledger, tx_sort_key, validate_category, validate_confidence, validate_month)
from ledger_core.services.habits import (ensure_habits, find_habit, learn_habits_from_transaction, refresh_usage_profile, seed_habits_if_needed, upsert_habit, write_habit_memory)
from ledger_core.services.interpretation import (likely_duplicate_candidates, merge_overrides, parse_text_transactions)
from ledger_core.services.presentation import (default_bill_month, find_bill)


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
        if proposal.get('amount') in (None, ''):
            from ledger_core.storage.documents import atomic_write_json
            from ledger_core.services.profile import runtime_dir
            atomic_write_json(runtime_dir(args.ledger)/'work'/'drafts'/(request_hash+'.json'),
                              {'version':1, 'reason':'missing_amount', 'request':request, 'candidate':proposal})
        tx = payload_from_proposal(ledger, proposal, args, tx_id=args.id if index == 0 else None)
        tx['field_evidence'] = {}
        for field in ('amount','type','category','merchant','occurred_at','account_id','to_account_id'):
            if tx.get(field) is None: continue
            argument = {'occurred_at':'date','account_id':'account','to_account_id':'to_account'}.get(field,field)
            explicit = getattr(args, argument, None) is not None
            kind = 'user' if explicit else 'confirmed_rule' if proposal.get('_habit') and field == 'amount' else 'parsed_text' if args.text else 'default'
            tx['field_evidence'][field] = {'kind':kind, 'value':tx[field], 'input':args.text if args.text else request,
                                           'profile_token':getattr(ledger,'profile_token',None)}
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


@mutating
def update_command(args):
    ledger = load_ledger(args.ledger, create=False)
    _, tx = find_transaction(ledger, args.id)
    import copy
    before = copy.deepcopy(tx)
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
    from ledger_core.services.proposals import mark_corrections
    mark_corrections(before, tx, {"kind":"user", "arguments":{k:v for k,v in vars(args).items() if k not in {"func","ledger"} and v is not None}})
    tx["updated_at"] = now_iso()
    ledger["transactions"].sort(key=tx_sort_key, reverse=True)
    learn_habits_from_transaction(ledger, tx)
    refresh_usage_profile(ledger, force=True)
    save_ledger(args.ledger, ledger)
    write_habit_memory(args.ledger, ledger, force=False)
    return tx


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

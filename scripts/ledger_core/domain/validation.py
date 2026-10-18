"""Financial invariants shared by proposals, imports and synchronization."""
from datetime import datetime
from decimal import Decimal
from .money import decimal_amount


def validate(ledger):
    accounts = {a.get("id"): a for a in ledger.get("accounts", []) if isinstance(a, dict)}
    if len(accounts) != len(ledger.get("accounts", [])) or None in accounts:
        raise ValueError("Account IDs must be unique")
    for identity, account in accounts.items():
        if not isinstance(identity, str) or not identity or not isinstance(account.get("name"), str):
            raise ValueError("Accounts need IDs and names")
        if account.get("currency", ledger["currency"]) != ledger["currency"]:
            raise ValueError("Account currency differs from ledger")
        decimal_amount(account.get("opening_balance", 0))
    budget_ids = set()
    for budget in ledger.get("budgets", []):
        if not isinstance(budget, dict) or not budget.get("id") or budget["id"] in budget_ids or decimal_amount(budget.get("amount")) <= 0:
            raise ValueError("Budgets need unique IDs and positive amounts")
        budget_ids.add(budget["id"])
    rows = ledger.get("transactions", [])
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in by_id:
            raise ValueError("Transaction IDs must be unique nonempty strings")
        by_id[row["id"]] = row
        if row.get("type") not in {"expense", "income", "refund", "transfer"}:
            raise ValueError("Unsupported transaction type")
        if decimal_amount(row.get("amount")) <= 0:
            raise ValueError("Amounts must be positive")
        if row.get("currency", ledger["currency"]) != ledger["currency"]:
            raise ValueError("Transaction currency differs from ledger")
        datetime.fromisoformat(row["occurred_at"])
        for key in ("account_id", "to_account_id"):
            if row.get(key) and row[key] not in accounts:
                raise ValueError(f"Unknown {key}: {row[key]}")
        if row["type"] == "transfer":
            if not row.get("account_id") or not row.get("to_account_id") or row["account_id"] == row["to_account_id"]:
                raise ValueError("A transfer needs two distinct accounts")
        if row.get("advance") and row["type"] != "expense":
            raise ValueError("Only an expense can be an advance")
        if row.get("balance_effect") is False and row.get("relation") != "writeoff":
            raise ValueError("Only writeoff rows may suppress cash movement")
    allocated = set()
    for row in rows:
        related = row.get("related_transaction_id")
        if related and (related not in by_id or related == row["id"]):
            raise ValueError("Dangling or self transaction relation")
        if row.get("relation") and not related:
            raise ValueError("A relation needs its counterpart")
        if row.get("relation") and row["relation"] not in {"refund", "repayment", "reimbursement", "writeoff", "split"}:
            raise ValueError("Unsupported relation")
        if row.get("relation") == "refund" and (row["type"] != "refund" or by_id[related]["type"] != "expense"):
            raise ValueError("A refund relation must point from a refund to an expense")
        if row.get("relation") == "writeoff" and row["type"] != "expense":
            raise ValueError("A writeoff must be an expense")
        if row.get("advance_settlements") and not row.get("advance"):
            raise ValueError("Settlement allocations require an advance")
        total = Decimal(0)
        for entry in row.get("advance_settlements", []):
            income_id = entry.get("transaction_id")
            income = by_id.get(income_id)
            if not income or income.get("type") != "income" or income_id in allocated:
                raise ValueError("Settlement income is missing or allocated twice")
            if income.get("related_transaction_id") != row["id"] or income.get("relation") not in {"repayment", "reimbursement"}:
                raise ValueError("Settlement counterpart does not agree")
            amount = decimal_amount(entry["amount"])
            if amount != decimal_amount(income["amount"]) or amount <= 0:
                raise ValueError("Settlement allocation differs from income")
            if datetime.fromisoformat(income["occurred_at"]).astimezone() < datetime.fromisoformat(row["occurred_at"]).astimezone():
                raise ValueError("Settlement precedes its advance")
            total += amount
            allocated.add(income_id)
        loss_id = row.get("writeoff_transaction_id")
        loss_amount = decimal_amount(row.get("written_off_amount", 0))
        if loss_id:
            loss = by_id.get(loss_id)
            if not loss or loss.get("relation") != "writeoff" or loss.get("related_transaction_id") != row["id"] or loss.get("balance_effect") is not False:
                raise ValueError("Writeoff counterpart does not agree")
            if loss_amount != decimal_amount(loss["amount"]) or row.get("advance") != "written_off":
                raise ValueError("Writeoff amount/state differs from its advance")
        if total + loss_amount > decimal_amount(row["amount"]):
            raise ValueError("Settlement and writeoff exceed original advance")
        if row.get("advance_settlements") and row.get("advance") == "settled" and total != decimal_amount(row["amount"]):
            raise ValueError("A settled advance must be fully allocated")
        if loss_id and total + loss_amount != decimal_amount(row["amount"]):
            raise ValueError("Writeoff must close exactly the remaining advance")
        if row.get("relation") == "writeoff" and by_id[related].get("writeoff_transaction_id") != row["id"]:
            raise ValueError("Orphan writeoff")
    for row in rows:
        if row.get("relation") in {"repayment", "reimbursement"} and row["id"] not in allocated:
            raise ValueError("Repayment must have an agreeing settlement allocation")
    sources = {}
    for row in rows:
        for source in row.get("sources", []):
            key = (source.get("provider"), source.get("transaction_id"))
            if not all(key):
                key = (source.get("file_hash"), source.get("row"))
            if all(x is not None for x in key):
                if key in sources and sources[key] != row["id"]:
                    raise ValueError("Source identity occurs on multiple transactions")
                sources[key] = row["id"]
    return ledger

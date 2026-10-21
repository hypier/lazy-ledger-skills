"""One interpretation of spending for budgets, analysis and reporting."""
from .money import decimal_amount
from datetime import datetime

VALID_ADVANCE_STATES = ("open", "settled", "written_off")
NATURES = ("spending", "earning", "transfer", "not_mine", "advance")
SETTLEMENTS = ("none", "pending", "settled", "written_off")


def settlement_of(tx):
    return {"open": "pending", "settled": "settled", "written_off": "written_off"}.get(tx.get("advance"), "none")


def nature_of(tx):
    if tx.get("excluded_from_totals"):
        return "not_mine"
    if tx.get("type") == "transfer":
        return "transfer"
    if tx.get("advance") in VALID_ADVANCE_STATES or tx.get("relation") in {"reimbursement", "repayment"}:
        return "advance"
    return "earning" if tx.get("type") == "income" else "spending"


def counts_as(tx, kind):
    if not isinstance(tx, dict) or tx.get("type") != kind:
        return False
    nature = nature_of(tx)
    if nature in {"not_mine", "transfer"}:
        return False
    if nature == "advance":
        # New writeoffs are dated loss rows; the original cash movement stays unchanged.
        return kind == "expense" and tx.get("advance") == "written_off" and not tx.get("writeoff_transaction_id")
    return True


def settled_amount(tx):
    return sum((decimal_amount(x.get("amount", 0)) for x in tx.get("advance_settlements", []) if isinstance(x, dict)), decimal_amount(0))


def remaining_amount(tx):
    if tx.get("advance") == "settled" and not tx.get("advance_settlements"):
        return decimal_amount(0)  # legacy explicitly closed advance
    return max(decimal_amount(0), decimal_amount(tx.get("amount", 0)) - settled_amount(tx) - decimal_amount(tx.get("written_off_amount", 0)))


def settle(advance, income):
    if advance.get("advance") != "open" or income.get("type") != "income":
        raise ValueError("Settlement requires an open advance and an income row")
    if income.get("currency") != advance.get("currency"):
        raise ValueError("Settlement currencies must agree")
    if datetime.fromisoformat(income["occurred_at"]).astimezone() < datetime.fromisoformat(advance["occurred_at"]).astimezone():
        raise ValueError("Settlement cannot precede the advance")
    entries = advance.get("advance_settlements", [])
    if any(x.get("transaction_id") == income.get("id") for x in entries):
        return None
    if income.get("related_transaction_id") and income["related_transaction_id"] != advance.get("id"):
        raise ValueError("Income already belongs to another transaction")
    amount = decimal_amount(income.get("amount"))
    if amount <= 0 or amount > remaining_amount(advance):
        raise ValueError("Settlement exceeds the outstanding advance")
    advance.setdefault("advance_settlements", []).append({"transaction_id": income["id"], "amount": float(amount), "occurred_at": income["occurred_at"]})
    advance["advance"] = "settled" if remaining_amount(advance) == 0 else "open"
    income.update(relation="reimbursement", related_transaction_id=advance["id"])
    return advance["id"]

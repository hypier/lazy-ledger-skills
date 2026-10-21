"""Read-only diagnostics and review candidates."""
import math

from ledger_core.domain.vocabulary import (INCOME_ONLY_CATEGORIES, REQUIRED_TX_FIELDS, VALID_ACCOUNT_TYPES, VALID_METHODS, VALID_RELATIONS, VALID_TYPES)
from ledger_core.services.analysis import (unmatched_refunds)
from ledger_core.services.common import (account_names, find_account, known_category_names, validate_month)
from ledger_core.services.interpretation import (likely_duplicate_candidates)


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
    from ledger_core.services.findings import reconcile, insight_candidate
    from ledger_core.domain.metrics import account_balances
    from ledger_insight import build_findings
    for finding in build_findings(ledger, balances=account_balances(ledger)):
        if finding.get('severity') in {'alert', 'warn'} or finding.get('kind') == 'outlier':
            items.append(insight_candidate(finding))
    queue = reconcile(ledger, items)
    return queue if include_handled else [item for item in queue if item.get("status") == "open"]

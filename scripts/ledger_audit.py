#!/usr/bin/env python3
"""Read-only duplicate and source-link audit for a Lazy Ledger JSON file."""

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path


REFERENCE_FIELDS = {
    "wechat_transaction_id": "wechat",
    "wechat_merchant_order_id": "wechat",
    "bank_statement_ref": "bank",
    "credit_statement_ref": "credit",
    "derived_ref": "derived",
}


def normalized_merchant(transaction):
    value = (
        transaction.get("merchant")
        or transaction.get("bank_counterparty")
        or transaction.get("credit_statement_description")
        or transaction.get("note")
        or ""
    )
    return re.sub(r"[\W_]+", "", str(value).lower(), flags=re.UNICODE)


def merchant_similarity(left, right):
    left_value = normalized_merchant(left)
    right_value = normalized_merchant(right)
    if not left_value or not right_value:
        return 0.0
    if left_value == right_value:
        return 1.0
    if left_value in right_value or right_value in left_value:
        return 0.95
    return SequenceMatcher(None, left_value, right_value).ratio()


def transaction_day(transaction):
    value = str(transaction.get("occurred_at") or "")[:10]
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def source_families(transaction):
    return {
        family
        for field, family in REFERENCE_FIELDS.items()
        if str(transaction.get(field) or "").strip()
    }


def shared_reference(left, right):
    for field in REFERENCE_FIELDS:
        left_value = str(left.get(field) or "").strip()
        right_value = str(right.get(field) or "").strip()
        if left_value and left_value == right_value:
            return field, left_value
    return None


def compact(transaction):
    return {
        "id": transaction.get("id"),
        "occurred_at": transaction.get("occurred_at"),
        "type": transaction.get("type"),
        "amount": transaction.get("amount"),
        "merchant": transaction.get("merchant"),
        "account": transaction.get("account"),
        "source_families": sorted(source_families(transaction)),
    }


def reference_report(transactions):
    report = {}
    for field in REFERENCE_FIELDS:
        groups = defaultdict(list)
        for transaction in transactions:
            value = str(transaction.get(field) or "").strip()
            if value:
                groups[value].append(transaction.get("id"))
        duplicates = [
            {"value": value, "ids": ids}
            for value, ids in sorted(groups.items())
            if len(ids) > 1
        ]
        report[field] = {
            "count": sum(len(ids) for ids in groups.values()),
            "unique": len(groups),
            "duplicates": duplicates,
        }
    return report


def source_confirmed_groups(transactions):
    grouped = defaultdict(list)
    for transaction in transactions:
        day = transaction_day(transaction)
        merchant = normalized_merchant(transaction)
        if not day or not merchant:
            continue
        try:
            amount = round(float(transaction.get("amount") or 0), 2)
        except (TypeError, ValueError):
            continue
        key = (day.isoformat(), transaction.get("type"), amount, merchant)
        grouped[key].append(transaction)

    confirmed = []
    for (day, tx_type, amount, merchant), rows in grouped.items():
        if len(rows) < 2:
            continue
        families = [source_families(row) for row in rows]
        if not all(families):
            continue
        if len(set.intersection(*families)) == 0:
            continue
        has_shared_reference = any(
            shared_reference(left, right)
            for index, left in enumerate(rows)
            for right in rows[index + 1 :]
        )
        if has_shared_reference:
            continue
        confirmed.append(
            {
                "date": day,
                "type": tx_type,
                "amount": amount,
                "merchant_key": merchant,
                "ids": [row.get("id") for row in rows],
                "source_families": sorted(set.intersection(*families)),
            }
        )
    return sorted(confirmed, key=lambda item: (item["date"], item["amount"], item["merchant_key"]))


def cross_source_candidates(transactions, window_days=1, threshold=0.82):
    candidates = []
    for index, left in enumerate(transactions):
        left_day = transaction_day(left)
        if left_day is None:
            continue
        try:
            left_amount = round(float(left.get("amount") or 0), 2)
        except (TypeError, ValueError):
            continue
        for right in transactions[index + 1 :]:
            if left.get("type") != right.get("type"):
                continue
            right_day = transaction_day(right)
            if right_day is None:
                continue
            day_delta = abs((left_day - right_day).days)
            if day_delta > window_days:
                continue
            try:
                right_amount = round(float(right.get("amount") or 0), 2)
            except (TypeError, ValueError):
                continue
            if left_amount != right_amount:
                continue
            if shared_reference(left, right):
                continue

            similarity = merchant_similarity(left, right)
            if similarity < threshold:
                continue

            left_families = source_families(left)
            right_families = source_families(right)
            if left_families and right_families and left_families.intersection(right_families):
                continue

            candidates.append(
                {
                    "reason": "same_day" if day_delta == 0 else "adjacent_day",
                    "day_delta": day_delta,
                    "merchant_similarity": round(similarity, 3),
                    "left": compact(left),
                    "right": compact(right),
                }
            )
    return candidates


def audit(ledger, window_days=1, threshold=0.82):
    transactions = [row for row in ledger.get("transactions", []) if isinstance(row, dict)]
    ids = [str(row.get("id") or "") for row in transactions]
    missing_id_indexes = [index for index, value in enumerate(ids) if not value]
    duplicate_ids = [value for value, count in Counter(ids).items() if value and count > 1]
    references = reference_report(transactions)
    candidates = cross_source_candidates(transactions, window_days=window_days, threshold=threshold)
    reference_duplicate_count = sum(len(item["duplicates"]) for item in references.values())
    return {
        "ok": not missing_id_indexes and not duplicate_ids and reference_duplicate_count == 0 and not candidates,
        "transaction_count": len(transactions),
        "unique_transaction_ids": len({value for value in ids if value}),
        "missing_transaction_id_indexes": missing_id_indexes,
        "duplicate_transaction_ids": sorted(duplicate_ids),
        "source_references": references,
        "duplicate_source_reference_count": reference_duplicate_count,
        "cross_source_candidate_count": len(candidates),
        "cross_source_candidates": candidates,
        "source_confirmed_same_value_groups": source_confirmed_groups(transactions),
    }


def markdown(report):
    lines = [
        f"Ledger audit: {report['transaction_count']} transactions",
        f"- Unique transaction IDs: {report['unique_transaction_ids']}",
        f"- Missing transaction IDs: {len(report['missing_transaction_id_indexes'])}",
        f"- Duplicate source references: {report['duplicate_source_reference_count']}",
        f"- Cross-source candidates: {report['cross_source_candidate_count']}",
        f"- Source-confirmed same-value groups: {len(report['source_confirmed_same_value_groups'])}",
    ]
    for field, details in report["source_references"].items():
        lines.append(f"- {field}: {details['count']} rows / {details['unique']} unique")
    for candidate in report["cross_source_candidates"]:
        left = candidate["left"]
        right = candidate["right"]
        lines.append(
            "- REVIEW "
            f"{left['id']} <-> {right['id']} | {left['amount']} | "
            f"{left['merchant']} / {right['merchant']}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--window-days", type=int, default=1)
    parser.add_argument("--merchant-threshold", type=float, default=0.82)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Exit 2 when unresolved duplicate evidence exists")
    args = parser.parse_args()

    if args.window_days < 0:
        raise SystemExit("--window-days must be non-negative")
    if not 0 <= args.merchant_threshold <= 1:
        raise SystemExit("--merchant-threshold must be between 0 and 1")

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    report = audit(ledger, window_days=args.window_days, threshold=args.merchant_threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(markdown(report))
    if args.strict and not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

# Reconcile Multi-Source Imports

Use this workflow for official WeChat/Alipay exports, bank statements, credit-card statements, or any request to check duplicates across sources. For long screenshots, first follow [image-batch-import.md](image-batch-import.md).

## Authority Model

Match evidence in this order:

1. Exact authoritative reference: transaction ID, merchant order ID, bank statement reference, or credit statement reference.
2. Exact timestamp, amount, type, and merchant.
3. Same or adjacent posting day, amount, type, and normalized merchant, with manual review.

Date and amount alone are never enough to delete a row. Distinct authoritative references can represent real repeated purchases, even when date, amount, and merchant are identical.

Common evidence fields:

- WeChat: `wechat_transaction_id`, `wechat_merchant_order_id`, `wechat_payment_method`.
- Bank: `bank_statement_ref`, `bank_statement_summary`, `bank_counterparty`, `bank_balance`.
- Credit card: `credit_statement_ref`, `credit_statement_month`, `credit_statement_post_date`, `credit_statement_description`.
- Derived rows: `derived_ref` plus a note explaining the derivation.

Preserve source text and attachment paths. Keep transaction time in `occurred_at`; keep posting date in its statement-specific field.

## Account And Channel Reconciliation

- `account` is the actual balance-bearing account. Prefer a specifically named bank/card account from the source.
- `payment_channel` describes the route (`wechat`, `alipay`, `paypal`, and so on) and does not replace `account`.
- If an official payment export names a bank card, update the existing payment row to that account instead of adding a second bank expense.
- Credit-card consumption reduces the credit account. Repayment is a `transfer` into that credit account, not income or expense.
- Credit-card opening balance must be derived from the earliest validated statement and checked against every closing balance.

## Workflow

1. Inspect the source with a structured parser appropriate to the file type. Never parse XLSX or PDF with ad hoc line splitting when a real parser is available.
2. Validate statement row counts, charges, refunds, repayments, and opening/closing balances before touching the ledger.
3. Run the read-only audit:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" audit \
  --json
```

4. Build deterministic source references. Match each source row to at most one ledger row and each ledger row to at most one source row.
5. Run a semantic classification pass before import: consult the habit portrait and merchant preferences, normalize merchant/description text, and assign the most specific configured category. `其他` is allowed only when evidence is insufficient. Group only materially ambiguous type/account/category decisions for user confirmation. A screenshot still requires explicit table confirmation; an official statement import may proceed when the user's request already authorizes it and source validation is complete.
6. Apply first to a temporary ledger copy. Verify expected additions, updates, balances, source-reference uniqueness, and rerun behavior.
7. Create a real backup, recheck that the live ledger has not changed, then apply once to the real ledger.
8. Run `audit --json`, `doctor --json`, the relevant `show --month ... --compare`, and `habit memory` after a large import. Verify `/api/ledger` only when the local service is running.

## Duplicate Decisions

- Same authoritative reference on two rows: confirmed duplicate evidence; merge source metadata into the retained row before removing anything.
- Matching rows from disjoint sources: candidate for the same purchase. Merge only when exact time, account, merchant, or other source context corroborates it; then attach both sources to the retained row.
- Distinct references from the same source: source-confirmed distinct unless other evidence proves duplication.
- Same merchant/amount but different exact times: usually distinct.
- `expense` plus `refund`: preserve both; they are not duplicates.
- Repeated statement lines with no time: preserve when the statement contains separate rows and its totals reconcile.

Use `--allow-duplicate` only on a reviewed import that intentionally contains a valid repeated purchase or expense/refund pair. Never use it to bypass an unreviewed whole-file collision.

## Stop Conditions

Do not write when statement totals fail, partial source linkage cannot be resolved deterministically, an account choice changes financial meaning, or a duplicate collision cannot be resolved from available evidence. Report the exact unresolved rows and ask for direction.

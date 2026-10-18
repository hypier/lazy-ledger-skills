# Proactive Review And Repair

Use this reference whenever recording, importing, correcting, or answering a query reveals a possible data problem. The agent should resolve clear cases itself and reserve conversation for genuine ambiguity.

## Detect

Run the smallest relevant read-only check:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" doctor --json
"$LEDGER_SKILL_DIR/scripts/ledger" audit --json
```

Use `doctor` for missing fields, invalid types or amounts, unknown accounts, broken transaction relations, likely duplicates, and `category_type_mismatch` (an income-only category on a non-income row). Use `audit` for authoritative source references and cross-source duplicate candidates. For a summary, also inspect review queues such as low-confidence rows, pending reimbursements, unmatched refunds, and open advances (`advance list`).

## Already Linked Automatically

A refund with exactly one confident counterpart (within 90 days, matching amount, compatible merchant) is linked at write time: the row gets `related_transaction_id` and `relation: refund`, and `add` returns a `refund_links` field. Do not ask about it and do not re-link it.

Ask only when no counterpart exists (reported under 未对上的退款) or when several materially different candidates remain, in which case nothing is linked and the decision stays with the user.

The same rule applies to advances: a reimbursement equal to exactly one open advance settles it automatically and the income row gets `relation: reimbursement`. Several matching advances means nothing is settled — ask.

## Persisted Review Queue

`review refresh` derives findings from the current ledger and appends new ones to `review_queue` in the ledger file; `review next` returns the highest-priority open item; `review resolve --id <tx> --code <code>` closes one item with an optional `--note`. Because the queue lives in the ledger, a review session can span several calls instead of restarting the scan each time, and a resolved item is not recreated by a later refresh.

## Discuss

Resolve clear findings automatically when evidence agrees: distinct authoritative source references imply independent source rows; a single nearby same-merchant, same-amount expense is a strong refund link; a malformed field with one deterministic correction can be fixed in place. Present one unresolved repair group at a time. Findings with the same repair type and evidence pattern may share one confirmation question; list every affected row, finding, evidence, and proposed action in that group. Keep unrelated or destructive decisions separate. Say what is known and what is uncertain.

## Confirm And Repair

Ask exactly one confirmation question before deleting or merging a transaction, changing its type, amount, account, or date, or adding or changing a relation. After the answer, either apply that repair group or record that the user declined it. Update by transaction ID, preserve unknown fields, and make the smallest requested change. Create a backup before a deletion. Then rerun `doctor --json` or `audit --json`, report the result, and only then present the next group.

If no deterministic repair exists, leave the rows unchanged and ask one focused question. Do not hide the issue by changing it to `其他`, lowering the amount, or suppressing the warning.

## Timing

- After a single clear add: check the new row and obvious duplicate candidates; keep the response concise when clean.
- Before asking about a refund relation, search nearby dates for the same merchant and amount; ask only if the search is inconclusive. After an import or bulk edit: run the full checks, then present the highest-priority repair group.
- During a totals query: answer the requested total first, then mention a warning only when it may affect that total or indicates data loss.

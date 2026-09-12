# Proactive Review And Repair

Use this reference whenever recording, importing, correcting, or answering a query reveals a possible data problem. The agent should resolve clear cases itself and reserve conversation for genuine ambiguity.

## Detect

Run the smallest relevant read-only check:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" doctor --json
"$LEDGER_SKILL_DIR/scripts/ledger" audit --json
```

Use `doctor` for missing fields, invalid types or amounts, unknown accounts, broken transaction relations, and likely duplicates. Use `audit` for authoritative source references and cross-source duplicate candidates. For a summary, also inspect review queues such as low-confidence rows, pending reimbursements, and unmatched refunds.

## Discuss

Resolve clear findings automatically when evidence agrees: distinct authoritative source references imply independent source rows; a single nearby same-merchant, same-amount expense is a strong refund link; a malformed field with one deterministic correction can be fixed in place. Present one unresolved repair group at a time. Findings with the same repair type and evidence pattern may share one confirmation question; list every affected row, finding, evidence, and proposed action in that group. Keep unrelated or destructive decisions separate. Say what is known and what is uncertain.

## Confirm And Repair

Ask exactly one confirmation question before deleting or merging a transaction, changing its type, amount, account, or date, or adding or changing a relation. After the answer, either apply that repair group or record that the user declined it. Update by transaction ID, preserve unknown fields, and make the smallest requested change. Create a backup before a deletion. Then rerun `doctor --json` or `audit --json`, report the result, and only then present the next group.

If no deterministic repair exists, leave the rows unchanged and ask one focused question. Do not hide the issue by changing it to `其他`, lowering the amount, or suppressing the warning.

## Timing

- After a single clear add: check the new row and obvious duplicate candidates; keep the response concise when clean.
- Before asking about a refund relation, search nearby dates for the same merchant and amount; ask only if the search is inconclusive. After an import or bulk edit: run the full checks, then present the highest-priority repair group.
- During a totals query: answer the requested total first, then mention a warning only when it may affect that total or indicates data loss.

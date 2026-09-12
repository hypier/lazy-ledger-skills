# Proactive Review And Repair

Use this reference whenever recording, importing, correcting, or answering a query reveals a possible data problem. The goal is a short conversation that leaves the ledger more trustworthy.

## Detect

Run the smallest relevant read-only check:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" doctor --json
"$LEDGER_SKILL_DIR/scripts/ledger" audit --json
```

Use `doctor` for missing fields, invalid types or amounts, unknown accounts, broken transaction relations, and likely duplicates. Use `audit` for authoritative source references and cross-source duplicate candidates. For a summary, also inspect review queues such as low-confidence rows, pending reimbursements, and unmatched refunds.

## Discuss

Present one compact finding at a time with the affected row, finding, evidence, and one proposed action. Say what is known and what is uncertain. A duplicate candidate is not a confirmed duplicate; an unusual amount is not automatically an error. Keep source references and attachments visible in the explanation. Maintain the remaining findings as an internal queue.

## Confirm And Repair

Ask exactly one confirmation question before deleting or merging a transaction, changing its type, amount, account, or date, or adding or changing a relation. After the answer, either apply that single repair or record that the user declined it. Update by transaction ID, preserve unknown fields, and make the smallest requested change. Create a backup before a deletion. Then rerun `doctor --json` or `audit --json`, report the result, and only then present the next issue.

If no deterministic repair exists, leave the rows unchanged and ask one focused question. Do not hide the issue by changing it to `其他`, lowering the amount, or suppressing the warning.

## Timing

- After a single clear add: check the new row and obvious duplicate candidates; keep the response concise when clean.
- After an import or bulk edit: run the full checks, then present the highest-priority finding before asking about another one.
- During a totals query: answer the requested total first, then mention a warning only when it may affect that total or indicates data loss.

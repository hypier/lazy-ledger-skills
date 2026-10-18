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

## What the butler notices on its own

`ledger_insight.py` runs the detectors; `ledger insight` prints them, `brief`
folds the top ones into the daily summary, and `record` re-checks only what is
relevant to the row just written. They differ in what kind of mistake they look
for:

| Detector | Looks for | Severity |
|---|---|---|
| `negative_balance` | a derived balance below zero, which means missing data | alert |
| `duplicate_suspected` | same amount, same subject, within 48 hours | alert |
| `stale_advance` | money owed for more than 30 days | warn |
| `missing_recurring` | a bill present 3+ consecutive months that stopped for 2+ | warn |
| `category_surge` | a category spending 1.5x last month's count, with the driver named | warn |
| `outlier` | one purchase 5x the usual single amount | info |
| `habit_merchant` | one merchant frequent enough that the total is worth seeing | info |
| `category_drift` | one merchant filed under two categories in different months | info |
| `missing_details` | rows with no merchant or note | info |

Two rules keep this from becoming nagging:

- **Order by what changes the total.** A possible double entry outranks a
  suggestion; a suggestion never outranks a contradiction.
- **Say each thing once.** Advice is dropped when a finding already named the
  same category or merchant, because repeating it reads as inattention.

## Advice, and its limits

`ledger_advice.py` produces at most two suggestions a month: a cap for the
category that dominates spending, and an observation about a merchant that
became a habit. Both are only offered when the month holds at least 8 rows --
below that, a claim about habits describes the sample, not the person.

The wording matters as much as the rule. Name the number, offer the action, and
leave the decision open:

> 这个月外食 7 次，¥315.00，占了 89% 的支出。
> 要不要给外食设个 ¥300.00 的上限？超了我提醒你。

Not `建议合理规划消费`. A suggestion the user cannot act on, or that implies
they were wrong to spend, is worse than silence.

## Brief versus digest

`brief` is for the user, in chat: it may include `→ ledger ...` commands for
repairs. `digest` is shaped to be forwarded: no commands, no field names, no
IDs, and it ends with what (if anything) needs a human. Choose `digest` when
the user asks to be told on a schedule or asks for something to send.

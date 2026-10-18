---
name: lazy-ledger
description: Maintain the user's local JSON ledger as their financial butler. Use when they mention spending, income, a payment, money owed, or a bill, ask how they are doing with money, or when reconciling WeChat/Alipay/bank statements, correcting rows, setting budgets, or opening the localhost ledger UI.
metadata:
  version: 2.0.0
---

# Lazy Ledger

## What this is

The user provides data; you do the bookkeeping. They should be able to say
"打车26，公司报销" or "昨天那杯星巴克记错了" and be understood, not asked to
fill in a form. Everything below serves that: read the intent, decide the
accounting, write it, and say what it means.

The ledger is a local JSON document, not a database. You are the only writer.

## The six jobs

Every request is one of these. Name the job before choosing a command.

| Job | What it means | Entry |
|---|---|---|
| **Record** | Turn a sentence, image, or file into rows | `record`, `add`, `import-tsv` |
| **Correct** | Fix what was already written | `find`, `update`, `delete` |
| **Explains** | "why did that count as spending" | `parse`, `show --json` |
| **Notice** | Proactively surface what the user missed | `insight`, `brief`, `doctor`, `audit` |
| **Report** | Turn a period into something readable | `show`, `digest`, `bill` |
| **Settle** | Track money that comes back | `advance list/settle/writeoff` |

If a request does not fit one of these, it is probably a question about the
ledger -- answer it from `show --json` rather than writing anything.

## How to behave

**Record without ceremony.** A clear single purchase is written immediately and
confirmed in one line. Ask only when the answer changes what the row *means*:
the amount, the type (spending vs income vs transfer), or which row is being
corrected. Category hesitation is not worth a question.

**Say why.** When a reading is not obvious, use `record` instead of `add`: it
replies "记好了：垫付 ¥26.00 · 其他 · 公司 · 微信零钱 / 这笔不算支出：公司报销"
rather than a bare confirmation. When the user asks "why", call `parse` and
quote the matched word, never the rule name.

**Speak about money, not about the tool.** Replies name the amount, the
category, and what changed in the month total. Never answer with a field list,
a raw record, or a command line unless the user asked for it. `digest` is the
output shaped to be forwarded to another person; `record` and `brief` are
shaped to be read by the user in chat.

**Notice what they did not ask about, once.** After a write, `record` already
reports what it can see about *that* row. Beyond that, `insight` finds the
things a careful bookkeeper would mention: a payment that looks entered twice,
a subscription that stopped, one merchant filed under two categories, an
account whose balance went negative. Surface the highest-priority one and stop.
Two findings is a briefing; five is a complaint.

**Advise gently and only on request-quality evidence.** `brief` includes
suggestions -- a cap on the category that is eating the month, the merchant
that became a habit. These are options, not verdicts: state the number, offer
the action, and let the user decide. Never editorialize about spending when the
user asked for a total.

**Report on a schedule when asked.** `digest` produces a self-contained text
with no commands in it, suitable for a daily or monthly message. If the user
asks to be told regularly, generate it and send it; do not ask them to run it.

**Ask at most one question, and only when it matters.** If two readings would
change balances, totals, or which account was charged, ask -- and show the
reading you are inclined toward. Otherwise decide, write, and say what you
assumed. A correction applies to the named row only; remember it as a rule only
when the user says so.

**Never invent.** No unreadable transactions, merchant names, account numbers,
or totals. Uncertainty goes in `note` and `confidence`. A screenshot always
gets a visible confirmation table before any write.

## Accounting invariants

The ledger only has to obey these; every command enforces them.

- Amounts are positive; `type` gives the direction. Moving money between the
  user's own accounts is `transfer` and never touches income or spending.
- Decide the *event* once and let `type` and `category` follow. A reimbursement
  arriving is income; a reimbursement the user *expects* is not income yet --
  it is an advance (`advance: open`), which stays out of spending until it is
  settled or written off.
- An advance is settled by the money that pays it back, and only when exactly
  one candidate matches. Otherwise ask.
- Paying another person is spending, even when the sentence says 转账.
- The funding account and the payment channel are different facts: a WeChat
  payment from a named bank card belongs to that bank account.
- A refund is a second row, not an edit of the first. The original expense
  stays.
- Rows keep their source references and unknown fields. Never delete or merge
  rows merely because date and amount match -- ask first.

## Commands

Set `LEDGER_SKILL_DIR` to the directory containing this file, then use
`"$LEDGER_SKILL_DIR/scripts/ledger"`. The wrapper injects `--ledger`; pass
`--ledger /path.json` to target another ledger.

| Need | Command |
|---|---|
| Record and explain | `ledger record --text "..."` |
| Record, machine-readable | `ledger add --text "..." --json` |
| How would this be read? | `ledger parse --text "..."` |
| What needs attention | `ledger insight` |
| Today's summary for the user | `ledger brief` |
| Forwardable period summary | `ledger digest --range this-month` |
| Period totals and comparison | `ledger show --range this-month --compare` |
| Find then correct | `ledger find --text "..."`, then `ledger update --id tx_...` |
| Money owed to the user | `ledger advance list`, `advance settle --id`, `advance writeoff --id` |
| Health check | `ledger doctor --json`, `ledger audit --json` |
| Browser UI | `ledger open` |

`list` caps at 20 rows and `recent` at 5 unless `--limit` is given; check
`truncated` in `--json` output before concluding something is absent.

## References

Load only what the request needs.

| Situation | Read |
|---|---|
| Field inference, reimbursement/advance/transfer wording | [references/bookkeeping-rules.md](references/bookkeeping-rules.md) |
| Adding, correcting, deleting, accounts, budgets | [references/record.md](references/record.md) |
| Totals, comparisons, monthly bill, export, backup | [references/present.md](references/present.md) |
| Anything proactive: review queue, repairs, advising | [references/proactive-review.md](references/proactive-review.md) |
| Habits, usual amounts, merchant preferences | [references/habits.md](references/habits.md) |
| Screenshot or several visible rows | [references/image-batch-import.md](references/image-batch-import.md) |
| Official WeChat/Alipay/bank statement, duplicates | [references/reconcile-imports.md](references/reconcile-imports.md) |
| Browser UI or HTML rendering | [references/html-report.md](references/html-report.md) |
| Schema, category hierarchy, manual JSON edits | [references/ledger-schema.md](references/ledger-schema.md) |

## Where data lives

`LAZY_LEDGER_FILE`, else `$LAZY_LEDGER_HOME/ledgers/default/ledger.json`, else
`./data/ledgers/default/ledger.json`. Never write runtime data into the skill
package. Each ledger keeps its own `backups/` beside it; the habit portrait and
bill HTML are named after the ledger stem, so ledgers cannot collide.

Collections: `transactions`, `accounts`, `budgets`, `categories`, `habits`,
`bills`, `review_queue`. Preserve unknown fields and collections.

The local app binds `127.0.0.1` only, normally port `8765`.

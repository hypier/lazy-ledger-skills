---
name: lazy-ledger
description: Maintain a local JSON personal ledger. Use when recording or correcting transactions, reconciling WeChat/Alipay/bank/credit-card statements without duplicates, managing accounts/categories/budgets, summarizing or generating monthly bills, or opening the localhost ledger UI.
metadata:
  version: 1.1.0
---

# Lazy Ledger

Operate the user's local ledger with minimal questions and source-faithful accounting. Chat is the default surface; the localhost app is for browsing and editing.

## AI-led entry

Treat each message as an intent and evidence problem before choosing a command. Extract the user's intended event, amount meaning, date, type, account, merchant, category, and relationships such as refund, reimbursement, or money owed. Use history and the habit portrait as evidence, clearly separating a fact in the message, a stable user rule, and a tentative inference.

- Pass understood fields explicitly to the CLI when possible; use `parse` when the meaning or amount is uncertain.
- Ask one focused question when competing interpretations would change balances or totals. Do not write a guessed multi-transaction interpretation.
- A correction applies to the current target only unless the user explicitly asks to remember it. Treat a one-off category as temporary; save a merchant rule or amount habit only on explicit instruction or a clearly established stable pattern.
- Adapt the response to the question: use concise confirmation for a write, an explanation for “why”, and a data-backed comparison for analysis. The CLI remains the source of arithmetic and persistence.
- When uncertainty differs by field, keep the transaction-level `confidence` compatible and describe the uncertain fields in `note`; ask only about fields that change the financial meaning.
- For "that last transaction" or "the previous one" corrections, use the current turn's returned ID first. If several rows were just written, show candidates before updating; update only the named field.

## Proactive review loop

AI bookkeeping is also an ongoing conversation about data quality. After a write, import, correction, or query that exposes relevant rows, look for actionable issues with `audit` or `doctor` and explain only findings that matter to the user's question. Typical findings include likely duplicates, missing accounts, implausible amounts or dates, low-confidence classifications, unmatched refunds, and expenses that look like transfers or reimbursements.

Analyze findings before involving the user. When source references, timing, amount, merchant, and transaction type make the intended result clear, apply a reversible field update or relation automatically and report it. Only surface findings where multiple reasonable interpretations remain, the repair changes financial meaning, or deletion/merging is proposed. Group unresolved findings that have the same repair type and evidence pattern into one confirmation question. Within that group, show each affected row and one proposed action. Handle groups sequentially: ask one confirmation question, wait for the answer, apply only that group, rerun the relevant check, and then present the next highest-priority group.

- `doctor` detects structural and referential problems; `audit` detects duplicate and source-reference problems. Their results are evidence for AI analysis, not a requirement to ask the user about every row.
- A low-confidence or unusual row is a review suggestion, not proof of an error. For social/gift spending, when the habit portrait shows it is rare, check first whether the row belongs under groceries, snacks, or ordinary spending. Preserve the original evidence and uncertainty until the user confirms.
- For consecutive transactions that are close in time and similar in merchant, payee, or description, treat them as a transaction group and infer a shared purpose. When there is no clear contrary evidence, classify by the group's highest-probability purpose and note "inferred from nearby similar transactions". Ask only when purposes conflict within the group or would change income/expense treatment.
- For groups that remain ambiguous, rank candidate purposes by evidence with confidence (for example "groceries 70% / snacks 25% / social 5%"), show the group, candidates, and proposed choice in one confirmation; after confirmation, apply the fix uniformly and record the basis used.
- If the user asks only for a total, do not derail the answer with every warning. Mention a concise material warning and offer the repair path when it could change the total.
- Keep a review queue internally, but expose only its highest-priority unresolved item. A clean result or a user decline advances to the next item.
- Before asking about an unlinked refund, search a nearby date window for the same merchant and amount, including normalized merchant names and source descriptions. Present reliable candidates first; ask only when no candidate or several materially different candidates remain.

## Runtime

- CLI entry: `scripts/ledger` in this skill. It resolves the skill directory itself and injects `--ledger`, so prefer it over calling the Python files directly.
- Direct scripts, when the wrapper is unavailable: `scripts/ledger_tool.py`, `scripts/bill_screenshot.py`, `scripts/ledger_audit.py`.
- Screenshot OCR: `scripts/bill_screenshot.py` (also reachable as `ledger shot`).
- Cross-source audit: `scripts/ledger_audit.py` (also reachable as `ledger audit`).
- Ledger: `LAZY_LEDGER_FILE` if set; otherwise `$LAZY_LEDGER_HOME/ledgers/default/ledger.json` when set; otherwise `./data/ledgers/default/ledger.json`. Runtime data belongs in `data/`, never in the skill root. Each ledger instance has separate `backups/`, `reports/`, and `memory/` directories.
- Habit portrait: `{ledger-stem}-memory.md` beside the ledger.
- Local app: `127.0.0.1` only, normally port `8765`.

Set `LEDGER_SKILL_DIR` to the absolute directory containing this `SKILL.md` before running reference commands. Do not assume the user's working directory is the installed skill directory.

The ledger is a JSON document store (`transactions`, `accounts`, `budgets`, `categories`, `habits`, `bills`), not SQL. Preserve unknown fields. Do not put runtime data inside an installed skill package unless the user is intentionally operating in this skill's source repository.

## Quick Path

These cover the majority of requests. Run them directly without loading a reference.

| Request | Command |
|---|---|
| Record one or several clear items | `ledger add --text "yesterday wechat starbucks 38"` |
| This month, versus last month | `ledger show --compare` |
| Spending over a named period | `ledger show --range last-month\|this-quarter\|this-year --compare` |
| Find a row before correcting it | `ledger find --text "yesterday starbucks"` |
| Correct one field by ID | `ledger update --id tx_20260707_ab12cd34 --category coffee-tea` |
| Delete by ID | `ledger delete --id tx_20260707_ab12cd34 --yes` |
| Accounts and balances | `ledger account list --json` |
| Budget progress | `ledger budget list` |
| Data health and duplicate check | `ledger audit --json` |
| List rows, count rows, check a date | `ledger list --limit 500 --json` |
| Open the localhost ledger UI | `ledger open` |

All commands assume `LEDGER_SKILL_DIR` is set; prefix with `"$LEDGER_SKILL_DIR/scripts/"`. Pass `--ledger /path/to.json` to target a different file.

`list` returns at most 20 rows and `recent` at most 5 unless `--limit` is given. `list --json` returns `{transactions, count, total, truncated, period}` — check `truncated` before concluding that a date, merchant, or category is absent, and re-run with a larger `--limit` when it is true.

Stop and load a reference when the request involves an image, an official statement file, a cross-source duplicate decision, a monthly bill letter, or the localhost app.

## Route The Request

Read only the references needed for the current request.

| Intent | Read | Primary command |
|---|---|---|
| Add, parse, correct, delete, account, budget | [references/record.md](references/record.md) | `add`, `parse`, `update`, `delete`, `account`, `budget` |
| Field inference and accounting semantics | [references/bookkeeping-rules.md](references/bookkeeping-rules.md) | Used by record/import decisions |
| Habit, usual amount, merchant preference | [references/habits.md](references/habits.md) | `habit`, `prefer` |
| Long screenshot or several visible rows | [references/image-batch-import.md](references/image-batch-import.md) | `shot prepare`, then `import-tsv` after confirmation |
| Official WeChat/Alipay/bank/credit-card file, duplicate audit, account reconciliation | [references/reconcile-imports.md](references/reconcile-imports.md) | `audit`, source parser, preview import |
| Totals, comparison, export, backup, monthly bill | [references/present.md](references/present.md) | `show`, `summary`, `export`, `backup`, `bill` |
| Open/edit in browser or render HTML | [references/html-report.md](references/html-report.md) | `serve`, `render` |
| Schema, category hierarchy, manual JSON changes | [references/ledger-schema.md](references/ledger-schema.md) | `doctor --json` |
| Proactive problem discovery and confirmed repair | [references/proactive-review.md](references/proactive-review.md) | `doctor`, `audit`, review queues |

## Accounting Invariants

- Store positive `amount`; `type` determines direction. Own-account movement is `transfer` and is excluded from income/expense.
- Keep the actual funding account separate from the payment channel. A WeChat payment funded by a named bank card belongs to that bank account with `payment_channel: wechat`.
- Use specific bank and credit-card accounts when the source identifies them; do not collapse them into a generic bank-card account.
- Preserve authoritative source references and statement attachments. Never delete or merge rows only because date and amount match.
- A full refund remains two rows: the original `expense` and a `refund`.
- Transactions store the most specific second-level category; reports roll it into its parent.
- For bulk imports, validate source totals, reconcile against the current ledger, test on a copy, back up, apply once, then verify IDs, balances, `doctor`, summaries, and idempotency where supported.
- Before writing an imported row, perform a semantic classification pass using its merchant/description, account and payment channel, plus the current habit portrait and merchant preferences. Assign the most specific configured category; keep the catch-all category only when evidence is insufficient. Group only materially ambiguous type, account, or category decisions for user confirmation. Verify this pass on the temporary copy before applying the real import.

## Interaction Defaults

- A clear single transaction is written immediately. Ask only when amount, type, or correction target is materially ambiguous.
- A payment-history screenshot always requires a visible confirmation table before any write, even when the initial request says "import".
- Before recording, use the habit portrait only when current; never guess an amount outside its stable-amount section.
- After a write, reply in the user's language with the affected rows, important assumptions, and the relevant period total. `add` already returns the affected month totals; reuse them instead of running a second summary command. Do not dump raw ledger JSON unless requested.

## Safety

- Never invent unreadable transactions, merchant suffixes, account numbers, or exact totals.
- Do not store bank logins, full card numbers, payment credentials, or secrets.
- Preserve uncertainty in `note` and `confidence`.
- Do not overwrite an existing ledger or silently remove suspected duplicates.
- Keep financial commentary descriptive unless the user explicitly asks for advice.

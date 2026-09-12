---
name: lazy-ledger
description: Maintain a local JSON personal ledger. This skill should be used when recording or correcting transactions (记一笔, 记账, 记一下), reconciling WeChat/Alipay/bank/credit-card statements without duplicates (导入账单, 对账, 查重), managing accounts/categories/budgets (账户, 预算, 分类), summarizing or generating monthly bills (本月花了多少, 月账单, 报表), or opening the localhost ledger UI (打开账本).
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
- For “刚才那笔/上一笔” corrections, use the current turn's returned ID first. If several rows were just written, show candidates before updating; update only the named field.

## Proactive review loop

AI bookkeeping is also an ongoing conversation about data quality. After a write, import, correction, or query that exposes relevant rows, look for actionable issues with `audit` or `doctor` and explain only findings that matter to the user's question. Typical findings include likely duplicates, missing accounts, implausible amounts or dates, low-confidence classifications, unmatched refunds, and expenses that look like transfers or reimbursements.

For each finding, state the affected rows, why it was noticed, and the proposed repair in plain Chinese. Ask for confirmation before changing, merging, deleting, or relabeling an existing row. Apply a repair only after confirmation, then rerun the relevant check and report the result. Several independent safe field repairs may be grouped into one confirmation table; never bundle an ambiguous destructive action with them.

- `doctor` detects structural and referential problems; `audit` detects duplicate and source-reference problems. Neither result is permission to mutate data.
- A low-confidence or unusual row is a review suggestion, not proof of an error. Preserve the original evidence and uncertainty until the user confirms.
- If the user asks only for a total, do not derail the answer with every warning. Mention a concise material warning and offer the repair path when it could change the total.

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
| Record one or several clear items | `ledger add --text "昨天微信 星巴克 38"` |
| This month, versus last month | `ledger show --compare` |
| Spending over a named period | `ledger show --range last-month\|this-quarter\|this-year --compare` |
| Find a row before correcting it | `ledger find --text "昨天星巴克"` |
| Correct one field by ID | `ledger update --id tx_20260707_ab12cd34 --category 咖啡茶饮` |
| Delete by ID | `ledger delete --id tx_20260707_ab12cd34 --yes` |
| Accounts and balances | `ledger account list --json` |
| Budget progress | `ledger budget list` |
| Data health and duplicate check | `ledger audit --json` |
| List rows, count rows, check a date | `ledger list --limit 500 --json` |

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
- Use specific bank and credit-card accounts when the source identifies them; do not collapse them into a generic `银行卡` account.
- Preserve authoritative source references and statement attachments. Never delete or merge rows only because date and amount match.
- A full refund remains two rows: the original `expense` and a `refund`.
- Transactions store the most specific second-level category; reports roll it into its parent.
- For bulk imports, validate source totals, reconcile against the current ledger, test on a copy, back up, apply once, then verify IDs, balances, `doctor`, summaries, and idempotency where supported.

## Interaction Defaults

- A clear single transaction is written immediately. Ask only when amount, type, or correction target is materially ambiguous.
- A payment-history screenshot always requires a visible confirmation table before any write, even when the initial request says “导入”.
- Before recording, use the habit portrait only when current; never guess an amount outside its stable-amount section.
- After a write, reply in Chinese with the affected rows, important assumptions, and the relevant period total. `add` already returns the affected month totals; reuse them instead of running a second summary command. Do not dump raw ledger JSON unless requested.

## Safety

- Never invent unreadable transactions, merchant suffixes, account numbers, or exact totals.
- Do not store bank logins, full card numbers, payment credentials, or secrets.
- Preserve uncertainty in `note` and `confidence`.
- Do not overwrite an existing ledger or silently remove suspected duplicates.
- Keep financial commentary descriptive unless the user explicitly asks for advice.

---
name: lazy-ledger
description: Maintain a local JSON personal ledger. Use for recording or correcting transactions, reconciling WeChat/Alipay/bank/credit-card statements without duplicates, managing accounts/categories/budgets, summarizing or generating monthly bills, and opening the localhost ledger UI.
---

# Lazy Ledger

Operate the user's local ledger with minimal questions and source-faithful accounting. Chat is the default surface; the localhost app is for browsing and editing.

## Runtime

- CLI: `scripts/ledger_tool.py` in this skill.
- Screenshot OCR: `scripts/bill_screenshot.py`.
- Cross-source audit: `scripts/ledger_audit.py`.
- Ledger: the user-named file, otherwise `./lazy-ledger.json` in the working directory.
- Habit portrait: `{ledger-stem}-memory.md` beside the ledger.
- Local app: `127.0.0.1` only, normally port `8765`.

Set `LEDGER_SKILL_DIR` to the absolute directory containing this `SKILL.md` before running reference commands. Do not assume the user's working directory is the installed skill directory.

The ledger is a JSON document store (`transactions`, `accounts`, `budgets`, `categories`, `habits`, `bills`), not SQL. Preserve unknown fields. Do not put runtime data inside an installed skill package unless the user is intentionally operating in this skill's source repository.

## Route The Request

Read only the references needed for the current request.

| Intent | Read | Primary command |
|---|---|---|
| Add, parse, correct, delete, account, budget | [references/record.md](references/record.md) | `add`, `parse`, `update`, `delete`, `account`, `budget` |
| Field inference and accounting semantics | [references/bookkeeping-rules.md](references/bookkeeping-rules.md) | Used by record/import decisions |
| Habit, usual amount, merchant preference | [references/habits.md](references/habits.md) | `habit`, `prefer` |
| Long screenshot or several visible rows | [references/image-batch-import.md](references/image-batch-import.md) | `bill_screenshot.py prepare`, then `import-tsv` after confirmation |
| Official WeChat/Alipay/bank/credit-card file, duplicate audit, account reconciliation | [references/reconcile-imports.md](references/reconcile-imports.md) | `ledger_audit.py`, source parser, preview import |
| Totals, comparison, export, backup, monthly bill | [references/present.md](references/present.md) | `show`, `summary`, `export`, `backup`, `bill` |
| Open/edit in browser or render HTML | [references/html-report.md](references/html-report.md) | `serve`, `render` |
| Schema, category hierarchy, manual JSON changes | [references/ledger-schema.md](references/ledger-schema.md) | `doctor --json` |

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
- After a write, reply in Chinese with the affected rows, important assumptions, and the relevant period total. Do not dump raw ledger JSON unless requested.

## Safety

- Never invent unreadable transactions, merchant suffixes, account numbers, or exact totals.
- Do not store bank logins, full card numbers, payment credentials, or secrets.
- Preserve uncertainty in `note` and `confidence`.
- Do not overwrite an existing ledger or silently remove suspected duplicates.
- Keep financial commentary descriptive unless the user explicitly asks for advice.

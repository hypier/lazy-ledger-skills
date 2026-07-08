---
name: lazy-ledger
description: Personal lazy bookkeeping assistant for recording expenses, income, refunds, transfers, and notes into a local JSON ledger, summarizing spending, finding transactions, cleaning/importing ledger data, and generating a self-contained HTML dashboard. Use when the user asks to 记账, 懒人记账, record a purchase, add an expense, summarize spending, view accounting data, make a ledger report, convert receipts or payment text into transactions, or create an HTML page to inspect ledger data.
---

# Lazy Ledger

## Overview

Use this skill to maintain a lightweight local personal ledger with minimal user effort. Prefer doing the bookkeeping work directly: infer sensible defaults, write structured transactions, summarize results, and render an HTML dashboard when the user wants to see the data.

Default ledger path: use the user's requested file when provided; otherwise use `./lazy-ledger.json` in the current working directory.

## Workflow

1. Identify intent: add transaction, revise/delete transaction, summarize, search, import/clean, or render HTML.
2. Read `references/bookkeeping-rules.md` for field inference and confirmation rules when parsing user text, screenshots, receipts, or messy notes.
3. Read `references/ledger-schema.md` before changing ledger JSON by hand or mapping external data.
4. Use `scripts/ledger_tool.py` for deterministic parsing, ledger writes, summaries, validation, and HTML rendering.
5. Read `references/html-report.md` when the user asks to customize or inspect the generated dashboard.
6. When generating HTML, use `assets/ledger-viewer-template.html` via the script unless the user asks for a custom page.
7. Report what changed: ledger path, transaction count affected, generated report path, and any assumptions.

## Add Transactions

For clear user input like `昨天星巴克 38` or `午饭 26.5 餐饮`, parse the fields and add the entry without asking a follow-up. Prefer `add --text` for casual text so the deterministic parser, duplicate check, and category defaults run together. Ask only when the amount is missing, there are multiple plausible amounts, or the transaction type would materially change totals.

Use defaults:

- `type`: `expense`
- `currency`: ledger default, usually `CNY`
- `occurred_at`: today if absent; preserve user-provided relative dates
- `category`: infer from merchant/note; otherwise `其他`
- `source`: `text`, `image`, `voice`, `manual`, or `import`

Example command:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py add \
  --ledger ./lazy-ledger.json \
  --text "昨天星巴克 38"
```

Preview the parsed fields without writing when the input is slightly ambiguous:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py parse \
  --ledger ./lazy-ledger.json \
  --text "昨天星巴克 38"
```

`add` refuses likely duplicates by default. If the user confirms it is a separate purchase, rerun with `--allow-duplicate`.

## Revise Transactions

Use `list` to find candidate transaction ids, then `update` or `delete` for corrections. If the user's correction clearly identifies one recent transaction, apply it and report the id. If several entries match, show concise candidates and ask which one.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py recent \
  --ledger ./lazy-ledger.json \
  --limit 5

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py list \
  --ledger ./lazy-ledger.json \
  --query 星巴克

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py update \
  --ledger ./lazy-ledger.json \
  --id tx_20260707_ab12cd34 \
  --category 餐饮
```

## View Data

For "看一下数据", "生成页面", "做个报表", or similar requests, render the dashboard:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py render \
  --ledger ./lazy-ledger.json \
  --output ./lazy-ledger-report.html
```

The generated HTML is self-contained and can be opened directly in a browser. It includes month filtering, search, category/type filters, summary totals, category bars, and transaction rows.

Run a quick ledger health check when importing, cleaning, or suspecting duplicate/invalid data:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py doctor \
  --ledger ./lazy-ledger.json \
  --json
```

## Safety Rules

- Never invent exact transactions when the user is asking for factual bookkeeping. If the amount is unknown, ask.
- Keep amounts numeric and positive; use `type` to express expense/income/refund/transfer.
- Do not overwrite an existing ledger without preserving its transactions.
- Do not store secrets, bank login data, card numbers, or full payment credentials.
- For screenshots or receipts, preserve uncertainty in `note` or `confidence` when fields are inferred.
- When the user asks for financial advice, limit the answer to descriptive spending analysis unless they explicitly request broader guidance.

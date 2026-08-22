---
name: lazy-ledger
description: Personal lazy bookkeeping assistant. Records expenses, income, refunds, and transfers into a local JSON document ledger from casual Chinese text, receipts, screenshots, or pasted payment history; learns a usage portrait from the ledger so later records can default account, category, and stable amounts without asking; tracks accounts and monthly budgets; summarizes spending in chat with year/quarter views, recurring bills, and budget pace; serves a localhost page with tabs for recording, charts, a monthly AI canvas bill, and accounts; and can also generate a self-contained HTML snapshot. Use when the user asks to 记账, 懒人记账, 记一笔, 这个月花了多少, 今年花了多少, 看账, 对账, 预算, 转账, 账户余额, 打开页面, 改账, 月度账单, 删掉刚才那笔, 记账习惯, 备份, record a purchase, import WeChat/Alipay history, summarize spending, or inspect ledger data.
---

# Lazy Ledger

Maintain a local personal ledger with minimal friction. Infer fields, write structured transactions, answer in Chinese with a readable summary, and open the localhost page when the user wants to look at or edit the data.

## Paths

- Tool: `scripts/ledger_tool.py` next to this SKILL.md. Typical invocation: `python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py`
- Screenshot prepare: `python3 /Users/barry/.agents/skills/lazy-ledger/scripts/bill_screenshot.py prepare --image …` (table only; no ledger write)
- Ledger: `./lazy-ledger.json` in the user's working directory unless they name another file
- Habit memory: `./lazy-ledger-memory.md` next to the ledger (periodic portrait for the agent to Read)
- Dashboard: `./lazy-ledger-report.html` unless they name another output
- Monthly canvas bill: `./lazy-ledger-bill-YYYY-MM.html` next to the ledger (one HTML file per month)
- Live page: `python3 ... serve --ledger ./lazy-ledger.json` on 127.0.0.1 only

Never write the ledger into the skill directory. The ledger file is a JSON document database (`store: lazy-ledger-docs`) with collections: `transactions`, `accounts`, `budgets`, `categories`, `habits`, `bills`.

## Route intent

| User intent | Read | Command |
|---|---|---|
| Record one or many items | [references/record.md](references/record.md) | `add --text` |
| "午饭一般 16 / 记账习惯 / 我平时怎么记" | [references/record.md](references/record.md) | Read `lazy-ledger-memory.md`; `habit memory` if missing/stale |
| Unclear amount / several totals | [references/record.md](references/record.md) | `parse --text` first |
| Correct or delete | [references/record.md](references/record.md) | `find` then `update` / `delete --yes` |
| Screenshot, receipt, payment-history paste | [references/record.md](references/record.md), [references/image-batch-import.md](references/image-batch-import.md) | Long bill: `bill_screenshot.py prepare` → table → wait → `import-tsv`. One receipt: `add --source image` |
| "花了多少 / 汇总 / 对账 / 预算" | [references/present.md](references/present.md) | `show --compare` |
| "今年花了多少 / 这季度 / 订阅还没记" | [references/present.md](references/present.md) | `show --range this-year` or `--range this-quarter --compare` |
| 账户 / 转账 / 余额 | [references/record.md](references/record.md) | `account list` / `add --text` with 转到 |
| 备份账本 | [references/present.md](references/present.md) | `backup` |
| 设预算 / 这个月还能花多少 | [references/present.md](references/present.md) | `budget set` then `show` |
| "看数据 / 打开报表 / 改账 / 本地页面" | [references/present.md](references/present.md), [references/html-report.md](references/html-report.md) | `serve` then URL `#charts` or `#ledger` |
| "月度账单 / 出一份账单" | [references/present.md](references/present.md) | `bill show --json`, write the letter, `bill save` (writes a Canvas HTML file) |
| Schema or manual JSON edits | [references/ledger-schema.md](references/ledger-schema.md) | `doctor --json` |
| Field inference details | [references/bookkeeping-rules.md](references/bookkeeping-rules.md) | — |

Do the obvious write when amount and meaning are clear. Do not ask the user to fill a form. Exception: a payment-history screenshot is always table-then-confirm, never a silent `import-tsv`.

## Record

Clear input such as `昨天星巴克 38` or a multi-line paste → `add --text` immediately.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py add \
  --ledger ./lazy-ledger.json \
  --text "昨天星巴克 38"
```

`add` prints `{added, count, month}`. Reply with the recorded line plus this month's expense total from `month.totals.expense`. If `month.budgets` has a matching category, mention remaining.

Ask only when amount is missing (and no stable usual amount exists for that short phrase), several amounts could be the total, or type (expense/income/refund/transfer) would change totals.

Before recording, Read `./lazy-ledger-memory.md` (same folder as the ledger). If it is missing, or its `tx_count` lags the ledger by 10+ rows, or it is older than 7 days, run `habit memory --ledger ./lazy-ledger.json` then Read the file again. Apply 默认 / 稳定金额 silently. Do not show shortcut chips, do not ask to 存为常用, and do not guess amounts under 不要猜金额. After a large import or when the user asks to 更新习惯, run `habit memory` (optionally `--summary` with a 2–4 sentence portrait).

Read [references/record.md](references/record.md) before handling screenshots, stacked WeChat/Alipay pastes, merchant habits, or corrections. For a long 账单截图, follow [references/image-batch-import.md](references/image-batch-import.md): run `bill_screenshot.py prepare`, show the table, and **do not import until the user confirms**.

## Present

Always answer in Chinese with a short human summary. Do not dump raw JSON unless the user asks.

For spending questions:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py show \
  --ledger ./lazy-ledger.json \
  --compare
```

`show` defaults to this month. If this month is empty, say so and rerun with `--month YYYY-MM` or omit the default by using `summary`.

To look at or edit data in a browser, start the local page in the background (do not block the session on it):

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py serve \
  --ledger ./lazy-ledger.json
```

The command prints `{"url":"http://127.0.0.1:8765/","ledger":"..."}`. Give the user that URL. Append `#charts` for the chart report, `#bill` for the monthly letter, `#book` for accounts. A canvas bill is also at `/bill/YYYY-MM`. Bind only to localhost. Use `render` for a full snapshot; `bill save` / `bill render` for one month's canvas page.

Then give the file path or URL. Read [references/present.md](references/present.md) for chat templates and when to add the dashboard.

## Safety

- Never invent exact transactions. If the amount is unknown, ask. Do not guess rows from an unreadable thumbnail.
- Keep amounts positive; use `type` for direction.
- Do not overwrite an existing ledger's transactions.
- Do not store secrets, bank logins, card numbers, or payment credentials.
- Preserve uncertainty in `note` / `confidence` for screenshots and imports.
- Financial advice stays descriptive unless the user explicitly asks for broader guidance.

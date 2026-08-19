# Present

How to show ledger data to the user. Chat is the default. The localhost page is for looking and editing. Static HTML is a snapshot export.

## Which surface

| Ask | Do |
|---|---|
| 记好了吗 / after any write | One-line confirmation in chat. No HTML. |
| 这个月花了多少 / 汇总 / 对账 | `show --compare`, paste the markdown. Offer HTML if they want to drill in. |
| 今年 / 这季度花了多少 | `show --range this-year` or `--range this-quarter --compare` |
| 还能花多少 / 预算 | `show --compare` or `budget list`. Mention remaining/overspend and any 预计超支. |
| 账户余额 / 各账户还剩多少 | `account list --json`, or `show` which already includes 账户. |
| 最近花在哪 / 哪个商户最多 | `show --compare` plus `list --limit 10` if they want rows. |
| 看一下数据 / 打开报表 / 本地页面 / 改账 | `serve` in the background, give the localhost URL. Add `#charts` for 报表, `#bill` for 月度账单. |
| 出一份月度账单 / 8月账单 | `bill show --month YYYY-MM --json`. Write the letter from `facts` + `brief`. Then `bill save --body`. Offer the page at `#bill`. |
| 我的记账习惯 / 我平时怎么记 | Read `lazy-ledger-memory.md`. Reply with the 画像. `habit memory` if the file is missing or stale. |
| 导出静态页面 | `render`, then give the file path. |
| More than 15 matching rows | Short totals in chat, then `serve` or `render`. |
| Export / 导出表格 | `export --format csv --output ./lazy-ledger.csv` |

Never paste the raw ledger JSON into chat unless the user asks for the file contents.

## Chat after add

Use the `added` array and `month` object from `add`:

```
记好了。
2026-08-18 · 支出 · 咖啡 · 星巴克 · ¥38.00（微信零钱）
本月支出 ¥1,280.00 · 共 16 笔
餐饮预算剩余 ¥720.00
```

Several items: list each line, then the month totals once. Mention inferred fields only when they matter (category `其他`, paid vs 原价, date defaulted). If a stable habit filled the amount, say so in one clause (`午饭按平时的 ¥16`). Do not list other 常用 items.

If `add` exits 2 with `likely_duplicate`, ask whether to keep both. Do not retry with `--allow-duplicate` until they confirm.

## Chat after a spending question

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py show \
  --ledger ./lazy-ledger.json \
  --compare
```

`show` defaults to this month. Pass `--month YYYY-MM`, `--range last-month|last7|last30|this-quarter|this-year`, or `--start / --end` when the user names a period.

If this month is empty, tell them and rerun with the latest month mentioned by the command, or with `--range last30`.

Paste the markdown as-is. The `### 观察` bullets already cover the useful takeaways (pace, recurring, vs previous). Do not add extra moralizing. If `### 周期账` lists `这月还没记`, mention it in one sentence.

## Chat after search / recent

Keep it compact:

```
最近 5 笔
- 08-18 星巴克 餐饮 ¥38.00
- 08-17 地铁 交通 ¥4.00
```

Use ids only when the user needs to correct or delete.

## Live page

Prefer this when the user wants to look at or change the ledger in a browser.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py serve \
  --ledger ./lazy-ledger.json
```

Run it in the background. It prints `{"url":"http://127.0.0.1:8765/",...}`. Only localhost is allowed. The page talks to the JSON document store: add from a text box, edit/delete rows, set budgets, see balances.

`--open` launches the system browser. `--port` picks another local port if 8765 is taken.

## Snapshot dashboard

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py render \
  --ledger ./lazy-ledger.json \
  --output ./lazy-ledger-report.html
```

The page is self-contained: no network, no libraries. It includes period chips, month/type/category/method/account filters, vs-previous deltas, account balances, budget remaining, a spend calendar, category and merchant bars, daily spend, a review queue, transaction table (cards on small screens), and CSV export.

Tell the user the absolute path and that they can open the file in a browser. If they want a different look, copy the template and edit the copy; do not edit `assets/ledger-viewer-template.html` unless they asked to change the skill.

Details: [html-report.md](html-report.md).

## Summary JSON for follow-up math

When you need numbers rather than prose:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py summary \
  --ledger ./lazy-ledger.json \
  --range this-month \
  --compare \
  --json
```

Useful fields: `totals`, `by_category`, `by_merchant`, `by_day`, `by_method`, `by_month`, `weekday`, `daily_average`, `comparison`, `accounts`, `budgets`, `needs_review`, `outliers`, `insights`, `recurring`, `pace`, `month_trend`, `pending_reimbursement`, `unmatched_refunds`.

## Backup

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py backup \
  --ledger ./lazy-ledger.json
```

Writes `lazy-ledger-YYYYMMDD.json` next to the ledger. Pass `--output` to pick another path.

## Export

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py export \
  --ledger ./lazy-ledger.json \
  --range this-month \
  --format csv \
  --output ./lazy-ledger.csv
```

`--format json` writes the filtered transaction list, not a replacement ledger file.

## Monthly bill

Two layers:

1. **Facts** — the same numbers as `show`, plus habit summary. Deterministic.
2. **Letter** — written by the agent. Flexible prose: how they spent, what changed, one thing to watch. Not a table dump.

When the user asks for 月度账单:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py bill show \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --json
```

Use `facts` only. Follow `brief`. 400–800 Chinese characters. Do not moralize. Do not invent amounts. Then save:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py bill save \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --title "八月还是把钱花在吃上" \
  --body "……写好的正文……"
```

Reply in chat with the letter. If they have the live page open, point them to `#bill`.

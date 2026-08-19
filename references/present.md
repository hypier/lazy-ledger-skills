# Present

How to show ledger data to the user. Chat is the default. The localhost page is for looking and editing. Static HTML is a snapshot export.

## Which surface

| Ask | Do |
|---|---|
| 记好了吗 / after any write | One-line confirmation in chat. No HTML. |
| 这个月花了多少 / 汇总 / 对账 | `show --compare`, paste the markdown. Offer HTML if they want to drill in. |
| 还能花多少 / 预算 | `show --compare` or `budget list`. Mention remaining/overspend. |
| 账户余额 / 各账户还剩多少 | `account list --json`, or `show` which already includes 账户. |
| 最近花在哪 / 哪个商户最多 | `show --compare` plus `list --limit 10` if they want rows. |
| 看一下数据 / 打开报表 / 本地页面 / 改账 | `serve` in the background, give the localhost URL. |
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

Several items: list each line, then the month totals once. Mention inferred fields only when they matter (category `其他`, paid vs 原价, date defaulted).

If `add` exits 2 with `likely_duplicate`, ask whether to keep both. Do not retry with `--allow-duplicate` until they confirm.

## Chat after a spending question

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py show \
  --ledger ./lazy-ledger.json \
  --compare
```

`show` defaults to this month. Pass `--month YYYY-MM`, `--range last-month|last7|last30`, or `--start / --end` when the user names a period.

If this month is empty, tell them and rerun with the latest month mentioned by the command, or with `--range last30`.

Paste the markdown as-is, then add at most two sentences of observation (biggest category, vs last month, one outlier). Do not moralize.

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

Useful fields: `totals`, `by_category`, `by_merchant`, `by_day`, `by_method`, `daily_average`, `comparison`, `accounts`, `budgets`, `needs_review`, `outliers`.

## Export

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py export \
  --ledger ./lazy-ledger.json \
  --range this-month \
  --format csv \
  --output ./lazy-ledger.csv
```

`--format json` writes the filtered transaction list, not a replacement ledger file.

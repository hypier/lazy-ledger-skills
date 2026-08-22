# HTML Report

Two surfaces:

1. Live local page (`serve`) — view and edit against the JSON document store. Default when the user wants to 看账 or 改账.
2. Static snapshot (`render`) — self-contained HTML file, no server.

## Live page

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py serve \
  --ledger ./lazy-ledger.json
```

- Binds 127.0.0.1 only
- Uses `assets/ledger-app.html`
- Reads and writes the same ledger file as the CLI
- Four tabs: 记账 / 报表 / 月度账单 / 账本 (`#ledger` `#charts` `#bill` `#book`)
- 报表: category donut, merchant bars, daily/monthly line, weekday and method charts (SVG, no libraries)
- 月度账单: month facts plus the saved AI letter; **打开画布账单** opens `/bill/YYYY-MM` (Canvas, one local HTML file per month)
- 账本: add/edit accounts (opening, default) and budgets
- No shortcut chips; habits stay in the background

Start it in the background and give the printed URL.

## Static snapshot

Use `assets/ledger-viewer-template.html` via `scripts/ledger_tool.py render`.

The report is local inspection only:

- No network calls
- No external libraries
- Ledger JSON is embedded in the file
- Open it directly in a browser

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py render \
  --ledger ./lazy-ledger.json \
  --output ./lazy-ledger-report.html
```

The template includes:

- Period chips: 本月 / 上月 / 本季 / 今年 / 近7天 / 近30天 / 全部
- Month, type, category, method, account, and search filters with visible labels
- Expense vs previous period, income, net, daily average, review count
- Insight bullets: pace, recurring due, weekend share, vs previous
- Account balances (opening + all history)
- Budget bars for the selected month
- Recurring charges and a 6-month spend trend
- Month spend calendar (heatmap)
- Category bars with amounts
- Top merchants
- Daily spend list
- Needs-review queue (low confidence or category `其他`)
- Transaction table on desktop, stacked cards on small screens
- Type badges (not color alone)
- CSV export of the current filter

If the user wants a custom look, copy the template or generated file and edit the copy, not the bundled asset.

## Monthly canvas bill

Each month is a separate self-contained HTML file next to the ledger:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py bill save \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --title "八月还是把钱花在吃上" \
  --body "……"
```

Default path: `{ledger-stem}-bill-YYYY-MM.html` (for `./lazy-ledger.json` → `./lazy-ledger-bill-2026-08.html`).

- Template: `assets/ledger-bill-canvas.html`
- Draws on `<canvas>` (paper-ledger look); no network, no libraries
- Also served live at `http://127.0.0.1:8765/bill/2026-08`
- Export PNG / print from the page
- `bill render --month YYYY-MM` regenerates the file from current facts + saved letter

Do not put absolute file paths into `lazy-ledger.json`. Generated `*-bill-YYYY-MM.html` files are gitignored.

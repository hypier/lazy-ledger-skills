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
- Add from casual text, edit/delete rows, set/delete budgets

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

- Period chips: 本月 / 上月 / 近7天 / 近30天 / 全部
- Month, type, category, method, account, and search filters with visible labels
- Expense vs previous period, income, net, daily average, review count
- Account balances (opening + all history)
- Budget bars for the selected month
- Month spend calendar (heatmap)
- Category bars with amounts
- Top merchants
- Daily spend list
- Needs-review queue (low confidence or category `其他`)
- Transaction table on desktop, stacked cards on small screens
- Type badges (not color alone)
- CSV export of the current filter

If the user wants a custom look, copy the template or generated file and edit the copy, not the bundled asset.

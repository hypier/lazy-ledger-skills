# HTML Report

Use `assets/ledger-viewer-template.html` with `scripts/ledger_tool.py render` to create a self-contained dashboard.

The report is for local inspection:

- No network calls.
- No external libraries.
- Ledger data is embedded directly into the generated HTML.
- The file can be opened directly in a browser.

Default render command:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py render \
  --ledger ./lazy-ledger.json \
  --output ./lazy-ledger-report.html
```

The template includes:

- Month selector.
- Search box.
- Category and type filters.
- Expense, income, refund, transfer, and net totals.
- Category spending bars.
- Transaction table.

If the user wants a custom visual style, copy the template to the requested output folder and edit the copied template or generated report, not the bundled asset.

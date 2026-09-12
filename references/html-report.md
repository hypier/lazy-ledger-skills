# Local App And HTML Reports

Use the live localhost app for viewing or editing the current ledger. Use `render` for a self-contained read-only snapshot.

## Live App

Before starting another server, check whether port 8765 already serves the intended ledger:

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
curl -fsS http://127.0.0.1:8765/api/health
```

If it is stopped or points at a different ledger, start the service:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" open
```

`open` is shorthand for `serve --open`: it starts the localhost app on port 8765 (or the next free port) and opens the browser. Use `serve` without `--open` when you only need the URL printed.

Use `--port` when 8765 belongs to another process. Bind only to `127.0.0.1`.

Useful routes:

- `/#ledger`: transactions.
- `/#charts`: reports.
- `/#bill`: monthly bill.
- `/#book`: accounts and budgets.
- `/settings`: category names, hierarchy, and icons.
- `/bill/YYYY-MM`: one monthly canvas bill.

After a write, `/api/ledger` should report the same transaction count as the file. Server health proves only that the service is running; it does not prove the user's current browser tab refreshed.

If an in-app browser blocks `goto` or `reload` for localhost, do not bypass the URL policy and do not claim the page was refreshed. Keep the server running and ask the user to refresh the existing tab or click the URL.

## Live UI Boundaries

The app reads and writes the same JSON document as the CLI. It supports transaction editing, accounts, budgets, two-level categories, monthly and annual reports, and saved bill letters.

Habits remain an internal signal: do not add frequent-item chips or “save as common” UI. Category/account/type icons and direct row editing are established UI conventions; preserve them when changing the bundled frontend.

## Static Snapshot

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" render \
  --output ./lazy-ledger-report.html
```

The generated page is self-contained, makes no network requests, embeds a snapshot of ledger data, and opens directly in a browser. It includes filters, period comparison, account balances, budgets, category/merchant/day views, review queues, transaction rows, and CSV export.

When the user wants a custom one-off appearance, edit a copy of the generated file. Change `assets/ledger-viewer-template.html` only when they explicitly ask to change the skill itself.

## Monthly Canvas Bill

`bill save` writes a separate self-contained HTML file beside the ledger:

```text
{ledger-stem}-bill-YYYY-MM.html
```

It is also served at `/bill/YYYY-MM`. The page supports PNG export and printing. Do not store generated absolute paths in the ledger JSON.

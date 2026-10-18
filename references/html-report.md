# Optional HTML output and compatibility HTTP API

No fixed HTML assets ship in 3.0. The three old interactive pages have been
removed. Recording, correction, analysis and reports work without a browser.
Use [present.md](present.md) to choose an output that helps the current task.

`report build --start YYYY-MM-DD --end YYYY-MM-DD --format html` renders the
same verified report package as Markdown/JSON. `--archive` saves the artifact,
source snapshot and renderer version. The page is read-only, responsive, fully
escaped and contains no JavaScript or client-side financial calculations.
Account balances are the current snapshot's balances, not period-end balances.

Compatibility entry points:

- `render --output report.html`: a static report over recorded dates.
- `bill render --month YYYY-MM`: a monthly report beside the ledger, named
  `<stem>-bill-YYYY-MM.html`; saved narrative is labelled for rechecking.
- `serve` / `open`: optional localhost service. `/` and `/settings` show a
  read-only report; `/bill/YYYY-MM` renders a month. The old editing tabs,
  charts, PNG export and custom `--template` option are retired.
- The existing JSON HTTP transaction/account/budget API remains available to
  trusted local clients and calls the same services as the CLI. The report page
  has no editing controls. `/api/health` checks service health; `/api/ledger`
  reads the current ledger. Health does not prove a browser has refreshed.

Bind only to `127.0.0.1`. `serve --open` opens a browser and blocks until stopped;
its first JSON line gives the actual URL. Start a server only for a concrete
viewing or integration need. If unavailable, return Markdown or a chat summary.

Generate one-off visualizations from the report JSON using host capabilities.
Keep their data snapshot, period and coverage; do not place a second accounting
engine in the view. Custom executable renderers require host isolation and
verification. A reusable template is optional and should be introduced only
for a demonstrated recurring need. Historical report files remain intact.

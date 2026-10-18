# Presentation and reports

Load `context --task report` and the user's profile. The purpose of presentation
is comprehension, not filling a fixed set of pages. Start with a concise answer
in chat. A single fact needs no dashboard.

- `show` / `brief`: current facts and actionable items in dialogue.
- `summary --json` / `show --json`: shared metric input for a chart or report.
- `export`: portable CSV or JSON detail when requested.
- `bill show --month YYYY-MM --json`: monthly facts and an existing saved letter.
- `bill save --month YYYY-MM --title ... --body ...`: save the written report.
  This does not create HTML. Avoid unsupported claims or moral judgments.

Choose a graph for a trend or comparison when it makes the answer easier to
understand. Use available host presentation capabilities and the core metric
payload; include period, currency and coverage. Do not invent another financial
aggregation algorithm inside a template. Refresh data before publishing a
previously generated view. State whether an artifact is a snapshot.

The `render`, `bill render` and `serve` compatibility entries now use the shared
report service, documented in [html-report.md](html-report.md). All three old
HTML assets were removed. The runtime manifest legitimately lists zero templates.
`profile.presentation.density` accepts `compact`, `normal` or `detailed`; compact
output retains metrics/evidence and omits the row listing. Graph and format
preferences in user prose guide the host's output choice.

Use `report build --start YYYY-MM-DD --end YYYY-MM-DD` for the new workflow.
Default Markdown contains totals, coverage, budget progress and evidence rows;
`--format json` returns the same metric package, and `--format html` creates a
self-contained, read-only page without a template or server. It contains no
client-side financial calculations. Chinese long text wraps on narrow screens.
Choose rich host visualizations only when they improve understanding.

`--format openui` adds an offline interactive snapshot; `--format view-json`
returns only its minimal display data. Choose and order registered components
using `--layout`, or let the core select a default based on data and density.
For layout rules, import previews and archive behavior, read
[generative-ui.md](generative-ui.md). These components replace fixed-page design,
not the shared financial calculations. The renderer exposes no write actions.

`--archive` saves a unique report directory with data, the rendered result and
a renderer/digest manifest. The data includes source transaction IDs and rows,
ledger/profile/runtime tokens, coverage, profile snapshot and metric version.
`report read --id REPORT_ID --format ...` uses that archived snapshot, never the
latest ledger. A correction leaves the old report intact; a new build gets a new
ID. Archive files are excluded from the ledger Git snapshot by default.

Legacy `bill save` narratives can become stale after corrections; recheck their
claims against current metrics before reuse. If a visual output is unavailable,
use the Markdown or JSON package to answer in chat. No visual is required to
record, correct or explain the data.

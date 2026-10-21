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

For monthly reviews, apply the conclusions-first guidance in
[analysis.md](analysis.md). In a visual monthly report, make the key findings easy
to scan and pair them with relevant charts and short explanations; keep long
narratives and transaction tables available as supporting detail. Choose chart
types by the question: a pie/donut for a small number of positive composition
shares, a line for change over time, bars for comparisons, or a waterfall for
how income, spending and refunds produce the net result. Use alternatives when
negative values or too many categories make a pie misleading. Do not fill the
page with repeated bar charts or add chart variety without an analytical purpose.
Label amounts, shares, units and comparable periods clearly. Distinguish missing
records from zero activity. Every headline and callout must be supported by the
same current metrics as its chart; chart variety alone does not make an analysis.

When `metrics.income_basis` is present, use the core's `totals.income` and
`totals.net` as the primary figures and disclose that basis. Keep `raw_totals`
as a separate reconciliation reference. Explain income using signed
`income_evidence` contributions; ordinary income-type transactions alone omit
adjustments based on transfers and excluded work payments. Do not apply the
profile adjustment a second time in a view. Month/member views must use the same
core rules. An unlabelled expense is not implicitly owned by a specific member.

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

For a custom interactive view, read [generative-ui.md](generative-ui.md). The agent
chooses the structure and authors OpenUI Lang or HTML, using `report ui-spec` and
selected `report data` from a frozen report. `report validate` returns diagnostics;
`report compose` renders the corrected source or archives it. Core financial values
remain authoritative; agent prose and HTML need semantic review. The old
`--format openui --layout ...` path remains available for simple compositions.

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

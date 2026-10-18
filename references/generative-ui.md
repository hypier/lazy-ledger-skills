# Optional OpenUI snapshots

Use this for interactive comparisons, evidence browsing, budgets or import
previews when they help the user. Ordinary entries and single facts stay in chat.
Read the user's presentation preferences and `context --task report` first.

The host agent chooses components; the core compiles a bounded JSON layout to
OpenUI Lang. The bundled OpenUI Renderer uses that description to render the
snapshot. No Gateway, API key, extra LLM call, CDN or running server is required.
This is a component composition capability, not three fixed report templates.

## Generate and inspect

```sh
ledger report build --start 2026-10-01 --end 2026-10-31 --format view-json
ledger report build --start 2026-10-01 --end 2026-10-31 --format openui --output /path/to/new-report.html
ledger report build --start 2026-10-01 --end 2026-10-31 --format openui --layout /path/to/layout.json --archive
ledger report read --id REPORT_ID --format openui --output /path/to/replayed-report.html
```

`view-json` is the minimal presentation projection, not the full report JSON.
It includes the chosen layout, data references, snapshot identity, period,
currency and coverage. It omits user-profile prose, runtime extensions, original
source cells and out-of-period balance inputs. Use it when preparing a display;
do not pass the full `--format json` report to an external UI service.

Choose an output path outside the installed skill and the ledger's authoritative
files. `--output` refuses an existing file; `--archive` creates a new report ID.
Open the resulting HTML in a browser or host file preview that permits scripts.
The static text remains visible if scripts are disabled, assets are unavailable,
or the renderer rejects the description. No Node installation is needed to use
the packaged skill. Markdown/JSON/static HTML continue to work separately.

## Compose from registered components

`context.capabilities.presentation.openui` lists installed versions, availability,
component names and permitted refs. The catalog is
`assets/openui/catalog.json`; the core projection and renderer use the same file.

A budget-focused layout could contain:

```json
{"version":1,"blocks":[{"component":"Budget","ref":"budgets"},{"component":"Breakdown","ref":"by_category"}]}
```

Available report components: `Metric` references `totals.income`, `totals.expense`,
`totals.refund` or `totals.net`; `Breakdown` references `by_category` or `by_day`;
`Budget` references `budgets`; `Transactions` references `transactions`.
Reorder or select blocks for the question and user preference. The default layout
adapts to available data and omits transaction rows for compact profiles.

Each block accepts only `component` and `ref`. Layouts accept 1–24 distinct blocks.
Do not supply amounts, labels, HTML, JS, raw OpenUI code, actions or tool calls.
An invalid layout is rejected before writing an artifact; correct it or use the
default layout. Header, period, currency and coverage cannot be hidden by layouts.
Amounts and budget percentages come from core calculations. Browser calculations
only scale visual bars and filter/page rows; they do not recompute financial totals.
Searching evidence does not change the report's scope or totals.

## Import preview

```sh
ledger import prepare --input /path/to/statement.csv > /path/to/import-plan.json
ledger report import-preview --input /path/to/import-plan.json --format openui --archive
```

Use `ImportSummary` / `import_summary`, `ImportRows` / `import_rows`, and `Issues` /
`import_issues` to compose an import view. These components are valid only for
import snapshots. Show new/existing/linked/excluded/review rows and parsing or
source-total errors. Older plans without preview data must be prepared again.
This view is a snapshot of preparation; it cannot commit anything. A ready plan
still needs the existing version-checked `import commit` workflow. Changing a
layout, filtering rows or opening an archive never authorizes a write.

## Versions and archives

OpenUI archives save the source snapshot, minimal `view.json`, `layout.json`,
`view.openui`, `fallback.md`, and a self-contained HTML file with its exact JS/CSS.
The manifest records dependency versions and content hashes. `report read
--format openui` replays saved HTML bytes for an OpenUI archive; it does not
regenerate it with the latest ledger or installed renderer. A view-json-only or
legacy archive has no saved OpenUI bundle, so an OpenUI read renders the archived
data with the currently installed renderer. Keep archive files together.

The installed catalog is exposed through context without modifying an old ledger's
portable runtime manifest. New frontend components require a maintained code
change, catalog update, version bump, rebuild and behavioral tests. They are not
Python converter extensions and cannot be loaded from a user's runtime directory.
No Query/Mutation/MCP tool provider is exposed. Future write interactions must
use the existing proposal and commit services, not a direct browser ledger API.

Upstream: [components](https://www.openui.com/docs/openui-lang/defining-components),
[Renderer](https://www.openui.com/docs/openui-lang/renderer).

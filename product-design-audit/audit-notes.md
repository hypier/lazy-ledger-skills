# Lazy Ledger Product Design Audit

Date: 2026-07-08

## Audit Scope

This audit reviews Lazy Ledger as a Codex bookkeeping skill, not as a full standalone app. The assessed experience is:

1. Record casual expense/income/refund/transfer entries into a local JSON ledger.
2. Find, correct, summarize, and delete transactions.
3. Generate and inspect the self-contained HTML dashboard.

Evidence reviewed:

- `SKILL.md`
- `references/bookkeeping-rules.md`
- `references/ledger-schema.md`
- `references/html-report.md`
- `scripts/ledger_tool.py`
- `assets/ledger-viewer-template.html`
- Generated sample files:
  - `product-design-audit/sample-ledger.json`
  - `product-design-audit/sample-report.html`

Screenshot capture was attempted through the Product Design browser flow, but the in-app browser blocked direct `file://` access and local HTTP preview timed out in this environment. Chrome extension control was unavailable. The HTML report was still generated successfully and reviewed from source/template behavior.

## User Goal

The intended user wants very low-friction personal bookkeeping: say or paste something like "昨天星巴克 38", have it recorded correctly, and later ask "这个月花了多少" or open a report without managing a formal accounting app.

## Current Strengths

1. The core promise is sharp: a local, lightweight ledger with minimal user effort.
2. The schema is understandable and preserves unknown fields, which gives room for future growth.
3. The deterministic CLI covers the essential operations: init, add, update, delete, list, summary, render.
4. The skill has sensible bookkeeping rules for Chinese casual input, including categories, refunds, transfers, screenshots, corrections, and duplicate checks.
5. The HTML report is offline and self-contained, which is a good privacy fit for personal finance data.

## Main UX Risks

1. There is no deterministic parse/preview command. The most important product behavior, turning casual text or receipt text into structured fields, lives in the agent instructions rather than in testable code.
2. Duplicate detection is documented but not implemented in the CLI. This is a high-risk gap because accidental duplicate expenses quietly distort totals.
3. Import/clean is part of the skill description, but the CLI has no import command. Users who paste Alipay/WeChat/bank exports will depend on ad hoc agent behavior.
4. The correction flow requires transaction IDs. That is reliable for machines, but clunky for humans who say "把昨天星巴克改成咖啡" or "删掉刚才那笔".
5. Category customization is sketched in the schema, but not surfaced in commands or reports. A user cannot easily teach the ledger their own categories.
6. The dashboard is read-only. It helps inspect data, but cannot correct obvious mistakes from the place where the user spots them.
7. The report has totals and category bars, but lacks trend, recurring payment, budget, and anomaly views. It answers "花了多少", but not yet "哪里不对" or "下个月要注意什么".

## Accessibility Risks

1. The report controls are native inputs/selects, which is a good baseline, but they have no visible labels beyond placeholder/default option text.
2. Type is communicated mainly through color in metric values and amount colors. The table has text labels, but the metric row could be clearer for low-vision users.
3. Mobile behavior hides merchant and note columns at small widths. That preserves layout, but it also removes context needed to identify a transaction.
4. The category bar chart is visual-only; amounts are visible, but there is no semantic table alternative or richer description for screen readers.
5. Keyboard/focus behavior was not verified because browser screenshot/control could not be completed in this environment.

## Recommended Roadmap

### P0: Make bookkeeping safer

1. Add a `parse` or `preview-add` command that accepts raw text and returns normalized JSON without writing.
2. Implement duplicate detection in `add`, with a `--allow-duplicate` escape hatch.
3. Add a human-friendly `recent` command and "last transaction" shortcut support for corrections.
4. Add validation for confidence range, currency consistency, date formats, and category names.

### P1: Make import and cleanup real

1. Add `import` for CSV/JSON with mapping presets.
2. Add `clean` or `doctor` to normalize old ledgers, detect missing fields, duplicates, invalid amounts, and suspicious dates.
3. Add `export` to CSV/JSON so users are not locked into the local format.
4. Add a batch review workflow: proposed transactions -> accept all / skip / edit.

### P1: Improve the report as a personal finance product

1. Add month-over-month trend and daily spending line/bar views.
2. Add top merchants and recurring expenses.
3. Add anomaly flags, such as unusually large expense, duplicate-like entry, or uncategorized spike.
4. Add a "needs review" section for low-confidence or imported transactions.
5. Add optional budgets per category and remaining budget indicators.

### P2: Improve personalization

1. Add category management commands: list, add keyword, rename, merge.
2. Support account/payment channel fields for cash, card, WeChat, Alipay, bank, etc.
3. Support multi-currency normalization with original amount and converted amount.
4. Add tags presets for trips, projects, family, reimbursement, and work expenses.

### P2: Improve dashboard accessibility and usability

1. Add explicit labels for filters.
2. Avoid hiding important transaction context on mobile; prefer stacked row cards below a breakpoint.
3. Add non-color cues for transaction type.
4. Add sortable columns and downloadable filtered CSV.
5. Add a review/correction affordance, even if it only copies the right CLI command.

## Suggested Next Implementation Order

1. `preview-add` plus duplicate detection.
2. `doctor` validation and duplicate report.
3. Import pipeline with batch review.
4. Dashboard "needs review", trends, and mobile transaction cards.
5. Category customization and budget features.


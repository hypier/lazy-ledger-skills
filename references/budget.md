# Budget manager

Load `context --task budget` and the user's profile and goals. Use
`budget list --month YYYY-MM --json` and `show --month YYYY-MM --json` to inspect
limits and spending. The core excludes open/settled advances, transfers and
not-mine rows consistently; a written-off remainder consumes its writeoff month.

Use `budget set --help` for the supported flags and set a budget only from a
user instruction or an approved proposal. State category, period and amount.
Recurring budget templates are explicit configuration, never an inferred fact.

If asked for guidance, show the observed amount, remaining budget and evidence
behind any forecast. A partial month or incomplete import limits the forecast.
Recommend an adjustment when useful; do not rewrite a budget merely because
spending exceeded it. Return the persisted result and one relevant implication.

# Ledger contract, version 2

The core is `scripts/ledger_core/`. Domain code owns decimal money, account
movements, consumption and settlement; storage owns persistence and revision
checks; services own user profile, runtime capabilities and Git snapshots.
Legacy command handlers continue through these boundaries.

A ledger has `schema_version`, stable `ledger_id`, increasing `revision`,
`currency`, timestamps, `git_preference` and document collections:
`transactions`, `accounts`, `budgets`, `categories`, `habits`, `bills`,
`review_queue`. Unknown fields are preserved. A malformed collection or unknown
schema version is an error, not an empty collection. Version 1 is readable;
`migrate` previews and explicitly adopts version 2.

Transactions retain IDs, positive two-decimal amount, type (`expense`, `income`,
`refund`, `transfer`), currency, occurred_at, category, source, confidence and
optional merchant, note, account IDs/names and source evidence. This release
supports a single currency per ledger; no implicit currency conversion.
An unknown account remains unassigned. A transfer requires both accounts.

Accounts have opening_balance and `opening_confirmed`. Default account templates
start with an unconfirmed zero, which is a calculation baseline only. Setting
an opening explicitly confirms it. New ledgers have no default payment account.
`excluded_from_totals` affects personal consumption, not actual cash movement.
`balance_effect: false` is reserved for core-generated noncash writeoff rows.

An advance is an expense with `advance: open`. Its actual account outflow stays
on the original date. Each `advance_settlements` entry names an actual income
transaction, its amount and date. Settlement cannot exceed the remaining
amount or precede the advance. The repayment row has a related_transaction_id
and reimbursement/repayment relation, so it is not personal earnings.

Writeoff creates a new expense for the remainder on the writeoff date, with
`relation: writeoff`, `balance_effect: false` and the related original ID. The
original gets `written_off_amount`, `writeoff_transaction_id` and written_off
state. It is excluded from consumption to avoid double counting. Legacy
written-off rows without the new link retain their original-date behavior;
migration does not fabricate a historical writeoff date.

`counts_as` is the common membership rule for summaries, budgets and insights.
Decimal arithmetic is rounded at the numeric JSON boundary. Account totals are
lifetime flows; period totals describe only the requested period. Do not equate
a partial ledger's net flow with actual available cash.

The authoritative profile lives in `<stem>-runtime/user-profile.md`, not in the
compatibility preferences/habits caches. See [habits.md](habits.md). Capability
versions live in `runtime.json`; see [runtime.md](runtime.md). A persisted `changes`
list records row before/after images and revision. `operations` stores add/record
idempotency receipts; repeating the same ID with different input is refused.

The recoverable transaction bundle includes the ledger, profile and runtime
manifest. Git history adds an optional snapshot of those three portable files.
Local machine permissions and remote configuration do not travel in the bundle.
See [history.md](history.md) for supported sync and restore behavior.

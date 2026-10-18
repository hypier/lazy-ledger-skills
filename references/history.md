# Optional ledger history and synchronization

At ledger creation ask once: no Git, local history, or private remote backup.
`init --git-mode undecided` records without initializing/uploading a repository.
`off` records refusal; `local` creates local history. Honor an existing choice.

Enable later with `history configure --mode local`. For remote mode, use the
host/provider to check the actual target and private visibility, then run:

```sh
ledger history configure --mode remote --remote URL --private-remote-confirmed
ledger history sync
```

The flag records the caller's completed visibility check; the CLI does not create
repositories or verify provider visibility. Use an empty private remote or this
ledger's remote and host credentials, never credentials in a URL. Sync runs in
the foreground only. Configuring a remote does not itself upload. `--mode off`
stops snapshots/sync without deleting history; `local` stops remote sync.

The isolated repository is `<stem>-runtime/history/.git`, bound to the ledger ID.
Only `ledger.json`, `user-profile.md`, `runtime.json` are versioned. The runtime
file includes registered converter code, fixtures and digests. Attachments,
reports, one-off scripts, local permissions, credentials and caches are excluded.
State this recovery scope to the user; excluded artifacts need separate backup.

## Saving and sync

A successful core write attempts a local snapshot. Git failure leaves the
financial write saved and reports pending; retry `history save`, not the original
transaction. `history list` lists snapshots. The core operation log retains row
changes even when Git is off.

`history sync` fetches/pushes outside the ledger write lock. Before applying it
rechecks data/rule/capability tokens and local HEAD; a concurrent write stops this
attempt, and a retry starts from the newer state. An incoming fast-forward
installs the exact validated bundle, without inventing another ledger revision.
Independent divergent records merge by stable ID in an isolated candidate and
produce a two-parent commit. Financial rows are indivisible conflict units.

Conflicting edits or deletion versus edit return `state: conflict` and a
`work/sync-conflicts.json` plan containing both versions and their commit IDs.
Populate `choices` with each conflict path mapped to `local` or `remote`, then
run `history sync --resolution plan.json`. Explain the financial effect; apply
choices within the user's correction authorization. Head changes invalidate the
plan. There is no last-write-wins or force push. Duplicate authoritative sources,
inconsistent settlements, profile/account conflicts and incompatible schema
changes fail validation; correct the relevant device records or migrate first.
Identical-source rows independently created with different IDs currently need
that explicit reconciliation rather than an automatic identity rewrite.

`context` returns local sync status. Remote success is reported only after
checking the pushed commit at the remote; a newer local edit remains pending.
Offline/authentication/unknown push outcomes retain local records and can be
retried. Status carries an evidence token and change flag to avoid repeatedly
announcing an unchanged failure. No scheduler is installed by the skill.

## Restore and a new device

`history restore --commit SHA` previews whole-bundle restoration; `--apply`
creates a new revision and commit. Current state is snapshotted first. Explain
that later rows leave the current view but remain in history. Profile and runtime
are restored together, while device permissions stay local. Use `proposal undo`
for a targeted operation reversal that preserves unrelated newer rows.

On a new device, clone into a separate working location, inspect the three
portable files and ledger identity, and explicitly configure local/remote mode.
Validate and enable converters again on that device. Portable user preferences
never authorize new-device uploads or code execution. Automated provider setup
and new-device onboarding are not implemented.

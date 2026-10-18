# Optional ledger history and synchronization

At ledger creation ask once: no Git, local history, or private remote backup.
`init --git-mode undecided` records without initializing/uploading a repository.
`off` records refusal; `local` creates local history. Honor an existing choice.

Enable later with `history configure --mode local`. For GitHub, preview an
existing repository with `history remote --repository OWNER/NAME`. Add `--create`
to preview creation; `--create --apply` creates a private repository using the
host's authenticated `gh`. `--apply` connects the inspected repository. The
service checks its actual private/archive status and, for a nonempty repository,
its main branch, portable files and ledger identity. Creation/connection never
uploads; run `history sync` when remote backup is authorized.

For another provider, use the host/provider to verify the actual private target,
then `history configure --mode remote --remote URL --private-remote-confirmed`.
That flag records a completed visibility check; it is not a provider API check.
Credentials stay in the host's `gh`/Git configuration, never in a remote URL.

Synchronization defaults to manual. When the user explicitly chooses automatic
sync, configure the verified remote with `--sync-mode after-write`. CLI/HTTP writes
queue retry intent locally and attempt sync after releasing the business lock.
Offline failures retain that intent; the next eligible CLI call retries, or use
`history sync`. Direct Python service users call `history.flush_auto_sync` after
their transaction. No resident scheduler is installed. `--mode off` stops both
snapshots and sync without deleting history; `local` stops uploads.

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
Authoritative same-source rows created with different IDs are aligned to one
canonical identity. Identical facts merge once and combine provenance; differing
facts produce a row conflict. `identity_aliases` preserves old IDs, and active
refund/settlement references are rewritten. Historical audit receipts remain
unchanged. A later correction can address either ID; undo of a pre-merge operation
may require a coordinated correction if the affected row/provenance changed.

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

On a new device, use `history inspect --source URL` to verify the portable
snapshot and obtain its ledger ID and commit. Then use:

```sh
ledger history clone --ledger /new/location/book.json --source URL \
  --expected-ledger-id LEDGER_ID --expected-commit COMMIT
```

The destination must be new. Restore validates before installation and records a
recovery journal before moving the runtime into place. If interrupted, the next
core load completes the bundle. Local-only mode is the default; remote mode
requires explicit current-device confirmation. No clone command uploads.
Validate and enable converters on that device before executing them.

`history diff --commit SHA` shows current/historical counts, periods, account
balances, receivables, source identities and budgets. `history restore` uses the
same preview. Archived reports, source attachments and device permissions are
explicitly listed as outside clone recovery. User-entered ledger evidence is
versioned intact, including paths a user put in an attachment/note; local runtime
configuration and credentials are excluded. Inspect the intended scope before
the first upload, and never describe the snapshot as a complete device backup.

GitHub commands follow the official [repo create](https://cli.github.com/manual/gh_repo_create)
and [repo view](https://cli.github.com/manual/gh_repo_view) interfaces. Provider
creation is exercised with a substitute provider response in automated tests;
real repository creation requires an authorized target and host authentication.

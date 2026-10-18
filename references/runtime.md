# Runtime context and controlled evolution

Every role starts with `context --task ...`. It includes ledger identity, revision,
schema and semantics versions, profile and capability tokens, coverage, extension
summaries and local Git status. Reload after relevant changes. Context does not
load converter source into every task; `schema show` exposes the full manifest.

`family.json` owns `family-runtime/`. The authoritative portable bundle is the
ledger, `user-profile.md` and `runtime.json`. A write-ahead `pending.json` lets the
next core reader complete an interrupted write under the ledger lock. Never
remove that journal as cleanup. Unknown core versions stop writes.

## Proposals and corrections

Use `proposal prepare --input request.json`, then inspect the returned preview.
Commit that exact JSON with `proposal commit --input proposal.json`. The saved
copy is `work/proposals/<id>.json`. A request contains `reason`, `operations`, and
optional `evidence`. Each operation has `action` (`add`, `update`, `delete`),
optional `collection` (default `transactions`), an `id` for an existing row, and
`after` for additions or `set` for corrections. `unset` removes optional fields.

```json
{"reason":"用户说明项目归属","operations":[{"action":"update","id":"tx_example","set":{"x_project":"旅行"}}],"evidence":{"kind":"user","text":"这笔算旅行费用"}}
```

Preparation works on a copy. Commit checks the complete ledger, profile and
runtime context, validates accounts, source identities and settlement relations,
then saves once. Replaying a committed proposal returns its receipt. A stale
proposal must be prepared again. Correct linked entries together in one proposal.

`proposal history` lists row changes and operation IDs. `proposal undo --operation
ID` previews a targeted reversal; add `--apply` when the task authorizes it.
Unrelated later rows remain. A later edit to the affected row blocks blind undo.
A metadata migration must be reversed with another metadata migration, because
its registry and data need to change together.

## Optional metadata migrations

Register a nonfinancial field with `schema register --name x_project --type string
--description "项目归属"`. Only registered JSON types are accepted on proposals.
Unknown existing fields are preserved; registration does not change money rules.

To rename or convert existing metadata, prepare `metadata-migrate --input
request.json`, then apply the returned plan with `metadata-migrate --input
plan.json --apply`. Example request:

```json
{"reason":"项目编码改为数字","operations":[{"field":"x_project","rename":"x_project_id","type":"number"}]}
```

The preview binds all context tokens and compares transaction count, totals and
account balances. Apply creates a three-file backup, updates data and registry
together, and records an idempotent receipt. Failed conversion or a collision
stops before writing. `metadata_revision` tracks these changes separately from
core schema. Monetary fields and new financial semantics require a tested core
migration; this service cannot redefine them. `migrate` remains the explicit
legacy-to-current core migration entry.

## User converters

Keep installed scripts unchanged. Put a draft converter in `work/`; register a
stable version into `extensions/<id>/<version>/main.py` only when needed. A bundle
has one Python entrypoint and standard-library dependencies only. Its adjacent
manifest must declare this contract and meaningful input/output fixtures:

```json
{"id":"my-statement","version":"1.0.0","entrypoint":"main.py","core_schema":[2],"dependencies":["stdlib"],"permissions":{"data":"input-only","network":false,"writes":"scratch-only"},"fixtures":[{"input":{"amount":12},"expected":{"version":1,"candidates":[{"amount":12,"occurred_at":"2026-10-01"}]}}]}
```

The script reads JSON from stdin and emits exactly one JSON document with
`version: 1` and `candidates: [...]`. Candidates are transactions, never direct
writes or arbitrary core operations. Include stable source IDs for imports.

1. `extension register --manifest manifest.json`: store an immutable candidate.
2. `extension validate --id my-statement --version 1.0.0`: execute fixtures and
   validate their financial candidates; this does not enable execution.
3. `extension enable --id my-statement --version 1.0.0`: enable that validated
   digest on this device; other versions of the same converter are disabled.
4. `extension run --id my-statement --version 1.0.0 --input input.json`: return a
   core proposal with per-field converter identity and input hash.
5. Inspect and commit the proposal through the normal core service.

`extension disable` and `extension list` manage availability. Editing a registered
script invalidates it; register a new version and validate again. Code and fixtures
travel in the runtime bundle so Git can recover the verified version, but
`extensions-local.json` permissions never travel. A new device must validate and
enable locally, even if the portable manifest says enabled.

The current runner supports macOS `sandbox-exec` only. It grants access to Python
and system runtime files plus a disposable working directory, denies user data
reads, external writes, network operations and child creation, and limits CPU,
wall time and output size. It is not a general dependency installer or a memory
quota system. Unsupported hosts or a sandbox that cannot start fail closed;
never substitute an unrestricted subprocess. Use an already supported importer
or a converter on a supported host instead.

# User profile and observations

Each ledger uses `<ledger-stem>-runtime/user-profile.md`. Load it for every task
through `context`. The marked JSON block is the authority for confirmed rules;
prose outside the block supplies context and must survive structured updates.
No bundled file contains a real person's habits.

The profile contains a version, optional default account ID, merchant-category
rules, aliases, confirmed phrase rules, communication preferences, and goals.
A fixed price may be used only when explicitly confirmed. Repeated prices and
frequent accounts remain observations, regardless of frequency.

`profile show` returns the current rules and token. To apply structured changes,
write the complete revised rules to a temporary JSON file and run
`profile apply --input FILE --expected-token TOKEN`. Read the profile first and
preserve unrelated fields. The legacy `account update --default`, `prefer`, and
`habit set/delete` commands update the same authority transactionally.

Phrase rules may set `enabled: false` or `expires_on: YYYY-MM-DD`. They remain in
the document but inactive rules must not fill fields. Deleting a confirmed rule
removes permission to apply it; observations cannot regrant that permission.

`habits` and `preferences.usage_profile` remain compatibility caches in the
ledger for compatibility. `habit list` computes an observation view
without saving. `habit rebuild` explicitly refreshes it. `habit memory` creates
a legacy derived Markdown export; that file is never the authority for rules.
Observations may suggest a question, but must not overwrite the profile.

For old files, `migrate` previews adoption of an independent profile;
`migrate --apply` saves a bundle backup first. Manual phrase rules and merchant
preferences are retained. A legacy default account is not automatically
confirmed because older versions supplied one without asking. Existing derived
`<stem>-memory.md` is backed up and retained as explicitly unconfirmed prose; it
does not grant fixed-price or execution permissions.

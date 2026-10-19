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
Observations may suggest a question or a clearly marked candidate note, but must
not overwrite confirmed rules.

## Learn from conversation

Notice reusable information during recording, import review, corrections and
analysis, not only in transaction statistics. Distinguish these scopes:

| User evidence | Treatment |
|---|---|
| “这次用现金”“这笔是退款” or an explanation limited to selected rows | Apply only to the authorized rows; do not create a standing rule |
| “以后都这样记”“默认用这个账户”“记住这家店归餐饮” | Save the explicit rule if its scope and required fields are clear; do not ask the same permission again |
| “我一般这样付”“这类钱通常是家里的资金”, or repeated similar corrections | Treat as a candidate; explain the proposed scope and ask whether to remember it |
| “这次例外” | Override only this event; retain the existing standing rule |
| “以后改成……”“不要再按原来的规则” | Update or disable the identified rule; preserve unrelated rules |

A relationship or ownership fact is not by itself a rule for every transaction
with that person. For example, a family member may hold shared funds but also
send gifts or loan repayments. Confirm which funds and accounts are covered,
and distinguish household scope from personal scope before proposing a transfer
rule. Never use the counterparty's name alone to decide all income or expenses.

Ask one concise, concrete question when remembering would help: “你刚才说这家店
通常是工作餐，要不要记住：以后这家店默认归工作餐，本次另有说明时按你的说明？”
Use actual evidence; do not invent repetition or infer a fixed amount from
repeated prices. If the scope is already explicit, save it instead of asking.
An unanswered, declined or deferred suggestion stays inactive and must not block
an otherwise clear transaction. Do not repeat the same suggestion without new
evidence or a user request to revisit it.

## Persist the decision in the user's ledger

Use the `profile_path` returned by context, never bundled skill instructions.
Keep narrative notes outside the marked JSON block, separating **待确认习惯**
from **已确认约定**. For useful cross-session candidates, record the proposed
scope, supporting user statement or transaction references, date and status
(pending, deferred, declined or confirmed). These notes are observations, not
executable defaults; retain only information relevant to bookkeeping. Do not
rewrite the derived `observations.json` cache to store conversational decisions.

Once confirmed, use the existing structured profile fields and `profile apply`,
`prefer` or `habit set` for rules the core supports. Preserve all unrelated
fields and prose, use the current profile token, then reload context. Record the
confirmation source and scope in the narrative notes, and close the matching
candidate so it is not proposed again. Report what was saved and when it applies.

For a contextual agreement the structured schema cannot express (such as the
scope of family-held funds), keep a confirmed narrative agreement with its
exceptions. Load it in future tasks and use it to reason about each event; do not
invent schema fields or claim the parser enforces it automatically. Ambiguous
events still need clarification. A confirmed fixed amount, account or category
default must use the supported structured rule before being treated as such.

Before editing narrative notes, reread the document and preserve the marked
JSON and unrelated text. If concurrent changes appear, reload and merge rather
than overwriting them; refresh context after saving. Clearly retain the user's
one-time overrides and any expiry. A future default does not authorize rewriting
historical transactions; handle retrospective changes as a separate scoped
request. An explicit replacement supersedes the identified old rule without
another confirmation. If conflicting scopes remain unclear, clarify them before
changing the rule; current explicit facts govern the current event.

For old files, `migrate` previews adoption of an independent profile;
`migrate --apply` saves a bundle backup first. Manual phrase rules and merchant
preferences are retained. A legacy default account is not automatically
confirmed because older versions supplied one without asking. Existing derived
`<stem>-memory.md` is backed up and retained as explicitly unconfirmed prose; it
does not grant fixed-price or execution permissions.

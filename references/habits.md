# Habits And Merchant Preferences

Use this reference when the user mentions usual prices, default accounts, merchant rules, or asks how they normally record transactions.

## Two Layers

1. `habits` and `preferences.usage_profile` in the ledger drive parsing.
2. `{ledger-stem}-memory.md` is the compact portrait the agent reads before recording.

Refresh the portrait when it is missing, older than 7 days, at least 10 transactions behind, after a large import, or when the user explicitly asks to update habits:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" habit memory
```

## Applying The Portrait

- `默认`: fill a missing account or method silently.
- `稳定金额`: may fill amount and category for the listed short phrase.
- `不要猜金额`: ask when amount is absent.
- Payment-processor company names are not stable-price phrases.
- Do not expose shortcut chips or ask the user to save a frequent item.

Mention a silently applied default only when it matters, for example `按你习惯记到微信零钱`.

## Explicit Rules

Set a usual amount only when the user states it:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" habit set \
  --phrase 午饭 \
  --amount 16 \
  --category 餐饮
```

Remember a merchant category or alias with `prefer`:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" prefer \
  --merchant 星巴克 \
  --category 咖啡茶饮
```

Updating a transaction category while a merchant is present may record a learned preference only when the pattern is stable. A one-off correction is scoped to that transaction; use `prefer` or an explicit “以后都这样记” request for a permanent rule.

Do not learn a merchant mapping from a guessed `其他` category. Do not lock an amount merely because several imported processor rows happen to match.

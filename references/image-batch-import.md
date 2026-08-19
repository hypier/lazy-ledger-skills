# Image Batch Import

Use this workflow when the user provides a payment-history screenshot and wants every visible transaction recorded.

## Goal

Turn many visible rows into deterministic ledger writes with one TSV import, instead of dozens of ad hoc `add` commands.

## Workflow

1. Inspect the image and decide whether it shows one transaction or many.
2. If it shows many, record only the fully visible rows. Do not invent cropped or half-visible items.
3. For each row, capture:
   - `occurred_at`
   - `type`
   - `amount`
   - `category`
   - `merchant`
   - `method` when the channel is visible
   - `note`
   - `source`
   - `confidence`
4. Save the cleaned rows as TSV with this header:

```tsv
occurred_at	type	amount	category	merchant	note	source	confidence	method	account
```

5. Import the file:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py import-tsv \
  --ledger ./lazy-ledger.json \
  --input ./rows.tsv
```

6. Run a health check after import:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py doctor \
  --ledger ./lazy-ledger.json \
  --json
```

## Heuristics

- Use `income` for红包 or money received.
- Use `transfer` for提现到银行卡 or transfers between the user's own accounts.
- Keep positive amounts; let `type` express direction.
- Preserve visible truncated merchant names such as `深圳市顺易通信息科技有限...` rather than guessing the hidden suffix.
- Lower `confidence` for truncated merchant names, masked recipients, or rows whose purpose is inferred from context.
- Store the source screenshot path in `attachment` when it helps future auditing.

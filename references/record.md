# Record And Correct Transactions

Read [bookkeeping-rules.md](bookkeeping-rules.md) for field inference and [habits.md](habits.md) when defaults or usual amounts matter.

## Choose The Write Path

| Input | Action |
|---|---|
| One or several clear text items | `add --text` immediately |
| Parseable but worth checking | `parse --text`, then add |
| Missing amount without a stable habit | Ask for the amount |
| Type could change totals | Ask whether it is expense, income, refund, or own-account transfer |
| One receipt or one-purchase image | Extract visible fields and `add --source image` |
| Several visible rows or a long screenshot | Follow [image-batch-import.md](image-batch-import.md) |
| Official statement or cross-source import | Follow [reconcile-imports.md](reconcile-imports.md) |
| Correction or deletion | Find the existing row, then update/delete by ID |

## Add Or Preview

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" add \
  --ledger ./lazy-ledger.json \
  --text "昨天微信 星巴克 38"
```

Several lines or semicolon-separated items can be passed in one `--text` value. Prefer paid amount when the input also shows an original price.

Use `parse` when a proposal should be inspected without writing:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" parse \
  --ledger ./lazy-ledger.json \
  --text "原价45 实付38 星巴克"
```

Useful overrides include `--type`, `--category`, `--merchant`, `--method`, `--account`, `--to-account`, `--date`, `--occurred-at`, `--tags`, `--source`, `--confidence`, and `--attachment`.

If `add` exits with `likely_duplicate`, show the candidate and ask whether both are real. Use `--allow-duplicate` only after confirmation.

## One Receipt Or Image

- Record only fields visible in the source.
- Paid amount wins over original/list price.
- Preserve exact date and time when visible.
- Keep a truncated merchant name truncated and lower `confidence`.
- Do not attach a temporary chat-cache thumbnail path.
- If the image contains multiple rows, stop and use the batch screenshot workflow.

## Correct Or Delete

Find the target first:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" find \
  --ledger ./lazy-ledger.json \
  --text "昨天星巴克"
```

When one target is clear, keep its ID and update only requested fields:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" update \
  --ledger ./lazy-ledger.json \
  --id tx_20260707_ab12cd34 \
  --category 咖啡茶饮
```

Use `--occurred-at` to add an exact time without replacing the transaction. A category update on a row with a merchant also remembers that merchant preference.

If several rows match, list concise candidates and ask which one. Delete only after the target is unambiguous:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" delete \
  --ledger ./lazy-ledger.json \
  --id tx_20260707_ab12cd34 \
  --yes
```

## Accounts And Transfers

Use specific accounts when known. A payment channel is not necessarily the balance-bearing account; see [reconcile-imports.md](reconcile-imports.md) for imported payment data.

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" account list \
  --ledger ./lazy-ledger.json \
  --json

python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" account add \
  --ledger ./lazy-ledger.json \
  --name 招商银行储蓄卡 \
  --type bank \
  --opening 12000
```

Own-account movement and credit-card repayment are `transfer` rows with both source and destination accounts. Balance is opening balance plus income/refund/transfer-in minus expense/transfer-out.

## Budgets

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" budget set \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --category 餐饮 \
  --amount 2000
```

Omit `--month` to create a reusable monthly template. A month-specific budget overrides the template for that category.

## After A Write

Reply in Chinese with the date, type, category, merchant, amount, account, and changed/new IDs. Mention only consequential inference, then give the affected month's expense total and matching budget remainder when present.

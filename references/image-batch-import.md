# Payment-History Screenshot Import

Use this workflow for a long WeChat/bank bill screenshot or any image containing several transactions. Never write from the screenshot before the user confirms the final table.

## 1. Prepare

```bash
python3 "$LEDGER_SKILL_DIR/scripts/bill_screenshot.py" prepare \
  --image /path/to/screenshot.jpg \
  --output ./wechat-bill-rows.tsv \
  --json
```

The JSON includes `markdown`, parsed `rows`, the TSV path, page-header monthly totals, and whether a wider original replaced the supplied image.

If it exits with `ocr_too_sparse`, ask for the album/Finder original. Do not guess from a thumbnail.

## 2. Reconcile Before Showing The Table

Compare every parsed row with the current ledger using exact time, amount, type, and merchant, then source evidence when available. Follow [reconcile-imports.md](reconcile-imports.md) for duplicate decisions.

Classify each visible row as:

- `新增`: not present.
- `已有，跳过`: exact existing transaction.
- `已有，仅更新`: the screenshot adds evidence such as exact time or a clearer merchant.
- `需确认`: ambiguous merchant, amount, account, type, or collision.

Correct only evidence-backed OCR mistakes and configured merchant categories. Keep unresolved truncation and lower confidence. The TSV used for import must contain new rows only; apply approved updates separately by existing transaction ID.

## 3. Show And Stop

Show the complete action table with date/time, merchant, type, amount, category, and action. State new expense/refund totals and any account assumption.

Stop after the table. Import only after the user replies with an explicit confirmation such as `可以导入`, `确认`, or `没问题`. An initial “请导入” request does not replace this confirmation step.

## 4. Apply After Confirmation

1. Copy the current ledger to a temporary preview.
2. Apply approved existing-row updates to the preview.
3. Import the reviewed new-row TSV:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" import-tsv \
  --ledger /tmp/lazy-ledger-preview.json \
  --input ./wechat-bill-rows.tsv
```

Use `--allow-duplicate` only when the reviewed TSV intentionally contains a valid same-merchant repeat or an expense/refund pair.

4. Validate preview counts, IDs, categories, totals, and duplicate audit.
5. Create a real backup and ensure the live ledger is still the version that was previewed.
6. Apply the same updates and import once to the real ledger.

## 5. Verify And Clean Up

Run:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_audit.py" \
  --ledger ./lazy-ledger.json --json
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" doctor \
  --ledger ./lazy-ledger.json --json
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" show \
  --ledger ./lazy-ledger.json --month YYYY-MM --compare
```

Refresh `habit memory` after a large import. If the local app is running, verify `/api/health` and `/api/ledger`. Remove the temporary TSV and preview files after successful application; keep the real backup.

## Parsing Heuristics

- `+`, `退款`, or `已全额退款`: `refund`; strip only a visible trailing refund label from the merchant.
- `转账-来自…` / 红包 received: `income`.
- `转账-转给…`: usually `expense` + 人情, not an own-account transfer.
- 提现到 the user's bank account: `transfer`.
- Page-header totals cover the whole month and need not equal the visible rows.
- Do not store a temporary chat-cache image path as `attachment`.

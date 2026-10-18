# Payment-History Screenshot Import

Use this workflow for a long WeChat/bank bill screenshot or any image containing several transactions. Never write from the screenshot before the user confirms the final table.

## 1. Prepare

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" shot prepare \
  --image /path/to/screenshot.jpg \
  --output ./wechat-bill-rows.tsv \
  --json
```

The JSON includes `markdown`, parsed `rows`, the TSV path, page-header monthly totals, and whether a wider original replaced the supplied image. Built-in OCR uses macOS Vision. Pass a known `--account` separately from the payment `--method` and `--bill-label`. A channel does not identify the funding account; absent accounts stay unassigned. The TSV preserves the image hash and original row index.

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

Use the shared plan/commit workflow after confirmation:

```sh
ledger import prepare --input ./wechat-bill-rows.tsv --confirmed-image
ledger import commit --input saved-import-plan.json
```

Save the exact ready plan returned by `prepare`. Use `--links` for approved
existing-row associations and `--distinct` for explicitly confirmed separate
purchases. The core validates a copy, checks source totals and full context,
then commits once with a replay receipt. The compatibility `import-tsv` entry
uses this same service and also requires `--confirmed-image` for image rows.
Never add that flag before visible confirmation.

## 5. Verify And Clean Up

Run:

```bash
"$LEDGER_SKILL_DIR/scripts/ledger" audit --json
"$LEDGER_SKILL_DIR/scripts/ledger" doctor --json
"$LEDGER_SKILL_DIR/scripts/ledger" show --month YYYY-MM --compare
```

Refresh `habit memory` after a large import. If the local app is running, verify `/api/health` and `/api/ledger`. Remove the temporary TSV and preview files after successful application; keep the real backup.

## Parsing Heuristics

- A positive refund row is `refund`; a positive receipt is otherwise income. An original expense labelled refunded remains a separate source fact for review. Never infer a missing original expense from a refund.
- `转账-来自…` / 红包 received: `income`.
- `转账-转给…`: usually `expense` + 人情, not an own-account transfer.
- 提现到 the user's bank account: `transfer`.
- Page-header totals cover the whole month and need not equal the visible rows.
- Do not store a temporary chat-cache image path as `attachment`.

# Image Batch Import

Two paths: a **long WeChat/bank bill screenshot** (standard SOP below), and a **manual TSV** when the agent already has a clean row list.

Do not invent cropped or half-visible rows. Do not write the ledger until the user confirms the table.

## Long screenshot SOP (required)

Chat previews are often ~99px wide and unreadable. The script looks up a wider original in Downloads/Desktop when needed, slices the image, OCRs with macOS Vision, and prints a markdown table. **Stop after the table.** Import only when the user says 可以导入 / 确认 / 没问题.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/bill_screenshot.py prepare \
  --image /path/to/screenshot.jpg \
  --output ./wechat-bill-rows.tsv \
  --json
```

`--json` includes `markdown`, `rows`, `tsv`, `header` (month totals from the page chrome), and `thumbnail_replaced`.

Then:

1. Paste `markdown` in chat. Ask the user to check merchants, amounts, and refund pairs.
2. Do **not** run `import-tsv` in the same turn unless they already confirmed this table.
3. After confirmation:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py import-tsv \
  --ledger ./lazy-ledger.json \
  --input ./wechat-bill-rows.tsv \
  --allow-duplicate
```

`--allow-duplicate` is for same-day same-merchant **支出 + 退款** pairs (detector ignores type). Skip it only if the table has no such pair.

4. `doctor --json`, then `show --month YYYY-MM --compare`, then `habit memory` after a large import.

If `prepare` exits 2 (`ocr_too_sparse`), the file is still a thumbnail. Ask for the album original, or a file dragged from Finder/Downloads (`微信图片_YYYYMMDDHHMMSS_*.jpg`), not the chat preview.

## After confirmation: TSV shape

```tsv
occurred_at	type	amount	category	merchant	note	source	confidence	method	account
```

The prepare script already writes this. Do not rebuild it by hand unless the user edited a row.

## One receipt / few visible rows

If the image is a single purchase, extract fields and `add --source image`. If it is a short list and `prepare` is overkill, you may transcribe into TSV yourself, **still show the table and wait for confirmation** before `import-tsv`.

## Heuristics

- `+` and 退款 in the title or 已全额退款 → `refund` (strip a trailing `-退款` from the merchant so it matches the original expense)
- `转账-来自…` / 红包 → `income`
- `转账-转给…` → `expense` + 人情 (not an own-account `transfer`)
- 提现到银行卡 → `transfer`
- Keep truncated names (`给H。。。`, `梦相随百货…`); lower `confidence`
- WeChat page totals are for the **whole month**; this screen can be a subset
- Do not store chat-cache thumbnail paths as `attachment`

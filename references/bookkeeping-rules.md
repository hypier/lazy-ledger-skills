# Bookkeeping Rules

Use these rules when translating casual user input into ledger transactions.

## Minimal Friction

Do the obvious bookkeeping action when the amount and rough meaning are clear. Do not ask the user to fill a formal form.

Use `ledger_tool.py parse --text ...` to preview casual text and `ledger_tool.py add --text ...` to write clear casual entries. Prefer explicit `--amount`, `--category`, `--merchant`, and `--date` only when overriding parser output or handling non-text sources.

Ask a follow-up only when:

- No amount is present.
- Multiple amounts could be the transaction total.
- The entry may be income/refund/transfer rather than expense.
- The user asks to modify or delete a transaction but the target is ambiguous.

## Transaction Types

- `expense`: spending, default.
- `income`: salary, reimbursement received, interest income, side income.
- `refund`: money returned from an earlier expense.
- `transfer`: movement between own accounts; exclude from spending totals.

Store positive `amount` for every type. Let reports decide how the type affects net totals.

## Date Rules

- If no date is given, use today.
- Preserve explicit dates.
- Interpret casual Chinese dates:
  - `今天`: today
  - `昨天`: yesterday
  - `前天`: two days ago
  - `上周五`: the previous Friday
  - `这个月`: current month; if recording a transaction and no day is given, ask or use today only if the user implies it happened now

## Category Inference

Use these defaults unless the user has customized categories:

- 餐饮: 饭, 午饭, 晚饭, 早餐, 外卖, 火锅, 奶茶, 咖啡, 餐厅, 美团, 饿了么
- 交通: 地铁, 公交, 打车, 滴滴, 高铁, 火车, 机票, 停车, 加油
- 购物: 淘宝, 京东, 拼多多, 超市, 便利店, 衣服, 数码
- 居住: 房租, 水电, 燃气, 物业, 宽带
- 娱乐: 电影, 游戏, 演出, KTV, 会员
- 医疗: 医院, 药, 挂号, 体检
- 教育: 课程, 书, 学费, 培训
- 人情: 红包, 礼物, 请客
- 收入: 工资, 奖金, 报销, 利息
- 其他: cannot infer confidently

If merchant and category conflict, prefer explicit user category over inferred merchant category.

## Screenshots And Receipts

When a screenshot or receipt is provided:

- Extract amount, merchant, date/time, payment channel, and order note when available.
- Do not force the user to classify receipt vs payment screenshot.
- If the image contains both original price and paid amount, use paid amount.
- If the image contains multiple transactions, ask whether to record all or which one.
- Put uncertain extraction details in `note` and lower `confidence`.

When the image is a long payment-history list and the user wants every visible transaction:

- Transcribe only fully visible rows.
- Preserve the visible merchant text even if it is truncated with `...`.
- Lower `confidence` for truncated merchant names or ambiguous payment counterparts.
- Convert the cleaned rows into TSV and use `ledger_tool.py import-tsv ...` for deterministic writes.

## Duplicate Detection

Before adding a transaction, scan recent transactions for likely duplicates:

- Same amount.
- Same date or within a few minutes.
- Same merchant or very similar note.
- Same source image/note if available.

If likely duplicate, ask before adding unless the user explicitly says it is a separate purchase.

The CLI enforces this by refusing likely duplicates unless `--allow-duplicate` is provided. Treat that refusal as a prompt to confirm with the user, not as a hard failure.

## Corrections

When the user says a transaction was wrong:

- Find likely matches by amount, merchant, category, note, and recency.
- If one clear match exists, update it.
- If multiple matches exist, list concise candidates and ask which one.
- Keep the original transaction id unless deleting/recreating is necessary.

## Summaries

For summaries:

- Expense total includes only `expense`.
- Income total includes `income`.
- Refund total includes `refund`.
- Net cashflow = income + refund - expense.
- Transfers are listed but excluded from expense and income totals.
- Mention the date range used.

## Validation

Use `ledger_tool.py doctor --ledger ... --json` after imports, manual edits, or suspicious totals. It reports missing required fields, invalid amounts/types/confidence values, and likely duplicates.

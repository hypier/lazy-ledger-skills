# Record

How to turn user input into ledger writes. Read [bookkeeping-rules.md](bookkeeping-rules.md) for inference details.

## Choose a capture path

| Input | Action |
|---|---|
| One clear line (`昨天星巴克 38`, `午饭 26.5 餐饮`) | `add --text` now |
| Several items in one message, separated by newline or `；` | `add --text` with the full message |
| `午饭26 晚饭38` on one line | `add --text`; the parser splits by amount |
| Stacked WeChat/Alipay paste (merchant / 支出 / ¥38.00 / date) | `add --text` with the raw paste |
| Slightly ambiguous, but still parseable | `parse --text`, then add if the proposal looks right |
| Missing amount, two possible totals, or unclear income vs expense | Ask; do not write |
| Receipt/screenshot of one purchase | Extract fields, `add` with `--source image` and `--attachment` |
| Long payment-history screenshot | [image-batch-import.md](image-batch-import.md) → TSV → `import-tsv` |
| "以后星巴克都记咖啡" | `prefer --merchant 星巴克 --category 咖啡` |
| "午饭一般 16" / "地铁都是 4 块" | `habit set --phrase 午饭 --amount 16` then `habit memory` |
| "我平时怎么记 / 记账习惯" | Read `lazy-ledger-memory.md`; refresh with `habit memory` if stale |
| "把昨天星巴克改成餐饮" | `find --text` then `update --id` |
| "从微信转到支付宝 500" / 还信用卡 | `add --text`; sets `account` and `to_account` |
| "餐饮预算 2000" / "这个月预算 8000" | `budget set --amount` |
| "账户余额" / "加一张招行卡" | `account list` / `account add` |

## Add

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py add \
  --ledger ./lazy-ledger.json \
  --text "昨天微信 星巴克 38"
```

Useful overrides: `--type`, `--category`, `--merchant`, `--method`, `--account`, `--to-account`, `--date`, `--tags`, `--source`, `--confidence`, `--attachment`.

`--method` values: `wechat`, `alipay`, `cash`, `card`, `bank`, `other`.

`--account` / `--to-account` accept an account id or name (`微信零钱`, `支付宝`, `现金`, `银行卡`, `信用卡`). If omitted, expenses go to the default account (微信零钱 unless changed). Transfers need a destination; infer from `从微信转到支付宝` or `还信用卡`.

`add` refuses likely duplicates (exit 2, JSON on stderr). Treat that as a confirmation prompt. If the user says it is a separate purchase, rerun with `--allow-duplicate`.

After a successful add, confirm in chat using [present.md](present.md). The JSON includes `month.totals` for the month of the first added row.

## Parse without writing

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py parse \
  --ledger ./lazy-ledger.json \
  --text "原价45 实付38 星巴克"
```

One item → a single proposal object. Several items → `{count, proposals}`.

Prefer paid amount when the text has `实付` / `券后`. Ignore `原价` extras.

## Screenshots and receipts

- Extract amount, merchant, date/time, payment channel, and note when visible.
- If original price and paid amount both appear, record paid amount.
- One image, one purchase: `add` with `--source image`. Put doubtful bits in `note` and lower `--confidence`.
- One image, many rows: do not invent cropped lines. Clean into TSV and `import-tsv`.
- Keep truncated merchant text (`…`) instead of guessing the suffix.

## Correction

1. `find --text "昨天星巴克"` or `recent --limit 5` or `list --query 星巴克`
2. If one candidate is obvious, `update` or `delete --yes`
3. If several match, list concise candidates and ask which one
4. Keep the original `id`

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py find \
  --ledger ./lazy-ledger.json \
  --text "昨天星巴克"

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py update \
  --ledger ./lazy-ledger.json \
  --id tx_20260707_ab12cd34 \
  --category 餐饮
```

Updating a category while a merchant is set remembers that habit for later parses.

## Habits

Two layers:

1. **Ledger stats** in `lazy-ledger.json` (`habits`, `preferences.usage_profile`) — updated when recording, used by the parser.
2. **Memory document** `lazy-ledger-memory.md` next to the ledger — a periodic markdown portrait. This is what the agent Reads before recording.

The live page must not show 常用 chips or 存为常用.

Before `add`, Read the memory file. Refresh it only when missing/stale:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py habit memory \
  --ledger ./lazy-ledger.json
```

Stale = file missing, or `tx_count` behind the ledger by 10+, or `updated_at` older than 7 days. The command prints `{"path":"..."}`.

Apply silently from the markdown:

- 默认 → missing account / method
- 稳定金额 → fill amount and category; do not ask
- 不要猜金额 / 支付公司全称 → never invent amount

Do not list merchants as buttons. Confirm in one line if you used a default (`按你习惯记到微信零钱`).

If the user states a rule (`午饭一般 16`) or you rewrite the portrait:

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py habit set \
  --ledger ./lazy-ledger.json \
  --phrase 午饭 \
  --amount 16 \
  --category 餐饮

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py habit memory \
  --ledger ./lazy-ledger.json \
  --summary "通常用微信零钱记餐饮和交通。午饭大约 16 元。超市金额不固定，缺数字就问。"
```

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py prefer \
  --ledger ./lazy-ledger.json \
  --merchant 星巴克 \
  --category 咖啡
```

Do not learn merchant categories from a guessed `其他`. Only learn those from explicit `--category`, `update --category`, or `prefer`. Amount defaults come from repeated short phrases with a stable price, or from `habit set`.

## Accounts

Default accounts: 现金, 微信零钱, 支付宝, 银行卡, 信用卡. New expenses land on the default account (微信零钱). `微信` / `支付宝` / `现金` in the text selects that account.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py account list \
  --ledger ./lazy-ledger.json \
  --json

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py account add \
  --ledger ./lazy-ledger.json \
  --name 招行储蓄卡 \
  --type bank \
  --opening 12000

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py account update \
  --ledger ./lazy-ledger.json \
  --id acc_wechat \
  --default
```

`--type`: `cash`, `wechat`, `alipay`, `bank`, `credit`, `other`.

The live page can also add an account and edit opening / default. CLI is still fine for chat.

Balance = opening + income/refund/transfer-in − expense/transfer-out. Credit cards go negative as you spend.

Tag `报销` on an expense to keep it in the 待报销 queue until you add `已报销`.

## Budgets

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py budget set \
  --ledger ./lazy-ledger.json \
  --amount 8000

python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py budget set \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --category 餐饮 \
  --amount 2000
```

Omit `--month` to reuse the limit every month. A month-specific row overrides the template for that category. `show` and the HTML report display spent / remaining.

## Batch import

Known rows → TSV, not dozens of `add` calls.

```bash
python3 /Users/barry/.agents/skills/lazy-ledger/scripts/ledger_tool.py import-tsv \
  --ledger ./lazy-ledger.json \
  --input ./rows.tsv
```

Header:

```tsv
occurred_at	type	amount	category	merchant	note	source	confidence
```

Optional columns: `currency`, `tags`, `attachment`, `id`, `method`, `account`, `to_account`.

After import, run `doctor --json`.

## Reply after a write

State what was recorded, the id(s), and any assumption (date defaulted to today, category inferred, paid amount preferred). Then give this month's expense total. Offer the dashboard only if they asked to look or many rows were added.

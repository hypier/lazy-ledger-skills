# Ledger Schema

Use this schema for `lazy-ledger.json`. Unknown fields must be preserved.

## Root

```json
{
  "schema_version": 1,
  "store": "lazy-ledger-docs",
  "store_version": 1,
  "currency": "CNY",
  "created_at": "2026-07-08T12:00:00+08:00",
  "updated_at": "2026-07-08T12:00:00+08:00",
  "transactions": [],
  "categories": [],
  "accounts": [
    {"id": "acc_wechat", "name": "微信零钱", "type": "wechat", "opening_balance": 0}
  ],
  "budgets": [
    {"id": "bud_any_all", "month": null, "category": null, "amount": 8000}
  ],
  "preferences": {
    "merchant_categories": { "星巴克": "咖啡" },
    "merchant_aliases": { "sbk": "星巴克" },
    "default_account_id": "acc_wechat",
    "usage_profile": {
      "summary": "记账默认走微信零钱。支出主要记在餐饮、交通。午饭常记餐饮，金额大约 ¥16。超市金额不固定，缺金额时要问。",
      "defaults": { "account": "微信零钱", "account_id": "acc_wechat", "method": "wechat" }
    }
  },
  "habits": [],
  "bills": []
}
```

`preferences` is optional on old files. The tool creates it when missing. `bills` holds monthly letters (`month`, `title`, `body`). Missing `bills` is filled as `[]`. Canvas HTML snapshots live beside the ledger as `{stem}-bill-YYYY-MM.html`; do not store those paths in this JSON.

The file is a JSON document database, not SQL. Collections are `transactions`, `accounts`, `budgets`, `categories`, `habits`, and `bills`. Each item is a document with an `id`. `store` is `lazy-ledger-docs`. Unknown fields must be preserved.

## Transaction

```json
{
  "id": "tx_20260708_ab12cd34",
  "type": "expense",
  "amount": 38.0,
  "currency": "CNY",
  "category": "咖啡",
  "merchant": "星巴克",
  "method": "wechat",
  "account": "微信零钱",
  "account_id": "acc_wechat",
  "to_account": null,
  "to_account_id": null,
  "note": "昨天星巴克 38",
  "occurred_at": "2026-07-07T12:00:00+08:00",
  "source": "text",
  "confidence": 1.0,
  "tags": ["报销"],
  "created_at": "2026-07-08T12:00:00+08:00",
  "updated_at": "2026-07-08T12:00:00+08:00"
}
```

Required: `id`, `type`, `amount`, `currency`, `category`, `occurred_at`, `source`, `created_at`, `updated_at`.

`type`: `expense` | `income` | `refund` | `transfer`

`source`: `text` | `image` | `voice` | `manual` | `import`

`method` (optional): `wechat` | `alipay` | `cash` | `card` | `bank` | `other`

Account fields:

- `account` / `account_id`: source account. Required in practice for new writes; old rows may omit them.
- `to_account` / `to_account_id`: destination, used by `transfer`

Also optional: `merchant`, `note`, `confidence` (0–1), `tags`, `attachment`.

`amount` is always positive.

## Account

```json
{
  "id": "acc_wechat",
  "name": "微信零钱",
  "type": "wechat",
  "currency": "CNY",
  "opening_balance": 200.0,
  "archived": false,
  "created_at": "2026-07-08T12:00:00+08:00"
}
```

`type`: `cash` | `wechat` | `alipay` | `bank` | `credit` | `other`

Missing `accounts` on an old file is filled with the five defaults. Do not replace an existing list.

## Budget

```json
{
  "id": "bud_2026-08_餐饮",
  "month": "2026-08",
  "category": "餐饮",
  "amount": 2000.0
}
```

`month` null = every month. `category` null = overall expense limit. A month-specific row overrides the template for that category.

## Category

```json
{
  "name": "餐饮",
  "icon": "food",
  "parent": null
}
```

`parent` is null for a first-level category and contains the first-level category name for a second-level category. Only two levels are supported. Transactions store the most specific category name directly; reports roll second-level values into their parent. Merchant habits live in `preferences.merchant_categories`, not here.

## Habit

```json
{
  "id": "hab_午饭",
  "phrase": "午饭",
  "merchant": "食堂",
  "category": "餐饮",
  "type": "expense",
  "amount": 16.0,
  "amount_locked": false,
  "method": "wechat",
  "account": "微信零钱",
  "account_id": "acc_wechat",
  "count": 8,
  "recent_amounts": [16.0, 16.0, 16.0],
  "pinned": false,
  "source": "learned",
  "last_used_at": "2026-08-18T12:00:00+08:00"
}
```

`phrase` is a short user saying (`午饭`, `地铁`, `喜茶`), not a payment-processor legal name. `source` is `learned` or `manual`. `amount` is the usual price when it is stable. Habits are internal signals for parse/add. The agent-facing portrait is `lazy-ledger-memory.md` next to the ledger, refreshed periodically (`habit memory`). `preferences.usage_profile` is the structured cache used to generate that file.

## Habit memory

`{ledger-stem}-memory.md` sits beside the ledger (for `./lazy-ledger.json` that is `./lazy-ledger-memory.md`). Markdown so the agent can Read it. Rewrite when missing, when `tx_count` lags by 10+, when older than 7 days, or on `habit memory` / `habit rebuild`. Do not rewrite on every single add unless those rules fire (the first add creates the file).

## Compatibility

When reading an older ledger:

- Preserve unknown top-level and transaction fields
- Fill missing root fields if safe
- Normalize missing arrays to `[]`
- Do not change existing ids

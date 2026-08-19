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
    "default_account_id": "acc_wechat"
  }
}
```

`preferences` is optional on old files. The tool creates it when missing.

The file is a JSON document database, not SQL. Collections are `transactions`, `accounts`, `budgets`, and `categories`. Each item is a document with an `id`. `store` is `lazy-ledger-docs`. Unknown fields must be preserved.

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
  "id": "cat_food",
  "name": "餐饮",
  "keywords": ["饭", "外卖", "奶茶"],
  "created_at": "2026-07-08T12:00:00+08:00"
}
```

Hints only. Transactions store the category name directly. Merchant habits live in `preferences.merchant_categories`, not here.

## Compatibility

When reading an older ledger:

- Preserve unknown top-level and transaction fields
- Fill missing root fields if safe
- Normalize missing arrays to `[]`
- Do not change existing ids

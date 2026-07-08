# Ledger Schema

Use this schema for `lazy-ledger.json`.

## Root

```json
{
  "schema_version": 1,
  "currency": "CNY",
  "created_at": "2026-07-08T12:00:00+08:00",
  "updated_at": "2026-07-08T12:00:00+08:00",
  "transactions": [],
  "categories": []
}
```

## Transaction

```json
{
  "id": "tx_20260708_ab12cd34",
  "type": "expense",
  "amount": 38.0,
  "currency": "CNY",
  "category": "咖啡",
  "merchant": "星巴克",
  "note": "昨天星巴克 38",
  "occurred_at": "2026-07-07T12:00:00+08:00",
  "source": "text",
  "confidence": 1.0,
  "tags": [],
  "created_at": "2026-07-08T12:00:00+08:00",
  "updated_at": "2026-07-08T12:00:00+08:00"
}
```

Required transaction fields:

- `id`
- `type`: `expense`, `income`, `refund`, or `transfer`
- `amount`: positive number
- `currency`
- `category`
- `occurred_at`
- `source`
- `created_at`
- `updated_at`

Optional transaction fields:

- `merchant`
- `note`
- `confidence`
- `tags`
- `attachment`

## Category

```json
{
  "id": "cat_food",
  "name": "餐饮",
  "keywords": ["饭", "外卖", "奶茶"],
  "created_at": "2026-07-08T12:00:00+08:00"
}
```

Categories are hints, not strict relational entities. Transactions may keep category names directly.

## Compatibility

When reading an older or partial ledger:

- Preserve unknown top-level fields.
- Preserve unknown transaction fields.
- Add missing root fields if safe.
- Normalize missing arrays to `[]`.
- Do not change existing ids.

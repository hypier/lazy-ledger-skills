# Present Ledger Data

Chat is the default. Use the localhost app when the user wants to inspect or edit rows, and static HTML only for an exportable snapshot.

## Choose A Surface

| Request | Action |
|---|---|
| Confirmation after a write | Concise rows plus affected-month totals |
| Spending, income, net, comparison | `show --compare` |
| Year or quarter | `show --range this-year|this-quarter --compare` |
| Budget remaining | `show --compare` or `budget list` |
| Account balances | `account list --json` |
| Recent/search rows | `recent`, `list`, or `find` |
| Open/edit ledger | Follow [html-report.md](html-report.md) and run `serve` |
| Static export | `render` |
| Monthly bill | `bill show`, write from facts, then `bill save` |
| Habit portrait | Follow [habits.md](habits.md) |

Do not paste raw ledger JSON unless requested.

## Spending Summary

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" show \
  --ledger ./lazy-ledger.json \
  --compare
```

Use `--month YYYY-MM`, `--range last-month|last7|last30|this-quarter|this-year`, or `--start / --end` when the user names a period.

Paste the command's markdown without redoing its arithmetic. The generated observations already cover comparison, pace, recurring items, refunds, and review queues. Add prose only when it answers the user's actual question.

For calculations or a custom explanation, use deterministic summary JSON:

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" summary \
  --ledger ./lazy-ledger.json \
  --range this-month \
  --compare \
  --json
```

## Search And Recent Rows

Keep chat output compact and include IDs only when correction or deletion needs them:

```text
最近 2 笔
- 08-18 星巴克 · 咖啡茶饮 · ¥38.00
- 08-17 地铁 · 公共交通 · ¥4.00
```

## Backup And Export

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" backup \
  --ledger ./lazy-ledger.json

python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" export \
  --ledger ./lazy-ledger.json \
  --range this-month \
  --format csv \
  --output ./lazy-ledger.csv
```

`backup` writes a dated full-ledger JSON copy. `export --format json` writes filtered transactions and is not a replacement ledger.

## Monthly Bill

The bill has two layers:

1. Deterministic facts from the ledger.
2. A short agent-written letter based only on those facts.

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" bill show \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --json
```

Follow the returned brief. Write 400-800 Chinese characters, describe what changed, and offer one useful observation without moralizing or inventing amounts.

```bash
python3 "$LEDGER_SKILL_DIR/scripts/ledger_tool.py" bill save \
  --ledger ./lazy-ledger.json \
  --month 2026-08 \
  --title "八月还是把钱花在吃上" \
  --body "……"
```

Reply with the letter and the returned HTML path. `bill render --month YYYY-MM` regenerates the file from current facts and the saved letter.

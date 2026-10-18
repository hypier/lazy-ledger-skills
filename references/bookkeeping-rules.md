# Bookkeeping Rules

Use these rules to infer transaction fields. Workflows live in [record.md](record.md); source reconciliation lives in [reconcile-imports.md](reconcile-imports.md).

## When To Ask

Write the obvious transaction directly. Ask only when:

- No amount is present and no active user-confirmed fixed-price rule applies.
- Several amounts could be the actual total.
- The row could be income, refund, or own-account transfer instead of expense.
- A correction target is ambiguous.
- Choosing an account would materially change balances and the source does not identify one.

## Type And Sign

Store a positive `amount`; reports apply direction from `type`.

- `expense`: spending.
- `income`: salary, interest, side income, reimbursement received.
- `refund`: money returned from an earlier expense.
- `transfer`: movement between the user's own accounts; excluded from income and expense.

Keep a fully refunded purchase as one `expense` plus one `refund`.

Decide the economic event once, then let `type` and `category` follow from it. They are not
independent: an income-only category such as `收入` on an `expense` row is a contradiction,
and `doctor` reports it as `category_type_mismatch`.

### Reimbursement, advance, and loan

Money that is expected back must not be counted as ordinary spending:

- Reimbursement arriving (`报销回来`, `报销了`, `报销到账`) is `income`; it settles an earlier
  advance rather than creating new spending.
- Paying out first (`垫付`, `代付`, `借出`, `借给`) leaves the account as `expense`, but the
  money is owed back. Record it as an advance (`--advance`, or the verb is enough) and keep it
  out of ordinary spending totals; do not silently treat it as a normal purchase.
- A reimbursement request is not income and an advance is not a finished expense. Preserve
  the distinction until the money actually moves back.

Advances carry a state on the row, so they can be queried and closed in a later session:

| State | Meaning | Counts as spending? |
| --- | --- | --- |
| `open` | Owed back, still outstanding | No |
| `settled` | Paid back | Yes, once reimbursed |
| `written_off` | Will not be repaid | Yes |

- `advance list` shows what is still owed; add `--all` to include closed rows.
- A reimbursement equal to exactly one open advance settles it automatically, and the income
  row links back with `relation: reimbursement`. If several advances match, nothing is
  settled — ask the user instead of guessing.
- When the user gives up on the money, `advance writeoff` turns it into real spending.
- `--not-mine` records a row that is not the user's own spending (代付, 帮朋友垫一下) without
  putting it in totals. It is the plain-language form of excluding a row from totals.

### Own-account transfers

Only movement between the user's own accounts is `transfer`:

- `还信用卡`, `提现`, `转到`, `转入`, `内部划转`, or an explicit route such as
  `从微信转100到支付宝`.
- 提现 moves money to a bank account: `微信提现500` is a transfer from 微信零钱 to 银行卡.
  A transfer needs both sides, so pass `--to-account` when the destination is not implied.
- Paying another person (`转账给妈妈`, `转给小王`) is `expense`, not a transfer, even though
  the word 转账 appears.

## Date And Time

- No date: current local time.
- Keep exact source time when visible.
- Support `今天`, `昨天`, `前天`, recent weekday phrases, and explicit month/day forms.
- For a month/day without year, use the current year unless that would place it implausibly far in the future.
- Do not replace an exact payment time with a bank posting date. Store posting date in source metadata.

## Amount

- One amount: use it.
- `实付` or `券后`: use paid amount, not original price.
- `一共` / `合计` / `总计` with several numbers: use the labeled total.
- Multiple independently labeled items: split them.
- Otherwise, ask which amount is the transaction total.
- Fill a missing amount only from an active, explicitly confirmed fixed-price rule in user-profile.md. A statistical habit portrait is not authorization.

## Category

Explicit user choice and `preferences.merchant_categories` win over keyword inference.

Common roots and cues:

- 餐饮: meals, restaurant, takeaway, tea drinks.
- 交通: transit, taxi, rail/air, parking, fuel.
- 购物: ecommerce, supermarket, convenience, clothing, electronics.
- 居住: rent, utilities, property management, broadband.
- 娱乐: movies, games, performances, memberships.
- 医疗: hospital, medicine, registration, health checks.
- 教育: courses, books, tuition, training.
- 人情: gifts, red packets, treating others.
- 理财: insurance, investment, financing fees where the ledger taxonomy defines them.
- 其他: evidence is insufficient; lower confidence.

When several transactions occur within a short time window and share a merchant, counterparty, or description pattern, classify them as a group. If the group strongly points to one purpose, use that purpose for otherwise incomplete rows and record the inference in the note; ask only when the rows conflict or the choice changes income, expense, refund, or transfer semantics.

For a genuinely ambiguous group, produce ranked candidate purposes with confidence and show the proposed choice to the user in one grouped confirmation. Apply the confirmed choice to the group together and preserve the evidence in each row's note.

Use the most specific configured second-level category (for example `咖啡茶饮` under `餐饮`). Reports aggregate it into its parent. Do not create a new top-level category when an existing parent/child relationship expresses the meaning.

## Method, Account, And Channel

- `method`: how the payment was executed (`wechat`, `alipay`, `cash`, `card`, `bank`, `other`).
- `account`: the balance-bearing source account.
- `payment_channel`: optional processor route on imported rows.

Examples:

- WeChat balance payment: method `wechat`, account `微信零钱`.
- WeChat payment funded by 招商银行信用卡(1080): method `card`, that credit account, payment channel `wechat`.
- Credit-card repayment: transfer from the funding account to the credit account.

Prefer a specifically named account over generic `银行卡` or `信用卡`. When an ordinary text entry omits the account, use an explicitly confirmed profile default, or leave the account unassigned. Never choose an account based on its balance.

Tags: `报销` / `对公` -> `报销`; `出差` / `差旅` -> `出差`; `订阅` -> `订阅`.

## Duplicate Boundary

For an ordinary add, same day, type, amount, and same merchant/note is a likely duplicate and requires confirmation.

For imported data, do not rely on this heuristic alone. Authoritative source IDs, exact time, posting date, account, and cross-source linkage decide whether rows should merge. Follow [reconcile-imports.md](reconcile-imports.md).

## Summary Math

- Expense total: rows for which the core `counts_as(row, "expense")` is true; advances and exclusions are not ordinary consumption.
- Income total: rows for which `counts_as(row, "income")` is true; advance repayments are not earnings.
- Refund total: `refund` only.
- Net: income + refund - expense.
- Transfers are listed but excluded from income/expense.
- Always name the date range and compare against an equal previous period.

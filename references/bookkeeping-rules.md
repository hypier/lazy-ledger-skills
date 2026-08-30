# Bookkeeping Rules

Use these rules to infer transaction fields. Workflows live in [record.md](record.md); source reconciliation lives in [reconcile-imports.md](reconcile-imports.md).

## When To Ask

Write the obvious transaction directly. Ask only when:

- No amount is present and no stable habit applies.
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

## Date And Time

- No date: today at local noon.
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
- Fill a missing amount only from the stable-amount section of the current habit portrait.

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

Use the most specific configured second-level category (for example `咖啡茶饮` under `餐饮`). Reports aggregate it into its parent. Do not create a new top-level category when an existing parent/child relationship expresses the meaning.

## Method, Account, And Channel

- `method`: how the payment was executed (`wechat`, `alipay`, `cash`, `card`, `bank`, `other`).
- `account`: the balance-bearing source account.
- `payment_channel`: optional processor route on imported rows.

Examples:

- WeChat balance payment: method `wechat`, account `微信零钱`.
- WeChat payment funded by 招商银行信用卡(1080): method `card`, that credit account, payment channel `wechat`.
- Credit-card repayment: transfer from the funding account to the credit account.

Prefer a specifically named account over generic `银行卡` or `信用卡`. When an ordinary text entry omits the account, use the current habit default.

Tags: `报销` / `对公` -> `报销`; `出差` / `差旅` -> `出差`; `订阅` -> `订阅`.

## Duplicate Boundary

For an ordinary add, same day, type, amount, and same merchant/note is a likely duplicate and requires confirmation.

For imported data, do not rely on this heuristic alone. Authoritative source IDs, exact time, posting date, account, and cross-source linkage decide whether rows should merge. Follow [reconcile-imports.md](reconcile-imports.md).

## Summary Math

- Expense total: `expense` only.
- Income total: `income` only.
- Refund total: `refund` only.
- Net: income + refund - expense.
- Transfers are listed but excluded from income/expense.
- Always name the date range and compare against an equal previous period.

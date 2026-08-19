# Bookkeeping Rules

Field inference for casual Chinese input. Workflows live in [record.md](record.md).

## Minimal friction

Do the obvious write when amount and meaning are clear. Prefer `add --text` so parsing, duplicate checks, and category defaults run together. Use explicit `--amount` / `--category` / `--merchant` / `--date` only to override or for non-text sources.

Ask only when:

- No amount is present, and no matching habit has a usual amount
- Several amounts could be the transaction total (`一共`, two prices with no 实付)
- The entry may be income / refund / transfer rather than expense
- A correction target is ambiguous

## Types

Store a positive `amount` for every type. Reports apply the sign.

- `expense`: spending, default
- `income`: salary, reimbursement received, interest, side income
- `refund`: money returned from an earlier expense
- `transfer`: movement between own accounts; listed but excluded from spend totals

## Dates

- No date → today at 12:00 local
- Keep explicit dates and times
- `今天` / `昨天` / `前天`
- `上周五`, `周一` / `这周一` (most recent that weekday, including today)
- `7月7日`, `7月7号`, `7/7` (current year; if the date is far in the future, use last year)

## Amounts

- One amount → that amount
- No amount, but a short stable phrase matches the usage portrait (`午饭`) → that amount, slightly lower confidence
- `原价45 实付38` or `券后` → paid amount, drop 原价
- `一共` / `合计` / `总计` with several numbers → last number
- Newline or `；` → separate transactions
- `午饭26 晚饭38` → two expenses
- Otherwise several amounts on one line → ask

## Category

User `preferences.merchant_categories` wins. Then keywords:

- 咖啡: 星巴克, 瑞幸, 咖啡, 拿铁, 美式
- 餐饮: 饭, 午饭, 晚饭, 早餐, 外卖, 火锅, 奶茶, 餐厅, 美团, 饿了么
- 交通: 地铁, 公交, 打车, 滴滴, 高铁, 火车, 机票, 停车, 加油
- 购物: 淘宝, 京东, 拼多多, 超市, 便利店, 衣服, 数码
- 居住: 房租, 水电, 燃气, 物业, 宽带
- 娱乐: 电影, 游戏, 演出, KTV, 会员
- 医疗: 医院, 药, 挂号, 体检
- 教育: 课程, 书, 学费, 培训
- 人情: 红包, 礼物, 请客
- 收入: 工资, 奖金, 报销, 利息
- 其他: cannot infer; lower confidence

Explicit user category beats merchant inference. `prefer` and `update --category` remember the merchant map. The usage portrait fills missing account / method, and fills amount only for short stable phrases. Long company names are not amount defaults.

## Method, account, tags

Infer when the text names them; leave unset otherwise.

- method: 微信 → `wechat`; 支付宝 / 花呗 → `alipay`; 现金 → `cash`; 信用卡 / 刷卡 → `card`; 银行卡 → `bank`
- account: map method/name onto ledger accounts. Default new expenses to `preferences.default_account_id` (微信零钱)
- transfer route: `从微信转到支付宝 500`, `微信转支付宝`, `还信用卡`
- tags: 报销 / 对公 → `报销`; 出差 / 差旅 → `出差`; 订阅 → `订阅`

## Screenshots

- Do not force receipt vs payment-history classification
- Visible paid amount wins over original price
- Multiple visible rows: ask all vs which one, unless they already said import all
- Truncated merchants stay truncated; lower `confidence`

## Duplicates

Likely duplicate = same amount, same day, and same merchant or very similar note (or same attachment). Ask before adding. CLI refuses unless `--allow-duplicate`.

## Summaries

- Expense total: `expense` only
- Income total: `income` only
- Refund total: `refund` only
- Net = income + refund − expense
- Transfers listed, excluded from expense/income
- Always name the date range
- `show` / `summary --compare` contrast with the previous equal period

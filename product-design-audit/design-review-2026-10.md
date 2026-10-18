# Lazy Ledger 功能设计评估

日期：2026-10-18
范围：整体功能设计（事件建模、自动化边界、人机职责划分、术语与交互），不含代码 bug 排查。

评估方法：阅读全部 SKILL.md / 9 份 references / CLI 与 server 实现，并对解析器、
总额计算、doctor、audit、退款关联、账户余额做实测取证。测试基线：
`python3 -m unittest discover -s tests` → **80 项通过，1 跳过**，代码工程层面健康。

---

## 结论摘要

这套系统的核心错位是：**它把"会计语义正确性"的整体责任交给了模型在对话里临场判断，
而 CLI 只做字段搬运和机械校验。** 一旦模型判错，系统不仅记错，还会报告一切正常。

最严重的实例（实测）：`报销回来300` 让本月支出从 138 涨到 438、收入仍为 0，
而 `doctor --json` 返回 `ok: true, issue_count: 0`。错误数据静默污染所有报表。

问题按严重度分为五类：

| # | 问题 | 严重度 | 是否静默 |
|---|---|---|---|
| 1 | type 与 category 两套语义互不校验 | P0 | 是，doctor 漏报 |
| 2 | 报销/垫付/借出等经济事件被误判为支出 | P0 | 是 |
| 3 | transfer 识别范围过窄 | P1 | 部分（confidence 0.75 可提示） |
| 4 | 退款启发式匹配成功却不落关联 | P1 | 是 |
| 5 | review 队列不持久化，状态机放在对话里 | P2 | 会话漂移 |
| 6 | 实现术语（excluded_from_totals）泄漏给用户 | P2 | 认知负担 |

---

## 一、P0：type 与 category 是两个互不校验的语义系统

### 现象

实测解析结果：

| 输入 | type | category | confidence | 是否正确 |
|---|---|---|---|---|
| `报销回来300` | expense | 收入 | 1.0 | ✗ 自相矛盾 |
| `报销了500` | expense | 收入 | 1.0 | ✗ 自相矛盾 |
| `垫付800` | expense | 其他 | 0.75 | ✗ 应为应收 |
| `借给小王200` | expense | 其他 | 0.75 | ✗ 应为应收 |

`报销回来300` 的总额污染链条：

```
type: expense     ← 判成支出
category: 收入     ← 却归到收入分类
→ summary: 支出 300、收入 0、净额 -300
→ doctor: ok=true, issue_count=0
```

### 根因

`ledger_tool.py` 中存在两套彼此独立的词汇表，各自推断后直接拼装：

```python
TYPE_KEYWORDS = {
    "income":   ["工资", "奖金", "收入", "利息", "报销到账"],   # 只认"报销到账"
    "refund":   ["退款", "退回", "返现"],
    "transfer": ["还信用卡"],
}
CATEGORY_KEYWORDS = {
    "收入": ["工资", "奖金", "报销", "利息"],                    # 认"报销"
    ...
}
```

用户在自然语言里几乎永远说「报销回来」「报销了」，不会说「报销到账」。
于是这类输入落进 `expense` + `收入` 的组合——**类型说这是支出，分类说这是收入**。

`doctor` 漏报的原因：它只校验字段合法性（类型枚举、金额正负、账户存在、关系引用），
**不校验 type 与 category 的语义相容性**。所以这个矛盾组合被判定为健康。

### 设计建议

不要让 type 和 category 各自独立推断后拼装，应收敛为**单一"经济事件"判定**，再派生字段：

| 经济事件 | type | 计入支出 | 是否产生应收/应付 |
|---|---|---|---|
| 消费 | expense | 是 | 否 |
| 报销到账 | income | 否 | 核销原应收 |
| 垫付 / 借出 | expense + 应收标记 | 否 | 是 |
| 提现 / 还款 / 内部划转 | transfer | 否 | 否 |
| 退款 | refund | 否 | 否 |

配套要求：
1. **把 type/category 相容性写进 doctor 作为会计不变量。** 收入类分类配 expense 类型
   应报结构错误。当前静默污染报表是最危险的行为——第一步先让错误可见。
2. 扩充 `TYPE_KEYWORDS`，纳入「报销回来 / 报销了 / 报销打款 / 垫付 / 借出 / 应收」。
3. `收入` 这个分类名本身与 type 同名，建议改为 `工资收入`、`报销收入` 等具体名，
   消除同名歧义。

---

## 二、P0：报销与垫付语义缺失

与问题一同源。实测 `垫付800` → `expense/其他`，`借给小王200` → `expense/其他`。

这两类都不是消费：
- **垫付**：公司会还，是应收款
- **借出**：对方会还，是应收款

当前它们被记为普通支出，直接虚增支出总额，且「待报销」只能靠 tags 猜：

```python
REIMBURSE_TAGS = {"报销", "待报销", "对公"}
REIMBURSED_TAGS = {"已报销", "报销已回", "报销到账"}
def pending_reimbursements(transactions):   # 靠 tags 交集判断
```

### 设计建议

把「垫付/待报销」提升为一等公民的**状态**，而非依赖用户手动打 tag：

- 记账时识别出垫付 → 自动标记 `垫付中`，产生应收
- 报销到账时 → 自动核销对应应收，而不是新建一条 income 完事
- 账本的「待报销」队列由状态派生，不再依赖 tag 命中

---

## 三、P1：transfer 识别范围过窄

实测：

| 输入 | 结果 | 应为 |
|---|---|---|
| `还信用卡2000` | transfer ✓ | transfer |
| `微信提现500` | expense/其他 | transfer |
| `从微信转100到支付宝` | expense/其他 | transfer |
| `转账给妈妈500` | expense/人情往来 | expense（正确，是给人不是给自己） |

`事实上 infer_type` 只认字面 `还信用卡`；`infer_transfer_route` 依赖一个严格的
`TRANSFER_ROUTE_RE` 正则，要求「账户词 + 转到/转给/转入/→/到 + 账户词」的完整形式。
`从微信转100到支付宝` 因中间夹了金额而未被匹配。

### 设计建议

1. 把 `提现`、`转到`、`转入`、`内部划转` 纳入 transfer 触发词。
2. 让路由正则容忍金额穿插（`从X转<金额>到Y`）。
3. 保留现有正确行为：`转账给<人>` 是 expense/人情，不是 transfer——**自己账户之间才是 transfer**，
   这是关键判据，应在文档里写明。

---

## 四、P1：退款启发式匹配成功却不落关联

### 现象

实测：记入 `昨天微信星巴克38`（expense）+ `今天星巴克退款38`（refund）后：

```
refund  38.0  related= None  relation= None
expense 38.0  related= None  relation= None
```

两行**没有建立任何关联**。而账本文件里 `related_transaction_id` 长期为空。

### 根因

`unmatched_refunds()` 的判定逻辑是：如果退款能在 90 天内找到金额相近、商户相容的支出，
就设置 `matched = True` 并 **break** —— 然后什么都不做，不写关联、不提示。

```python
if amount_ok and merchant_ok:
    matched = True
    break
...
if not matched:
    rows.append(compact_candidate(refund))   # 只有匹配失败才提示
```

结果是行为恰好反了：
- **匹配得上** → 静默通过，关联永远不落库
- **匹配不上** → 提示用户手动处理

自动化用在了最不需要的地方，而真正该落库的关联反而空着。这直接解释了为什么
README 里「退款自动挂回原单」至今仍列在**规划中**——它事实上从未闭环。

### 设计建议

启发式匹配成功后，**自动写入 `related_transaction_id` + `relation: refund`**。
这是可逆的、不改金额/类型/账户的操作，完全符合 `proactive-review.md` 已有的
"明确即自动应用"原则。回复里一行带过即可。

只有金额或商户存在实质性歧义（多个候选、金额差异大）时才提问。

---

## 五、P2：review 队列不持久化，状态机放在对话里

### 现象

`grep -rn "review_queue\|pending_issues" scripts/ SKILL.md references/` → **0 命中**。

但文档要求模型维护一个隐式状态机：

> Keep a review queue internally, but expose only its highest-priority unresolved item.
> Handle groups sequentially: ask one confirmation question, wait for the answer,
> apply only that group, rerun the relevant check, and then present the next
> highest-priority group.

这套状态机放在对话里，长会话必然漂移：忘记队列、跳过重跑、一次问两组、
跨会话完全丢失。

### 设计建议

把 review 队列做成**账本里的持久状态**（如 `review_queue` 集合或 `pending_issues`），
由 CLI 提供确定性的 `review next` / `review resolve` 动词。
模型只负责"解释和确认"，不负责"记住队列"。这同时解决跨会话一致性问题。

---

## 六、P2：实现术语泄漏给用户

`excluded_from_totals` 在文档与代码中出现 **28 次**，且直接写进 SKILL.md 的
"会计不变量"一节。用户说的永远是「这笔是垫付的」「公司会还我」「这个不算我花的」。

更深的问题是这个布尔值把**两件不同的事**压成了一个字段：

1. 这笔钱不是我的消费（报销、垫付、代付）——有明确经济含义，应是一等公民的状态
2. 这笔钱我决定不算（历史数据、忽略）——才是真正的"排除出统计"

现在两类混在一起，导致「待报销」靠 tags 猜、「未对上退款」靠启发式猜、
`excluded_from_totals` 靠用户显式标记——**三个相关机制彼此不通信**。

### 设计建议

- 在 CLI 层做**用户语言 → 内部字段**的映射（垫付/待报销/不算我花的）
- 把第 1 类提升为事件状态，第 2 类保留为显式排除
- SKILL.md 面向模型描述时用语义说法，避免暴露存储字段名

---

## 值得保留的设计（不要改坏）

- **正面金额 + type 决定方向**的建模 —— 正确，避免符号错误
- **权威引用优先级**（transaction ID > 时间戳 > 模糊匹配）与"日期金额相同永不删除" —— 审慎正确
- **JSON document store + 保留未知字段** —— 个人记账规模下完全够用，无需引入 SQL
- **文件锁 + 原子写 + 0600 权限 + localhost-only mutation 校验** —— 安全模型到位
- **先副本预演、再备份、后落盘**的导入流程 —— 正确
- **退款检测的 90 天/金额/商户启发式** —— 判据本身合理，问题只在于匹配后没有动作

---

## 建议实施顺序

| 优先级 | 行动 | 理由 |
|---|---|---|
| **P0** | 把 type/category 相容性纳入 `doctor` 校验 | 先让错误可见；当前静默污染最危险 |
| **P0** | 扩充经济事件识别：报销回来/报销了/垫付/借出 | 直接虚增支出，实测已复现 |
| **P1** | 扩展 transfer 识别（提现、转到、金额穿插） | 常见口语漏判 |
| **P1** | 启发式匹配成功后自动写 `related_transaction_id` | 让退款关联真正闭环 |
| **P2** | review 队列持久化 + `review next/resolve` | 消除会话漂移 |
| **P2** | 用户语言映射替代 `excluded_from_totals` 暴露 | 降低认知负担 |

---

## 附：实测复现步骤

```bash
export LEDGER_SKILL_DIR=/path/to/lazy-ledger-skills
export LAZY_LEDGER_FILE=/tmp/leval.json && rm -f /tmp/leval.json

# P0 证据：报销被记为支出，doctor 不报错
$LEDGER_SKILL_DIR/scripts/ledger add --text "报销回来300"
$LEDGER_SKILL_DIR/scripts/ledger summary --range this-month --json
#   → totals: {expense: 300.0, income: 0.0, net: -300.0}
$LEDGER_SKILL_DIR/scripts/ledger doctor --json
#   → {ok: true, issue_count: 0}

# P1 证据：退款匹配成功但关联为空
$LEDGER_SKILL_DIR/scripts/ledger add --text "昨天微信星巴克38"
$LEDGER_SKILL_DIR/scripts/ledger add --text "今天星巴克退款38"
python3 -c "import json; [print(t['type'], t.get('related_transaction_id')) \
  for t in json.load(open('/tmp/leval.json'))['transactions']]"
#   → refund None / expense None
```

解析器行为对照表（复现用）：

```bash
for t in "报销回来300" "垫付800" "借给小王200" "微信提现500" \
         "从微信转100到支付宝" "还信用卡2000"; do
  $LEDGER_SKILL_DIR/scripts/ledger parse --text "$t"
done
```

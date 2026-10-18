# 顶层功能设计：简化与智能化评估

日期：2026-10-18 · 版本：1.4.0 · 视角：整体架构，不涉及代码 bug

---

## 一、当前形状

| 指标 | 数值 |
|---|---|
| CLI 动词 | 24 个顶层 + 7 个分组（review/advance/habit/account/budget/category/prefer） |
| 账本集合 | 7 个（transactions/accounts/budgets/categories/habits/bills/review_queue） |
| SKILL.md | 126 行，约 7 个章节 |
| references/ | 9 份，796 行 |
| ledger_tool.py | 4,960 行 |

---

## 二、六个顶层设计问题

### 问题 1：语义判定仍是「关键词平铺」，热路径先于结构

`classify_event` 是整个系统最重要的判断点——它决定 `type`、`category` 和状态标记。但它实际是**有序的关键词 if/elif 链**（L1398-1443）：

```
报销关键词 → 红包收 → 红包发 → 垫付 → 转账路由 → TYPE_KEYWORDS → TRANSFER_VERBS → 分类关键词
```

问题不在于顺序对不对，而在于：

- **关键词表是扁平的字符串列表**，`CATEGORY_KEYWORDS` 里 `"收入": ["工资", "奖金", "报销", "利息"]`，而 `"报销"` 同时是 `REIMBURSE_INCOME_KEYWORDS` 和 `TAG_KEYWORDS["报销"]` 的成员。同一个词在三个表里承担三种角色。
- **判定与解释分离**：`classify_event` 只返回 `(type, category, flags, confidence)`，不返回「为什么」。助手想解释"为什么这是收入"，只能自己再猜一遍。
- **置信度是手写常数**（0.95 / 0.9 / 0.7 / 1.0），与证据强度无关。

### 问题 2：状态是散落的布尔位，没有统一的生命周期模型

一笔钱在账本里可能同时是：

| 字段 | 含义 | 谁在用 |
|---|---|---|
| `type` | expense/income/refund/transfer | 所有报表 |
| `advance` | open/settled/written_off | advance 命令、summary |
| `excluded_from_totals` | 布尔 | summary、doctor |
| `related_transaction_id` + `relation` | refund / reimbursement | 报表关联 |
| `tags` 含 `报销` | 旧式报销标记 | pending_reimbursements |

这五个字段**表达的是同一维度的不同片段**：这笔钱的"经济状态"。现在的后果是：

- `advance list` 要同时看 `advance == "open"` **和** `tags & REIMBURSE_TAGS` 两套标记
- `summary` 要单独判断 `advance == "open"`、`excluded_from_totals`、`type`
- `pending_reimbursements` 和新加的 `open_advances` 是**两个几乎重复的函数**

### 问题 3：命令面按「技术实现」而非「用户意图」切分

24 个动词里，真正对应高频用户意图的只有少数：

| 用户说的话 | 现在的路径 |
|---|---|
| "我昨天吃了38块的午饭" | `add` ✅ 直接 |
| "微信账单帮我导一下" | `shot prepare` → 确认 → `import-tsv`（3 步） |
| "这钱我帮别人垫的" | `add --not-mine` 或 `add --advance`（要选） |
| "那笔钱还我了" | 得先 `advance list` 拿 id → `advance settle --id` |
| "看看这个月" | `show` ✅ 直接 |
| "有什么问题吗" | `review refresh` → `review next` → `review resolve` |

**"还我了"这条路径是断的**：用户不会说"settle transaction tx_20261018_xxx"。系统的 `add` 动词已经能识别"报销回来800"并自动核销，但前提是**金额恰好相等且唯一**。用户说"垫的500还我了"如果匹配不上，助手得走三个命令。

### 问题 4：确认策略写了三遍，标准不一致

SKILL.md 中关于"什么时候问、什么时候直接做"的规则分散在：

1. `## AI-led entry` — 「Ask one focused question when competing interpretations would change balances」
2. `## Proactive review loop` — 「apply a reversible field update automatically... Ask before those financial-meaning changes」
3. `## Interaction Defaults` — 「**Ask only when** amount, type, or correction target is materially ambiguous」

三处的抽象层级不同，**边界也不完全一致**。第 2 条说"改类型/金额/账户/日期必须先问"，第 3 条说"只在真正歧义时问"。

### 问题 5：references/ 按「任务类型」切分，导致同一概念跨文件

9 份 reference 是**按场景**切的（record / habits / image-batch / reconcile / present / html-report / ledger-schema / bookkeeping-rules / proactive-review），但**概念是横切的**：

- "报销/垫付" 出现在 `bookkeeping-rules.md`（会计规则）和 `proactive-review.md`（如何复核）
- "转账" 出现在 3 个文件
- "退款关联" 出现在 `bookkeeping-rules.md` 和 `proactive-review.md`

助手要回答"垫付该怎么处理"，得读两个文件才能拼出完整答案。

### 问题 6：智能化停留在「解析器」层，没有「记忆」层

系统花了大量代码做 `habits`（习惯画像）、`prefer`（商户偏好）、`memory.md`，但这些都是在**单条记账**上做个性化。更高层次的智能——**这个用户的账本长什么样、什么反常、什么该提醒**——仍然散落在 `detect_recurring`、`doctor`、`audit`、`review` 四处，互不通气。

---

## 三、简化方向（按收益排序）

### S1. 把语义判定从「关键词链」升级为「事件对象」

**现状**：`classify_event(text) → (type, category, flags, confidence)`

**建议**：返回一个**带解释的事件对象**：

```python
{
  "event": "reimbursement_arrival",   # 事件类型，不是会计类型
  "type": "income",
  "category": "收入",
  "state": None,
  "evidence": [{"rule": "REIMBURSE_INCOME_KEYWORDS", "match": "报销回来", "weight": 0.95}],
  "confidence": 0.95,
}
```

好处：
- **可解释**：助手能直接说"因为出现了'报销回来'，所以记为收入"
- **可扩展**：新增事件类型只需加一个 rule，不用在 if/elif 链里找位置
- **置信度有来源**：不再是魔法常数

这是**最有价值的一项**，因为它把整个系统的"判断核心"从一堆 if 变成可检查、可解释的数据结构。

### S2. 把五个状态字段收敛成一条「经济状态」轴

**现状**：`type` + `advance` + `excluded_from_totals` + `relation` + `tags`

**建议**：显式的状态机——

```
   ┌─────────┐  钱出去，预期回来   ┌────────┐  还回来了   ┌──────────┐
   │ normal  │ ─────────────────→ │ advance│ ──────────→ │ settled  │
   └─────────┘                    └────────┘             └──────────┘
        │                              │
        │  不是我花的                   └── 要不回来了 ──→ written_off
        ↓
   not_mine
```

派生规则统一在一处：
- `normal` → 计入支出
- `advance` → 不计入支出，进待收
- `settled` → 计入支出（钱确实花了）
- `written_off` → 计入支出（坏账也是损失）
- `not_mine` → 不计入任何合计

这样 `summary`、`advance list`、`pending_reimbursements` 都只读**一个字段**。

### S3. 合并重复函数，建立单一「待办」入口

**现状**：`pending_reimbursements()`、`open_advances()`、`unmatched_refunds()`、`build_review_queue()` 四个函数。

**建议**：统一为 `open_items(ledger) → [ {kind, tx, action, why} ]`，`review next` 和 `advance list` 都从它取数。

### S4. 让「还我了」成为一等动词

**现状**：`advance settle --id` 要求用户先知道 id。

**建议**：`ledger settle --text "垫的500还我了"`——走和 `add` 同样的语义解析，识别出"还款事件"后自动找匹配的垫付并核销；找不到就列入待确认。

这**不新增 CLI 概念**，只是把 `add` 的解析能力复用到核销上。用户意图 → 命令的映射从 3 步降到 1 步。

### S5. 确认策略收敛为一张判定表

把三处规则合成一张表，放在 SKILL.md 顶部：

| 变更类型 | 默认动作 |
|---|---|
| 补字段（缺商户/缺备注） | 直接做，报告 |
| 改分类（同父类内） | 直接做，报告 |
| 改分类（跨父类） | 问 |
| 改 type / 金额 / 账户 / 日期 | 问 |
| 删除 / 合并 | 问 |
| 新增关联（退款、核销） | 唯一匹配直接做，多候选问 |

### S6. references/ 重组为「概念」而非「场景」

建议从 9 份收敛为 5 份：

| 新文件 | 合并自 |
|---|---|
| `semantics.md`（经济事件语义 + 状态机） | bookkeeping-rules + 部分 proactive-review |
| `recording.md`（记账、解析、习惯、别名） | record + habits |
| `importing.md`（截图 + 官方账单 + 对账） | image-batch-import + reconcile-imports |
| `reviewing.md`（复核、doctor、audit、队列） | proactive-review + 部分 reconcile |
| `presenting.md`（show/bill/HTML/本地页面） | present + html-report + ledger-schema |

---

## 四、智能化方向

### I1. 事件解释（依赖 S1）

`parse` 和 `add` 返回 `evidence`，助手能说出「我按 X 判成 Y，因为 Z」。当前用户看不到判定理由。

### I2. 跨维度异常检测

现在 `doctor` 查结构、`audit` 查重复、`detect_recurring` 查周期——三者不共享上下文。建议一个 `insights` 动词统一输出：

```
- 本月餐饮比上月高 40%，主要来自外卖（+6 笔）
- 有 3 笔未结垫付，最早 45 天前
- 「星巴克」这个月出现 12 次，比平常多一倍
```

### I3. 主动核算而未主动问

现在复核是「发现问题 → 问用户」。更智能的是「发现问题 → 能自证的直接修 → 只说结果」。S5 的判定表就是这个方向。

### I4. 账本层面的健康度

给账本一个可查询的「健康状况」：字段完整率、未关联比例、待收账龄、分类覆盖率。这比 `doctor` 的二元 pass/fail 更有信息量。

---

## 五、建议的实施顺序

| 顺序 | 项目 | 理由 |
|---|---|---|
| 1 | **S2 状态机收敛** | 数据模型的地基，后续所有简化依赖它 |
| 2 | **S5 确认判定表** | 纯文档改动，立刻减少助手行为不一致 |
| 3 | **S1 事件对象** | 改动集中在一个函数，收益是全局可解释性 |
| 4 | **S3 统一待办入口** | 依赖 S2，能删掉重复函数 |
| 5 | **S4 settle 动词** | 依赖 S3，直接改善高频路径 |
| 6 | **S6 references 重组** | 纯文档，但工作量大，最后做 |
| 7 | I1–I4 智能化 | 依赖 S1/S2 |

---

## 六、不建议做的

- **不要引入数据库**。JSON 文档库 + 文件锁在这里是正确的选择，迁移成本远超收益。
- **不要合并 add 和 import-tsv**。两者证据来源和确认流程本质不同。
- **不要给 24 个动词做统一 UI**。动词多不是问题，问题是**高频意图的路径不短**。先修路径（S4），别砍动词。

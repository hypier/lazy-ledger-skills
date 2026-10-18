# Lazy Ledger

以智能管家为主的本地记账 skill：用户提供事实，管家负责入账、对账、预算、分析和报告。
当前分支实现 3.0 的账务基础及提案、导入、扩展、同步和报告流程，保留旧 CLI 并逐步移出单体业务代码。
完整范围及未完成阶段见 [执行计划](product-design-audit/execution-plan-2026-10.md)。

## 本轮实现

- 共享领域内核：十进制金额、余额、汇总、预算与收支判定；存储不再反向依赖 CLI。
- 独立用户画像：每次加载账本读取 `user-profile.md`。已确认规则与统计观察分开；无默认付款账户，不根据余额换账户，不从两次重复价格推定固定金额。
- 一致写入：文件锁、修订与读指纹检查、画像/能力冲突检查、可恢复多文件事务、行变更记录；add/record 支持幂等键。
- 结算：明确还款证据、实际收入关联、部分收回、剩余金额在核销当期计损失；相关分录拒绝单独破坏性修改。
- 统一上下文：`context`、`profile show/apply`、`schema show/register`、`migrate`。
- 可选 Git：账本独立仓库、本地快照、显式远程配置、前台同步、独立记录三方合并、冲突选择与整本恢复；网络等待不占用账本写锁。
- 通用提案：校验后一次提交、字段来源、幂等回执和定向撤销；旧提案不能覆盖新数据。
- 导入对账：微信/支付宝 CSV 与规范化 TSV、来源交易号去重、跨来源关联、笔数/金额核对；歧义先进入计划。
- 用户扩展：代码独立存放、版本登记、样例验证、摘要绑定和本机启用；macOS OS 沙箱运行，输出仍须经核心提案提交。
- 结构演进：可选字段改名/类型转换的预览、备份、指标一致性检查与可恢复提交。
- 复核状态：待处理、待补充、解决、忽略、暂缓；按证据重开，通知回执去重。
- 展示按需：无模板的 Markdown/JSON/静态 HTML 报告，归档保存数据快照和依据；旧三个视图仅为兼容入口。
- 专业职责按任务加载，普通记账无需多 agent 并行。

## 使用与开发

```sh
export LAZY_LEDGER_FILE=/path/to/personal/ledger.json
scripts/ledger init --git-mode undecided
scripts/ledger context --task record
scripts/ledger record --text "午饭28" --operation-id unique-request-id
scripts/ledger show --range this-month
scripts/ledger history configure --mode local
scripts/ledger history list
```

创建账本时由管家询问 Git 选择；未回答时不初始化 Git、不上传。
远程设置需要明确目标及已核验的私有可见性；不自动创建仓库。
账本使用 `<文件名主体>-runtime/` 隔离画像、能力和本机设置。
正式账本应放在 skill 安装目录以外。

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/package_skill.py --validate-only
python3 scripts/package_skill.py /tmp/ledger-package
```

测试使用临时合成账本；HTTP 测试需要本机监听权限。分发校验应覆盖实际
解压后的运行，不能只依赖 frontmatter 与文件存在检查。

## 边界与后续工作

扩展运行目前仅支持 macOS `sandbox-exec`，没有可用隔离器就拒绝执行；不安装
第三方依赖。动态字段迁移限于不影响账务口径的 `x_` 元数据。同步遇到相同来源
但不同记录 ID 时拒绝重复合并，需要先显式对账；远程建仓、自动核验私有可见性、
新设备自动引导和后台调度尚未实现。源附件、报告和本机授权不在 Git 快照范围。

旧 CLI 中仍有业务流程需要继续迁入服务层，旧 HTML 只保留兼容用途。
不同银行/XLSX/PDF 格式及更多截图样例仍需单独适配。完整阶段状态见执行计划，
不将这些兼容路径视为全部重构完成。

本轮验证：199 项回归，198 通过、1 项缺少原有截图样例跳过；包含 HTTP、
真实 macOS 隔离、双设备本地远程同步，以及移除 HTML 资源后的安装包端到端流程。
最终导入边界修正后，43 项相关流程定向复验全部通过；远程快照账户校验
补充后，7 项历史恢复/同步复验全部通过。

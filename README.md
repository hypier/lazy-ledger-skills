# Lazy Ledger

本文件记录这个 skill 后续要补的能力，方便按同一方向继续迭代。

## 目标

把本地 JSON 记账做成更顺手的个人记账系统：

- 记账时更懂用户习惯
- 对话里能看懂账，页面里能改账
- 数据用文档库保存，不引入 SQL

## 已落地

- 记账：多笔文本、支付渠道、实付优先、微信/支付宝堆叠粘贴、商户习惯 / 别名、从流水总结使用画像，并定期写入 `lazy-ledger-memory.md` 给助手读
- 长截图导入：`ledger shot prepare` 切片 + 本机 OCR → 先出核对表，用户确认后再 `import-tsv`
- 展示：`show` 对话摘要、对比上期、待复核 / 偏大支出；HTML 月历热力、商户排行、每日支出、筛选导出
- 智能报表：本季 / 今年、按日均推算月底或全年、周期账识别、待报销、未对上的退款、月度走势、账本观察
- 账户：默认五账户、转账从哪到哪、余额 = 期初 ± 流水、`account list/add/update`；本地页面可改期初和默认账户
- 预算：按月总额或分类限额、模板可每月复用、`show` 和页面显示剩余 / 超支 / 预计超支
- 本地页面：四个 Tab（记账 / 报表 / 月度账单 / 账本）。报表用 SVG 饼图、折线、柱状图；月度账单由助手写成文章，并另存为每月一份 Canvas HTML（`lazy-ledger-bill-YYYY-MM.html`）
- 备份：`backup` 复制一份带日期的 JSON
- 文档库：`data/ledgers/default/ledger.json` 作为本地 JSON document store（collections：transactions / accounts / budgets / categories / bills），带文件锁

## 工程约定

- 统一入口：`scripts/ledger`。它自己解析 skill 目录并补 `--ledger`，所以文档和调用都写 `ledger add --text "..."` 这种短命令，不重复写解释器路径。未知子命令原样转发给 `ledger_tool.py`，新增 CLI 动词不用改封装。
- 账本路径：环境变量 `LAZY_LEDGER_FILE` 优先，其次是 `LAZY_LEDGER_HOME/ledgers/default/ledger.json`，否则使用工作目录下的 `./data/ledgers/default/ledger.json`。`ledger which` 可打印当前解析结果。
- 每个账本实例独立保存正式数据、备份、记忆和报表：`ledgers/<name>/ledger.json`、`backups/`、`memory/`、`reports/`。
- 打包：`python3 scripts/package_skill.py [--validate-only] [输出目录]`。先校验 frontmatter、目录名一致性、reference 链接是否存在、入口可执行，再按 `.skillignore` 过滤输出到 `dist/lazy-ledger.skill`。和通用打包脚本不同的是它会读 `.skillignore`，避免把账本数据打进分发包。
- 运行时数据（账本 JSON、备份、`*-memory.md`、生成的报表 HTML/CSV、TSV）既不进 git 也不进分发包，规则集中在 `.gitignore` 和 `.skillignore`，两处的运行时数据段落应保持一致。
- 测试：`python3 -m unittest discover -s tests -p "test_*.py"`。

## 规划中的功能

- 微信 / 支付宝官方 CSV 一键导入
- 退款自动挂回原单
- 自定义分类表

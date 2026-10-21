# 3.0 重构验收记录

日期：2026-10-18。分支：`codex/ledger-butler-refactor`。续作基线：`a16bd8c`。

## 实现与兼容

`scripts/ledger` → `ledger_tool.py` 参数分发 → adapters 输出适配 → services 业务 → domain 口径、storage 事务。
HTTP 调用相同的返回值服务，不捕获 CLI stdout。3.0 初版曾保留 `ledger_db.py` 与 Python API 兼容导出；2026-10-18 已移除，CLI、HTTP 和测试均直接引用核心模块。当前 Python 调用路径见 README 的开发说明。

`add/record/update/delete`、账户、分类、习惯、预算、垫付、导入、复核与月报入口保留。`import-tsv` 接入共享导入计划，权威来源重复执行不再增账。图片来源必须使用已获用户核对的 `--confirmed-image`。`render`、`bill render`、`serve` 输出新报告；旧编辑页面、画布导出和 `--template` 退休，旧历史报告文件不删除。

用户输入、明确规则、解析结果及纠错分别保存字段证据。业务操作保存理由、原始输入与变更；纠错更新当前字段证据，原值保留在审计中。缺金额保留任务草稿，不写成正式交易。

## 行为验收定位

- 清晰记账、固定金额、账户事实、画像隔离、单次纠错：`test_core_refactor.py`、`test_account_routing.py`、`test_cli_wrapper.py`。
- 退款、转账、信用卡还款、垫付部分收回与核销：`test_event_semantics.py`、`test_core_refactor.py`。
- 官方来源复跑、跨来源关联、保留真实重复消费、总额不符拒绝写入：`test_workflows.py`。
- 银行借贷双列、信用卡退款、截图事实和确认门槛、旧导入入口：`test_release_acceptance.py`、`test_bill_screenshot.py`。
- 完整上下文冲突、并发写入、三文件恢复、操作撤销和不丢后续记录：`test_core_refactor.py`、`test_workflows.py`、`test_regressions.py`。
- 扩展候选/验证/授权/停用、修改失效、真实文件/网络/子进程隔离：`test_extensions.py`；安装包也执行扩展流程。
- 字段迁移与指标不变、旧结构/未知字段/多账本、旧观察保留：`test_workflows.py`、`test_core_refactor.py`、`test_release_acceptance.py`。
- 主动复核、忽略/暂缓/重开、通知去重、可比期间、证据更正与撤销：`test_workflows.py`、`test_release_acceptance.py`。
- 各报告格式、不可变归档、HTML 转义、空数据、独立解压安装、HTTP 业务兼容：`test_workflows.py`、`test_ledger_tool.py`、`test_installed_skill.py`。
- Git 关闭/本地/远程、仓库白名单、独立记录合并、同源 ID 统一、金额冲突、并发时拒绝安装、断网与自动重试：`test_core_refactor.py`、`test_sync_workflows.py`。
- GitHub 私有检查与显式建仓、新设备身份/提交核验、拒绝多余文件和覆盖、安装中断恢复：`test_remote_restore.py`。提供方返回值使用替身，未创建真实仓库。

## 实际验证

全量 **219 项测试全部通过，无跳过**（109.763 秒）。最后一处分析模块依赖清理另做了受影响的简报/建议流程复验。

完整验证命令为 `python3 -m unittest discover -s tests -v`。测试全用临时合成数据；localhost HTTP 和 macOS 沙箱在具备系统权限的环境实际执行。旧截图测试对个人下载文件的依赖已替换为合成 OCR 行验证。

标准 `skill-creator/scripts/quick_validate.py` 与 `scripts/package_skill.py --validate-only` 通过。分发包含 67 个必需文件，不包含三个旧 HTML 模板、测试目录、用户运行数据或发布审计文档。独立解压后执行记账、导入、习惯、字段、扩展、迁移与报告流程。

实际浏览器检查了合成报告的桌面及 375px 内嵌视口：关键金额、覆盖说明、长中文商户和来源 ID 均可阅读，无横向溢出；键盘 End 能滚动到长明细。只读 HTML 无表单或脚本，不存在键盘操作缺失的业务控件。未交付打印专用产品。

## 已知支持边界

- 一个账本一种币种；没有通用汇率换算。
- macOS 内置 OCR、macOS 扩展隔离；其他平台无隔离器就拒绝执行扩展。
- 内置适配只承诺文档列出的 CSV/TSV 布局；任意 XLSX/PDF 使用经验证的宿主解析器/转换器。
- Git 只恢复账务、画像和可移植能力；附件、报告、设备授权不在范围内。账务证据里的用户原文（可能含路径）会随账务版本化。
- 同源合并后若审计涉及的行/证据已经变化，旧操作的盲目撤销会被拒绝，应生成协调纠错提案。
- 后台调度与对外通知由宿主按用户意图提供；本次没有安装调度、上传数据或迁移正式账本。

正式切换与恢复步骤已写入 `references/runtime.md`。迁移先在副本验证并备份，再锁定实际版本执行；历史恢复生成新提交，不直接覆盖迁移后的新记录。

# Lazy Ledger 3.2.1

以智能管家为主的本地记账 skill。用户提供事实和目标，管家负责记账、导入对账、预算、分析、复核与报告；专业角色按任务加载，普通记账不需要启动多个 agent。

本次重构在 `codex/ledger-butler-refactor` 完成。CLI 已缩为参数与分发入口，CLI 和 HTTP 共用业务服务。实施与验收见 [执行计划](product-design-audit/execution-plan-2026-10.md) 和 [验收记录](product-design-audit/release-3.0.md)。

## 已实现

- 共享账务内核：十进制金额、统一收支/余额/预算口径，明确转账、退款、垫付、部分结算与当期核销。
- 独立用户习惯文档：每次任务加载 `user-profile.md`；明确规则、历史观察和单次选择分开。未知付款账户保持未分配。
- 一致写入：锁、完整上下文校验、幂等回执、字段证据、操作记录、定向撤销和可恢复多文件事务。
- 受控演进：字段登记、迁移预览/备份/指标核对；自定义解析脚本与系统分离，登记、样例验证、本机授权后才运行。
- 导入对账：微信/支付宝 CSV、已声明布局的银行和信用卡 CSV、规范化 CSV/TSV、macOS 截图 OCR。来源去重、跨来源关联、总额核验；截图确认前不入账，退款不反推原支出。
- 可选 Git：建账时询问，不启用/仅本地/私有远程三种模式；私有 GitHub 建仓与核验、手动或已授权的写后同步、离线重试、三方合并、同来源身份统一、冲突计划、历史差异与新设备恢复。
- 主动复核：写后维护发现事项，支持待补充、忽略、暂缓、解决和按新证据重开；通知回执去重。
- 按需展示：三个固定 HTML 模板已移除。对话优先；Markdown、JSON、可选只读 HTML 共用报告数据，归档保存不可变快照。旧展示入口已转向新报告服务。
- AI 自主展示：由 IDE 中的 AI 按问题直接生成 OpenUI Lang，支持嵌套布局、说明、分栏、标签页和折叠依据；也可生成独立 HTML/CSS/JS/SVG。组件能力与提示词从实际代码导出，脚本负责数据投影、校验、预览和归档，无需额外模型 API 或 MCP。

## 使用

```sh
export LAZY_LEDGER_FILE=/path/to/personal/ledger.json
scripts/ledger init --git-mode undecided
scripts/ledger context --task record
scripts/ledger record --text "午饭28" --operation-id unique-request-id
scripts/ledger show --range this-month
scripts/ledger report build --start 2026-10-01 --end 2026-10-31 --archive
scripts/ledger report build --start 2026-10-01 --end 2026-10-31 --format openui --archive
```

账本使用 `<文件名主体>-runtime/` 隔离画像、能力、报告和本机设置；建议放在 skill 安装目录之外。未决定 Git 模式时照常记账，不初始化历史或上传。

AI 生成入口为 `report ui-spec` → 冻结报告 → `report data` → AI 编写源码 → `report validate` → `report compose`。支持 `--openui` 或 `--html-source`；`--replace` 可更新独立预览，`--archive` 保存源码和不可变快照。旧 `--layout` 入口保留兼容。AI OpenUI 校验需要 Node.js 18+，安装版无需 npm；HTML 的内容含义仍需 AI 核对。`report watch` 可监听源码，校验通过后自动更新本地预览，失败时保留上一版并输出诊断；不接管 IDE 的逐 token 输出。具体命令见 [生成式展示](references/generative-ui.md)。

```sh
scripts/ledger history configure --mode local
scripts/ledger history remote --repository OWNER/BOOK --create
# 用户选定目标后，明确执行创建/连接；此步仍不上传
scripts/ledger history remote --repository OWNER/BOOK --create --apply
scripts/ledger history sync
scripts/ledger history list
scripts/ledger history diff --commit COMMIT
```

新设备先 `history inspect --source URL`，再 `history clone --ledger NEW_PATH --source URL --expected-ledger-id ID --expected-commit SHA`；默认仅本地模式，扩展须在本机重新验证。具体模式和恢复范围见 [历史说明](references/history.md)。

## 支持范围

- 一个账本一种币种，不进行无依据的汇率换算。
- 内置 OCR 和扩展隔离执行支持 macOS；其他平台没有合格隔离器时拒绝执行扩展。核心账务不需要这些可选能力。
- XLSX/PDF 和未声明的银行布局通过宿主解析器或已验证转换器适配，不猜测任意文件结构。
- Git 快照包含账务、画像、能力及登记的扩展代码。来源附件、报告成品和本机授权不包含在恢复范围内。账务中的用户输入原文与路径完整保留；凭据及本机运行配置不进入快照。
- 不内置常驻调度服务。只有用户提出需求时，管家才使用宿主支持的调度与消息能力。
- GitHub 提供方流程使用模拟响应验证；没有创建、修改或上传真实远程仓库，也没有改动正式账本。

## 开发验证

内部存储调用直接依赖 `ledger_core.storage.documents`；`ledger_db.py` 和 `ledger_core.adapters.api` 保留供旧 Python 调用方使用。兼容导出即使没有内部调用也不作为死代码自动删除。金额展示统一使用 `ledger_core.domain.money.format_money`，原导入路径仍可用。OpenUI 生成规范已升级到 version 2，JSON schema 统一从 `library.schema` 读取。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/package_skill.py --validate-only
python3 scripts/package_skill.py /tmp/ledger-package
```

测试使用临时合成账本和本地 Git 远程，包含独立解压安装运行。HTTP 测试需本机监听权限；扩展测试需要 macOS 的实际隔离能力。标准 skill 元数据另使用 `skill-creator/scripts/quick_validate.py` 校验。

前端源码在 `frontend/openui/`，固定依赖与锁文件独立管理；构建结果在 `assets/openui/`，随 skill 分发。使用者不需要 npm。开发时在该目录运行：

```sh
OPENUI_TELEMETRY_DISABLED=1 DO_NOT_TRACK=1 npm ci --ignore-scripts
npm run build
npm test
```

浏览器测试使用隔离的无头 Chrome 和合成账本，检查金额、只读交互、注入防护、无网络请求、移动布局和错误降级。构建时更新资源摘要及第三方许可证；修改组件契约需同步更新 catalog 的 renderer 版本。不要直接修改打包 JS。

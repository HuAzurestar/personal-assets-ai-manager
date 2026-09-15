# PAAM 目标数据模型

本文件是 PIRC-9 的权威表字典。账本按“事实、审查、经济”分层。

## V2 核心关系

四个核心业务对象为 `bill_fact`（事实流水）、`review_case`（流水审查）、
`ledger_entry`（迁移期物理名；语义为 Economic Flow）和
`review_case_bill`（迁移期物理名；语义为 Flow Allocation）。

Allocation 是三元关系：每行同时保存 `case_id`、`bill_id`、
`economic_id` 和一份明确金额。一个 Review、Fact 或 Economic 都可以
拥有多条 Allocation；一次完整审查由事实集合、经济集合和分配矩阵组成。

Economic Type 仅允许 `TRANSACTION`、`ACCOUNT_TRANSFER`、`CLAIM`。
AA、垫付、借款、退款、转账和换汇是 `review_case.behavior_code`，不参与
Economic Type 汇总。

对每条已接受 Fact，所有 CONFIRMED Review 且 ACTIVE Economic 的
Allocation 金额之和必须严格等于 Fact 金额。对每条 ACTIVE Economic，
有效 Allocation 之和也必须严格等于 Economic 金额。Pending 建议不占用
正式金额；导入通过 CONFIRMED DEFAULT Review 生成等额 TRANSACTION。
取消人工 Review 时，释放金额立即通过新的 DEFAULT Review 恢复为
TRANSACTION，因此正式经济层不存在 PARTIAL 或 UNRESOLVED 金额。

Fact、Allocation 和 Economic 必须同方向、同币种。换汇由不同币种的
多个 ACCOUNT_TRANSFER Economic 表达，不保存汇率、不跨币种求净额。
CLAIM 使用 `claim_key` 和 `claim_side` 聚合建立、结算及余额；退款等
冲正使用 `reversal_of_id` 指向原 TRANSACTION。

原始文件、Raw、Review 历史和标签表仍然保留；“四个核心对象”不表示
删除证据与审计辅助表。

## 通用约束

每张表固定包含：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | INTEGER | 主键 |
| `created_time` | DATETIME | `NOT NULL DEFAULT CURRENT_TIMESTAMP` |
| `updated_time` | DATETIME | `NOT NULL DEFAULT CURRENT_TIMESTAMP` |

业务字段使用 `NOT NULL`；缺省文本使用空串，未知语义使用 `UNKNOWN` 等明确状态。缺失的必要金额、方向或时间不能用 0/默认时间伪造。关系全部使用隐式 ID，不声明 SQL `FOREIGN KEY`，由 Service 批量校验并在同一事务内写入。

金额表示为 `amount_value / 10^amount_scale`，并带 `currency_code`。禁止 Float；不同币种不得直接相加或隐式换汇。

## 一、事实层

事实层保存外部来源、原始证据和接受后的规范事实。列表和汇总不读取这一层；只有导入、校验和单条详情读取。

### 1. `import_file`：一次导入的来源文件

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `batch_code` | VARCHAR(64) | `''` | 同一次提交的批次标识 |
| `source_type` | VARCHAR(40) | `UNKNOWN` | 支付宝、微信、银行、手工等来源 |
| `institution_code` | VARCHAR(40) | `UNKNOWN` | 银行或平台代码 |
| `filename` | VARCHAR(255) | `''` | 用户看到的原文件名 |
| `file_format` | VARCHAR(12) | `UNKNOWN` | CSV/XLS/XLSX/PDF/ZIP |
| `sha256` | VARCHAR(64) | `''` | 整个文件指纹 |
| `period_start` | VARCHAR(32) | `''` | 文件中最早有效流水时间 |
| `period_end` | VARCHAR(32) | `''` | 文件中最晚有效流水时间 |
| `total_count` | INTEGER | `0` | 来源行总数 |
| `success_count` | INTEGER | `0` | 接受或成功关联的行数 |
| `skip_count` | INTEGER | `0` | 重复或明确跳过的行数 |
| `issue_count` | INTEGER | `0` | 解析失败或冲突行数 |
| `status` | VARCHAR(20) | `PENDING` | PENDING/IMPORTED/PARTIAL/FAILED |

非空 SHA 使用 `(source_type, sha256)` 唯一索引。`total_count = success_count + skip_count + issue_count`。

### 2. `bill_raw`：来源行与原始证据

| 字段 | 类型 | 默认 | 可变性与说明 |
| --- | --- | --- | --- |
| `bill_id` | INTEGER | `0` | 可变的隐式 Fact ID；未解决时为 0 |
| `import_file_id` | INTEGER | `0` | 不可变的隐式文件 ID |
| `source_row_number` | INTEGER | `0` | 不可变的文件内行号 |
| `source_reference` | VARCHAR(160) | `''` | 来源交易号/订单号 |
| `raw_payload` | TEXT | `'{}'` | 不可变的原始字段 JSON |
| `raw_hash` | VARCHAR(64) | `''` | 不可变的规范行指纹 |
| `parse_status` | VARCHAR(20) | `PENDING` | PENDING/SUCCESS/DUPLICATE/SKIPPED/INVALID |
| `issue_code` | VARCHAR(80) | `''` | 稳定的机器错误代码 |
| `issue_message` | TEXT | `''` | 用户可读错误说明 |

唯一约束为 `(import_file_id, source_row_number)`。`bill_id` 不唯一：同一笔真实交易可以因不同导出选项、不同文件或不同来源拥有多条 Raw。处理冲突时只能更新 `bill_id` 和处理状态，不能改原始载荷、指纹、来源文件和行号。

### 3. `bill_fact`：接受后的规范账单事实

| 字段 | 类型 | 默认 | 可变性与说明 |
| --- | --- | --- | --- |
| `fact_key` | VARCHAR(160) | 无伪造默认 | 不可变、唯一的来源身份或已接受指纹 |
| `occurred_time` | DATETIME | 无伪造默认 | 不可变的发生时间 |
| `cash_direction` | VARCHAR(8) | 无伪造默认 | 不可变的 IN/OUT |
| `amount_value` | BIGINT | 无伪造默认 | 不可变的最小精度整数金额 |
| `amount_scale` | SMALLINT | `2` | 不可变的小数位数 |
| `currency_code` | VARCHAR(12) | `CNY` | 不可变的币种/单位 |
| `account_code` | VARCHAR(120) | `UNKNOWN` | 导入时识别的不可变来源账户 |
| `counterparty` | VARCHAR(200) | `''` | 不可变的规范交易对手 |
| `summary` | TEXT | `''` | 不可变的规范摘要 |

Fact 只放跨来源稳定、计算必须的核心字段。客户详情、完整账户文本、追溯文本和 SHA 等只保留在 Raw/File，点开详情时再查。账户修正通过 ACCOUNT Review 覆盖投影，不更新 `bill_fact.account_code`。

## 二、审查层

审查层保存用户对事实的解释。AA、借贷、退款、转账、换汇等共用一套表，用类型与角色实现多态，不按业务类型拆表。

### 4. `review_case`：当前审查聚合

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `review_type` | VARCHAR(40) | `UNKNOWN` | v1 兼容字段；V2 财务审查与 `behavior_code` 同值 |
| `behavior_code` | VARCHAR(40) | `UNKNOWN` | V2 行为说明；DEFAULT/AA/ADVANCE/LOAN/REFUND/TRANSFER/FX_EXCHANGE 等 |
| `status` | VARCHAR(20) | `PENDING` | PENDING/CONFIRMED/REJECTED/REVOKED |
| `allocation_status` | VARCHAR(20) | `PARTIAL` | PARTIAL/COMPLETE/CONFLICT |
| `version` | INTEGER | `1` | 乐观并发版本 |
| `title` | VARCHAR(160) | `''` | 简短展示标题 |
| `result_json` | TEXT | `'{}'` | 少量类型专属状态；禁止塞成员 ID 数组 |

客户端更新、确认、撤销和恢复操作必须携带 `expected_version`。只有 CONFIRMED 的财务 Review 能改变经济层；待审建议不能占用正式金额。

### 5. `review_case_bill`：一个 Case 的多个账单与分配

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `case_id` | INTEGER | `0` | 隐式 Case ID |
| `bill_id` | INTEGER | `0` | 隐式 Fact ID |
| `economic_id` | INTEGER | `0` | V2 流水分配指向的隐式 Economic ID；非财务 Review 目标为 0 |
| `role` | VARCHAR(40) | `UNKNOWN` | 该 Fact 在当前类型中的作用 |
| `party` | VARCHAR(120) | `''` | AA/借贷等需要的对方；不需要时空串 |
| `amount_value` | BIGINT | `0` | 明确保存的本次分配整数金额 |
| `amount_scale` | SMALLINT | `2` | 分配金额精度 |
| `currency_code` | VARCHAR(12) | `CNY` | 分配币种，必须与 Fact 一致 |

`role` 只描述该分配在本次行为中的作用，不决定 Economic Type。每行必须显式提交正整数金额；后端不按“剩余全额”猜测。排序使用 `(case_id, id)`，不设 `position` 字段。

### 6. `review_history`：只追加的确定性审计

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `case_id` | INTEGER | `0` | 隐式 Case ID |
| `version` | INTEGER | `1` | 本操作完成后的 Case 版本 |
| `operation` | VARCHAR(20) | `CREATE` | CREATE/UPDATE/CONFIRM/REVOKE/RESTORE/ASSIGN/ACCOUNT_SET/RESOLVE/DISMISS/REOPEN |
| `schema_version` | INTEGER | `1` | 快照结构版本 |
| `request_json` | TEXT | `'{}'` | 规范化命令内容 |
| `before_json` | TEXT | `'{}'` | 操作前完整聚合快照 |
| `after_json` | TEXT | `'{}'` | 操作后完整聚合快照 |
| `snapshot_hash` | VARCHAR(64) | `''` | `after_json` 的完整性指纹 |
| `reverses_history_id` | INTEGER | `0` | 本操作反向对应的历史 ID |
| `actor` | VARCHAR(120) | `local-user` | 操作者 |
| `reason` | TEXT | `''` | 操作原因 |
| `idempotency_key` | VARCHAR(120) | `''` | 非空时全局唯一的命令幂等键 |

唯一约束为 `(case_id, version)` 和非空 `idempotency_key`。历史不 UPDATE、不 DELETE；撤销/恢复追加新记录。request + before + after + hash 足以确定性重放和核对具体字段变化。

## 三、经济层

该层是 UI 日常读取的正式经济结果。准确性来自 `bill_fact + confirmed review + allocation`；经济审查服务是唯一写入者。

### 7. `ledger_entry`：最终展示的一条实际账本记录

V2 中该表是一条单方向、单币种的 Economic Flow。旧 in/out 和
`ledger_type` 字段仅用于 v1 迁移兼容，新查询不得用它们决定经济语义。

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_type` | VARCHAR(40) | `UNRESOLVED` | v1 兼容字段；V2 查询不得用于分类 |
| `economic_type` | VARCHAR(40) | `TRANSACTION` | TRANSACTION/ACCOUNT_TRANSFER/CLAIM |
| `cash_direction` | VARCHAR(8) | `UNKNOWN` | IN/OUT |
| `amount_value` | BIGINT | `0` | 单方向 Economic 的整数金额 |
| `amount_scale` | SMALLINT | `2` | Economic 金额精度 |
| `currency_code` | VARCHAR(12) | `CNY` | Economic 币种 |
| `claim_key` | VARCHAR(160) | `''` | CLAIM 周期的稳定关联键 |
| `claim_side` | VARCHAR(20) | `UNKNOWN` | RECEIVABLE/PAYABLE/UNKNOWN |
| `reversal_of_id` | INTEGER | `0` | 冲正指向的原 TRANSACTION Economic ID |
| `status` | VARCHAR(20) | `ACTIVE` | PENDING/ACTIVE/REVOKED |
| `allocation_status` | VARCHAR(20) | `DEFAULT` | v1 兼容字段；`V2_ONLY` 防止新 Economic 泄漏到旧列表，V2 发布只看 `status` 与金额守恒 |
| `title` | VARCHAR(200) | `''` | 列表使用的紧凑标题 |
| `start_time` | DATETIME | 无伪造默认 | 来源 Fact 的最早时间 |
| `end_time` | DATETIME | 无伪造默认 | 来源 Fact 的最晚时间 |
| `in_amount_value` | BIGINT | `0` | 流入整数金额 |
| `in_amount_scale` | SMALLINT | `2` | 流入精度 |
| `in_currency_code` | VARCHAR(12) | `CNY` | 流入币种 |
| `out_amount_value` | BIGINT | `0` | 流出整数金额 |
| `out_amount_scale` | SMALLINT | `2` | 流出精度 |
| `out_currency_code` | VARCHAR(12) | `CNY` | 流出币种 |
| `in_account_code` | VARCHAR(120) | `UNKNOWN` | 有效流入账户；多个时为 MULTIPLE |
| `out_account_code` | VARCHAR(120) | `UNKNOWN` | 有效流出账户；多个时为 MULTIPLE |
| `input_hash` | VARCHAR(64) | `''` | 排序后的 Fact/Review 输入指纹 |
| `projection_version` | INTEGER | `1` | 投影规则版本及并发依据 |

普通 Fact 导入后默认生成同方向、同币种、等额的 TRANSACTION。人工 Review 可以把事实金额重新分配为 TRANSACTION、ACCOUNT_TRANSFER 或 CLAIM；AA、借贷、退款、转账和换汇只保存在 Review 行为说明中。每条 Economic 只有一个方向和币种，跨币种行为必须拆成多条 Economic，并且永不折算汇率。

### 8. `ledger_entry_source`：投影反向追溯

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | 隐式 Ledger ID |
| `source_kind` | VARCHAR(20) | `BILL_FACT` | BILL_FACT 或 REVIEW_CASE |
| `source_id` | INTEGER | `0` | 对应来源 ID |

该表只供 v1 投影兼容和导入迁移定位使用。V2 的 Fact、Review、Economic 追溯只通过 `review_case_bill` 三元分配关系完成；不能再依据这里的唯一约束推断 Fact 与 Economic 是 1:1。

### 9. `tag_view`：标签维度

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `name` | VARCHAR(120) | `''` | 展示名 |
| `system_name` | VARCHAR(64) | `''` | 唯一稳定代码 |
| `status` | VARCHAR(20) | `ACTIVE` | ACTIVE/ARCHIVED |

### 10. `tag`：标签值

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `view_id` | INTEGER | `0` | 隐式 Tag View ID |
| `name` | VARCHAR(120) | `''` | 展示值 |
| `system_name` | VARCHAR(64) | `''` | 维度内稳定代码 |
| `status` | VARCHAR(20) | `ACTIVE` | ACTIVE/ARCHIVED |

`(view_id, system_name)` 唯一。每个活动维度有受保护的 `unclassified` 默认值；定义采用归档而不是删除。

### 11. `ledger_entry_tag`：Ledger 当前标签

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | 隐式 Ledger ID |
| `tag_id` | INTEGER | `0` | 隐式 Tag ID |

`(ledger_id, tag_id)` 唯一。Tag Assignment 直接以 Ledger ID 为对象，不经 Fact、`ledger_entry_source` 或 Review 间接派生。每个活动 Ledger 在每个活动 Tag View 下恰有一个当前值；缺失值补为 `unclassified`。列表页按返回的 Ledger ID 一次批量读取。当前规模无需 Elasticsearch；引入第二套存储会增加一致性成本。

## 热、冷与读取规则

| 数据 | 热度 | 正常读取方式 |
| --- | --- | --- |
| `ledger_entry` | 热 | 只读取 ACTIVE Economic 的列表、筛选和按币种汇总 |
| `ledger_entry_tag`、`tag`、`tag_view` | 热/温 | 列表按 ID 批量取；字典独立取 |
| `ledger_entry_source` | 温 | 仅单条详情和投影重建 |
| `bill_fact` | 温 | 导入核对、审查、单条详情 |
| `review_case`、`review_case_bill` | 温 | 审查工作台与单条详情 |
| `import_file`、`bill_raw`、`review_history` | 冷 | 来源追溯、问题核查、审计详情 |

流水列表禁止读取 Raw、文件元数据、Review 明细和历史；这些详细文本只在用户点开一条记录时按 ID 批量取。SHA 前端可以短显示，但后端保留完整值。

## 迁移结论

旧 23 张业务表和 5 张过渡表已经从模型和运行时删除。现有 11 表数据库采用原位迁移，必要语义分别进入：

- 文件/批次/来源/异常：`import_file + bill_raw`。
- 规范流水：`bill_fact`。
- AA/垫付/借贷/退款/转账/换汇等行为：统一 Review 三表。
- 正式经济结果：`ledger_entry`、三元 `review_case_bill` 与标签表。

启动时为历史 Fact 补建 CONFIRMED DEFAULT Review 与等额 TRANSACTION，并复用可用的 v1 默认 Ledger 行。迁移期保留旧列和旧读取 API，但 V2 写入、汇总与新页面只使用三类 Economic 语义，不做长期双写设计。

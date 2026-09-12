# PAAM 目标数据模型

本文件是 PIRC-9 的权威表字典。账本固定为 11 张表，按“事实、审查、增强热投影”分层。

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
| `review_type` | VARCHAR(40) | `UNKNOWN` | AA/LOAN_BORROW/LOAN_LEND/REFUND/TRANSFER/FX_EXCHANGE/DUPLICATE/TAG/ACCOUNT/FACT_CONFLICT |
| `status` | VARCHAR(20) | `PENDING` | PENDING/CONFIRMED/REJECTED/REVOKED |
| `allocation_status` | VARCHAR(20) | `PARTIAL` | PARTIAL/COMPLETE/CONFLICT |
| `version` | INTEGER | `1` | 乐观并发版本 |
| `title` | VARCHAR(160) | `''` | 简短展示标题 |
| `result_json` | TEXT | `'{}'` | 少量类型专属状态；禁止塞成员 ID 数组 |

客户端写操作必须携带 `expected_version`。只有 CONFIRMED 的财务 Review 能改变热投影；待审建议不能改变实际账本。

### 5. `review_case_bill`：一个 Case 的多个账单与分配

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `case_id` | INTEGER | `0` | 隐式 Case ID |
| `bill_id` | INTEGER | `0` | 隐式 Fact ID |
| `role` | VARCHAR(40) | `UNKNOWN` | 该 Fact 在当前类型中的作用 |
| `party` | VARCHAR(120) | `''` | AA/借贷等需要的对方；不需要时空串 |
| `amount_value` | BIGINT | `0` | 明确保存的本次分配整数金额 |
| `amount_scale` | SMALLINT | `2` | 分配金额精度 |
| `currency_code` | VARCHAR(12) | `CNY` | 分配币种，必须与 Fact 一致 |

常见角色包括 `AA_PAID`、`AA_RECEIVED`、`LOAN_RECEIVED`、`LOAN_REPAID`、`LOAN_LENT`、`LOAN_RECOVERED`、`REFUND_EXPENSE`、`REFUND_RECEIVED`、`TRANSFER_OUT`、`TRANSFER_IN`、`TRANSFER_FEE`、`DUPLICATE_RETAINED`、`DUPLICATE_EXCLUDED`。省略金额时，后端可以按剩余全额计算，但入库时金额必须明确。排序使用 `(case_id, id)`，不设 `position` 字段。

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

## 三、增强热投影层

该层是 UI 日常读取的可重建结果。准确性来自 `bill_fact + confirmed review`；投影服务是唯一写入者。

### 7. `ledger_entry`：最终展示的一条实际账本记录

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_type` | VARCHAR(40) | `UNRESOLVED` | INCOME/EXPENSE/AA/LOAN_BORROW/LOAN_LEND/REFUND/TRANSFER/FX_EXCHANGE/PASS_THROUGH/UNRESOLVED |
| `allocation_status` | VARCHAR(20) | `DEFAULT` | DEFAULT/PARTIAL/COMPLETE/EXCLUDED/CONFLICT；唯一发布状态 |
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

普通 Fact 是一条 INCOME 或 EXPENSE。特殊 Review 把数个 Fact 投影成一条 AA、借贷、退款、转账或换汇记录，并分别保留 in/out，避免把代收、退款和账户间转账误当收入。跨币种两侧分别展示，在未来明确汇率政策前不计算净额。

### 8. `ledger_entry_source`：投影反向追溯

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | 隐式 Ledger ID |
| `source_kind` | VARCHAR(20) | `BILL_FACT` | BILL_FACT 或 REVIEW_CASE |
| `source_id` | INTEGER | `0` | 对应来源 ID |

`(source_kind, source_id)` 唯一，因此一个 Fact 只属于一个最终投影，反向也能从投影唯一找到全部 Fact/Review。详情由此批量读取来源，不扫描无关表。

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

### 11. `ledger_entry_tag`：热投影标签

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | 隐式 Ledger ID |
| `tag_id` | INTEGER | `0` | 隐式 Tag ID |

`(ledger_id, tag_id)` 唯一。列表页按返回的 Ledger ID 一次批量读取。当前规模无需 Elasticsearch；引入第二套存储会增加一致性成本。

## 热、冷与读取规则

| 数据 | 热度 | 正常读取方式 |
| --- | --- | --- |
| `ledger_entry` | 热 | 列表、筛选、汇总的主表 |
| `ledger_entry_tag`、`tag`、`tag_view` | 热/温 | 列表按 ID 批量取；字典独立取 |
| `ledger_entry_source` | 温 | 仅单条详情和投影重建 |
| `bill_fact` | 温 | 导入核对、审查、单条详情 |
| `review_case`、`review_case_bill` | 温 | 审查工作台与单条详情 |
| `import_file`、`bill_raw`、`review_history` | 冷 | 来源追溯、问题核查、审计详情 |

流水列表禁止读取 Raw、文件元数据、Review 明细和历史；这些详细文本只在用户点开一条记录时按 ID 批量取。SHA 前端可以短显示，但后端保留完整值。

## 删除结论

旧 23 张业务表和 5 张过渡表已经从模型、运行时与开发 SQLite 物理删除。它们的必要语义已分别进入：

- 文件/批次/来源/异常：`import_file + bill_raw`。
- 规范流水：`bill_fact`。
- 退款/AA/借贷/转账/重复/账户/标签/冲突：统一 Review 三表。
- 最终实际流水：Ledger 三表与标签三表。

开发库没有生产数据，因此按已确认方案直接清空重建，没有旧数据回填或双写阶段。Git 历史仍可用于追溯被删除实现，但任何新功能不得恢复兼容表或影子 API。

# PR #8 修复说明

本文记录 PR #8 在合并前需要完成的修复及验收口径。项目尚未上线，因此不处理旧版 `bills`、`import_batches` 等表的数据迁移；开发环境中的旧数据库可以重建。

## 1. Fact Conflict 的业务含义

`FACT_CONFLICT` 不是普通的重复流水。它表示导入行带有可稳定识别交易的身份键，但该身份键与已经接受的 Fact 无法安全地对应。保留该状态的目的，是避免覆盖不可变的 `transaction_fact`，同时保存有矛盾的来源证据供人工处理。

当前生成流程如下：

1. `statement_parser` 解析来源行，生成交易引用、来源、账户、时间、金额等规范字段。
2. `smart_import.identity_keys()` 在存在平台交易号或可稳定构造银行身份时生成身份键。
3. `smart_import.build_plan()` 在以下情况下把行标记为 `action="error"`：
   - 同一个身份键已经指向多个历史 Fact；
   - 身份键只指向一个 Fact，但新来源行的金额、币种或交易日期与该 Fact 不兼容；
   - 匹配链形成循环，或匹配目标仍处于未解决状态，并且该行保留了身份键。
4. `TargetImportMatchMapper.plan()` 允许包含这种行级冲突的文件继续确认，以便先保存来源证据；只有整文件解析失败或未解决的人工歧义会阻止确认。
5. `TargetImportWriteMapper.write_plan()` 将带身份键的错误行写为：
   - `transaction_fact_id = 0`
   - `row_status = INVALID`
   - `issue_code = FACT_CONFLICT`
6. 后续操作允许人工关联已有 Fact、创建新 Fact、忽略冲突或重新打开。

与重复导入的区别：身份一致且金额、币种、日期兼容时，导入行会作为 `supplement` 补充既有 Fact 的来源证据，不会创建冲突。

### 保留或移除的决策

建议保留 `FACT_CONFLICT`。它保护已接受 Fact 的不可变性，并让有矛盾的原始证据能够入库而不是被丢弃。如果产品不准备提供人工处理能力，则应改为在预览阶段阻止确认，并同时删除当前不可达的冲突处理 API/UI；不能继续保留“写入冲突但用户无法处理”的中间状态。

## 2. 冲突工作流修复

### 2.1 修正请求 DTO

当前 `dismiss` 和 `reopen` 路由复用了不包含 `expected_version` 的 `TargetReviewTransitionRequest`，但 Service 和前端都依赖该字段。这是实际契约错误：前端发送该字段时会因 `extra="forbid"` 返回 422，不发送时 Service 访问不存在的属性会报错，因此需要修复。

修复方案：

- 抽取 `TargetTransitionRequest` 作为通用基础 DTO，承载 `actor` 和 `reason`。
- `TargetReviewTransitionRequest` 继承基础 DTO 并保留 Review 已实现的 `idempotency_key`。
- 新增通用的 `TargetVersionedTransitionRequest(TargetTransitionRequest)`，只增加 `expected_version: int`；不创建仅改名字的冲突专用 DTO。
- `dismiss`、`reopen` 使用 `TargetVersionedTransitionRequest`。
- `TargetFactConflictResolveRequest` 继承 `TargetVersionedTransitionRequest`，只补充 resolve 特有字段，避免重复定义公共字段。
- 经济审查的 revoke、restore 没有版本号契约，继续使用基础 DTO，不被此次修复改变。
- 为 resolve、dismiss、reopen 分别增加成功、旧版本冲突和重复提交测试。

当前冲突命令虽然接收 `idempotency_key`，但并未持久化或回放。本次选择从冲突命令 DTO 和前端请求中删除该字段，只提供基于 `expected_version` 的旧页面写入保护；经济审查已经实现的幂等契约保持不变。后续只有设计出可靠的持久化与回放位置后，才重新为冲突命令增加幂等键。

### 2.2 补齐 UI 入口

当前前端实现了 `showFactConflict()` 和操作按钮绑定，但没有任何页面生成 `data-action="fact-conflict-detail"` 的入口；来源行响应也没有返回冲突行 ID。

修复方案：

- `ImportFileRowRead` 增加 `id`。
- Import File 来源行详情在 `issue_code == "FACT_CONFLICT"` 时显示“处理事实冲突”。
- 按钮使用来源行 `id` 打开 `showFactConflict(id)`。
- 导入历史或 Review 工作台增加待处理冲突数量和筛选入口，避免只能逐文件查找。
- 解决、忽略或重新打开后刷新来源行、文件统计和冲突列表。

### 2.3 将列表能力下推到 SQL

`fact_conflict/list` 当前先加载全部冲突，再在 Service 中筛选、排序和分页，还会在 Python 中比较时间。

修复方案：

- 保留产品当前真正需要的 `status` 筛选；按 ID 查询使用已有详情接口。
- 删除尚无 UI 需求的 `id`、`created_time`、`updated_time` 列表筛选，以及调用方自定义排序能力，避免维护低收益的查询组合和索引。
- 列表固定使用 `id DESC` 稳定排序；Mapper 接收状态、页码和页大小。
- 使用 SQL 完成状态 `WHERE`、`COUNT`、`ORDER BY`、`OFFSET` 和 `LIMIT`。
- Service 只负责 DTO 转换和业务状态映射。
- 增加超过一页数据、状态筛选和稳定翻页测试。后续只有出现明确的 UI 场景并评估索引成本后，才增加新的筛选或排序字段。

## 3. 时间标准统一

数据库统一存储 UTC ISO-8601 文本，格式为 `YYYY-MM-DDTHH:mm:ss.SSSZ`。API 返回带 `Z` 的 UTC 时间；前端根据用户选择的时区显示，默认 `Asia/Hong_Kong`。

### 后端

- `created_time`、`updated_time` 使用 `datetime.now(timezone.utc)`，禁止把 naive datetime 直接标记为 `Z`。
- Router 是 API 时间边界：带时间的请求必须包含 RFC 3339 的 `Z`/显式偏移，或同时携带明确的 IANA 时区；Router 完成解析并转换为 UTC aware datetime 后再调用 Service。
- Service、Mapper 和数据库层只接收 UTC aware datetime，不推断、不补默认时区。
- `UTCISO8601DateTime.process_bind_param()` 遇到 naive datetime 时直接拒绝，作为边界遗漏的防御性校验。
- `process_result_value()` 返回 UTC aware datetime，不再移除 `tzinfo`。
- `TransactionFact.occurred_time` 和 `LedgerEntry.occurred_time` 使用同一种 UTC ISO-8601 类型。
- 上传账单时，Router 接收前端显式提交的来源时区并传给导入解析器；解析器用该时区解释账单中的无时区时间并立即转成 UTC，不允许 Service、Mapper 或数据库猜测。
- 所有时间筛选边界均由 Router 转为 UTC aware datetime 后交给 Service 和 Mapper。

### 前端

- API 时间统一按标准 ISO-8601 解析。
- 提供显示/导入时区选择，默认 `Asia/Hong_Kong`；用户选择后，展示、筛选边界和无时区账单解释保持一致。
- 日期/时间筛选先以当前展示时区构造边界，再使用 `toISOString()` 发送 UTC。
- 日期范围继续使用左闭右开语义：`start <= occurred_time < end`。

### 必测边界

- 本地日期 9 月 1 日不能包含 8 月 31 日晚间记录。
- 跨月、跨年和夏令时地区的日期边界正确。
- Fact、Ledger、Review、Import File、Tag View 和 Fact Conflict 的时间筛选行为一致。
- API 不再返回 offset-naive datetime，Python 中不再出现 naive/aware 比较异常。

## 4. SQLite schema 规则

### 4.1 `asset/sql` 是唯一设计基准

`src/asset/sql/*.sql` 是物理数据库设计的权威来源。ORM Entity 只负责运行时映射，不能形成第二套不同的 DDL。

修复方案：

- 新建数据库时按固定顺序执行 `src/asset/sql/*.sql`。
- `ensure_target_schema()` 不再逐表调用 ORM `Table.create()` 生成另一套结构。
- 增加 schema parity 测试，比较实际 SQLite 与 ORM 映射的表名、列名、SQLite 类型、NULL、主键、唯一约束和索引；列默认值直接由权威 SQL 资产定义，不再依赖 ORM 生成 DDL。
- SQL 设计变更必须先修改 `asset/sql`，随后同步 Entity 和 parity 测试。

### 4.2 去掉 CHECK 约束

按项目约定，业务枚举、正数金额、合法 ID、JSON 格式及状态转换由 Schema、Service 和 Mapper 统一维护，不依赖 SQLite `CHECK`。

修复方案：

- 从所有 `src/asset/sql/*.sql` 删除 `CHECK (...)`。
- 从 Entity 删除全部 `CheckConstraint`。
- 保留 `NOT NULL`、必要默认值、主键、唯一约束和已验证的索引。
- 为原 CHECK 覆盖的规则补充 Service/Schema 测试，尤其是金额、枚举、计数一致性、JSON 和逻辑 ID。

### 4.3 SQLite 编码

- 新数据库创建前设置并验证 `PRAGMA encoding = 'UTF-8'`。
- schema 验收测试必须断言 `PRAGMA encoding` 返回 `UTF-8`。
- SQL 文件、Python 源码和前端资源统一保存为 UTF-8。

## 5. 非目标范围

- 项目尚未上线，不迁移旧版 `bills`、`import_batches` 等数据。
- 开发数据库在 schema 变更后使用受控 reset 工具重建。
- 本次不增加新的物理表；如幂等或审计要求确实需要新表，应另行评审十表边界。

## 6. 合并验收

- resolve、dismiss、reopen 可从 UI 完成，并覆盖正常、重复、旧版本请求。
- 冲突行可从 Import File 来源行和统一待处理入口访问。
- 所有持久化时间均为 UTC ISO-8601，前端按显示时区转换。
- `asset/sql`、实际 SQLite schema 和 ORM 映射通过 parity 测试。
- SQL 与 Entity 均不存在 `CHECK` 约束。
- Fact Conflict 列表在数据库侧完成状态筛选、固定稳定排序和分页，不暴露无实际 UI 需求的筛选、排序组合。
- 完整 pytest、前端语法检查和目标浏览器回归全部通过。

# PIRC-9 SQL 重构清单

状态：核心重构完成。当前运行时代码和 SQLite 都不再包含旧模型。

## 已完成

- [x] 数据分为事实层、审查层、经济层。
- [x] Review 下的三元 Allocation 连接 Fact 与 Economic，并维持严格 Fact 金额守恒。
- [x] Economic Type 收敛为 TRANSACTION、ACCOUNT_TRANSFER、CLAIM；场景名称保留在 Review。
- [x] 旧 23 表与 5 张过渡兼容表从开发数据库移除。
- [x] 旧 Controller、Service、Mapper、ORM、影子迁移器、接口与页面物理删除。
- [x] 运行入口只初始化 11 张目标表。
- [x] DTO/VO 不执行 SQL；Controller 和 Service 不拼 SQL。
- [x] 列表、详情、汇总只查询明确字段，禁止 `SELECT *`。
- [x] 流水列表的标签使用一次 `ledger_id IN (...)` 批量查询。
- [x] 流水详情按 Fact/Raw/Import/Review ID 集合批量查询，不逐条查。
- [x] Review 和标签写入先批量校验隐式 ID，再在同一事务内更新投影。
- [x] 导入先计算本批身份、引用和时间范围，再读取匹配候选；不加载全库历史。
- [x] 导入确认批量预取、分组写入；1 行与 20 行 SELECT 次数不线性增长。
- [x] 金额统一为整数值、精度和币种，目标表没有 Float 金额。
- [x] 表关系使用隐式 ID，不声明 SQL `FOREIGN KEY`。
- [x] 原始证据和审查历史只在单条详情加载，不进入列表/汇总。
- [x] 旧代码测试替换为目标 API 回归，保留 CSV、XLS、XLSX、PDF、ZIP、多源、冲突与幂等验证。

## 查询预算

| 路径 | SELECT 预算 | 数据量关系 |
| --- | ---: | --- |
| 流水分页 | 3 | 10/100 行一致：count、page、tags batch |
| 汇总 | 2 | 与流水数无关：count、grouped legs |
| 单条详情 | 最多 10 | 只随该投影的来源种类变化，不随列表页行数变化 |
| Review 列表 | 3 | 20 个 case 固定 |
| 标签字典列表 | 2 | view 和 tag 各一次 |
| 标签分配 | 6 | 1/20 个来源 Fact 一致 |

## 最小索引

只保留主键、唯一约束和已被查询计划验证的热路径索引：

| 索引 | 用途 |
| --- | --- |
| `ix_ledger_entry_time_id` | 流水按时间和 ID 稳定分页 |
| `ix_bill_fact_occurred_time_id` | 导入候选的时间窗和事实排序 |
| `ix_bill_raw_bill_id_id` | 详情批量取原始证据 |
| `ix_bill_raw_source_reference_bill_id` | 来源引用去重；非空部分索引 |
| `ix_review_case_bill_bill_case` | 从 Fact 反查相关 Review |
| `ix_review_case_bill_case_id` | 批量读取一个/多个 Case 明细 |
| `ix_ledger_entry_source_ledger_kind_id` | 从热投影读取 Fact/Review 来源 |

唯一索引另用于文件 SHA、Fact key、Review 版本/幂等键、投影来源和标签关系。没有为低频文本、状态枚举或未证实路径提前堆索引。

## 后续开发

SQL 重构没有必须继续的结构性工作。下一阶段应以真实功能为驱动：

1. 继续以经济审查分配矩阵扩展具体行为模板，不再新增 Economic Type。
2. 数据量显著增长后，以 `EXPLAIN QUERY PLAN` 和端到端耗时决定是否增加复合索引。
3. 多币种只按原币种分别展示；当前模型明确不保存汇率、不做本位币估值。
4. 若未来单机 SQLite 的写并发或 CPU 解析经过测量成为瓶颈，再评估拆服务；当前没有迁移语言或引入 ES 的收益证据。

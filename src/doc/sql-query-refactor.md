# PIRC-9 SQL 重构清单

本页下列PIRC-9/PIRC-24完成项是历史清单；PIRC-35当前二十表及双链以`data-model.md`和对应SQL为准，不把旧十四表/旧经济类型说明当作当前验收结果。

## PIRC-35 具名选择读取（2026-10-02）

- 2026-10-03 R14：Position当前页数量使用同一有界证据流按position_id分组，单详情复用此实现；最多100个对象、50,000贡献腿/2秒、fetchmany400，本方名称一次IN查询。没有逐对象quantity/detail SQL、估值表或新增索引。`test_position_list_summary.py`检查1/100对象列表与search SQL数量恒定，列表/详情状态及token一致、400−300余100、来源失效、无证据与真实历史零的区分、分页/稀疏搜索以及超限整页拒绝。这是功能/查询数量证据，不是生产p95或R21通过结论。

- 元数据列表和新search以同快照关系检查、分页/稳定seek和一次归属联读返回个人/集合名称；不读Raw payload。每页最多100候选，无累计扫描行上限，搜索在遮罩后的公开值上匹配。
- `test_metadata_search.py`对1/100条来源列表和搜索验证SQL语句数不随每行增加，并覆盖空命中继续、当前归属、相同尾号不同来源、隐藏前缀零命中、字面通配字符及破损归属不被筛选隐藏。
- 来源腿search在Position ID固定范围内先扫描一批，再批量读取命中腿的Review和第二段关系，沿用IN≤400及关系完整性限额；没有逐腿SQL或新增索引。`test_pirc35_position.py`覆盖空批到后续命中及跨Position游标拒绝。
- 这些定向契约和查询数量证据不是R21默认候选排序的性能通过结论；大规模默认排序、p95及深游标仍需单独实测。

状态：核心重构完成。当前运行时代码和 SQLite 都不再包含旧模型。

## 已完成

- [x] 数据分为事实层、审查层、经济层。
- [x] Review 下的三元 Allocation 连接 Fact 与 Economic，并维持严格 Fact 金额守恒。
- [x] Ledger Entry Type 收敛为 INCOME_AND_EXPENSE、INTERNAL_TRANSFER、ASSET_AND_LIABILITY；Review 行为独立表达。
- [x] 旧 23 表与 5 张过渡兼容表从开发数据库移除。
- [x] 旧 Controller、Service、Mapper、ORM、影子迁移器、接口与页面物理删除。
- [x] 原账务模型收敛为 10 张目标表；PIRC-24 在同库增加设置、规则、建议申请和模型审计，当前共 14 张，详见 data-model.md。
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
| `ix_ledger_entry_occurred_time_id` | 流水按发生时间和 ID 稳定分页 |
| `ix_transaction_fact_occurred_time_id` | 导入候选的时间窗和事实排序 |
| `ix_transaction_import_row_fact_id` | 详情批量取原始证据 |
| `ix_transaction_import_row_reference_fact` | 来源引用去重；非空部分索引 |
| `ix_review_allocation_fact_case` | 从 Fact 反查相关 Review |
| `ix_review_allocation_case_id` | 批量读取一个/多个 Case 明细 |

唯一索引另用于文件 SHA、Fact key、Review 版本/幂等键、投影来源和标签关系。没有为低频文本、状态枚举或未证实路径提前堆索引。

## 后续开发

### PIRC-35 R08 当前事项读取（2026-10-03）

Candidate普通页及文本命中批在原同快照校验/分页后，增加一次页内Fact当前Review摘要联读；不同Fact数量不增加SQL次数。整组成员总数用Allocation中的不同Fact，不随候选页量缩小，多个同组现金拆分不重复计成员。成员集合通过`/review/{id}/fact/list`作标准分页，原Review停用仍保留全部原成员；不逐行加载详情、原文或历史。页内ID≤100，组ID集合留在SQL内；摘要结果≤4000、响应≤2MiB、查询和Python组装共用30秒预算。超限整页拒绝而非截断。新反例比较1/100 Fact的候选页、搜索批及实际100成员组，不将查询次数恒定当作R21规模延迟通过。

R08前端直接复用成员页批量完整选择，不逐Fact加载Candidate或详情；原现金/数量按需复用Inspection只读详情及超限分页，查看不写账。下面旧阶段的“没有必须继续的结构性工作”和旧索引名称不代表本次22项审查通过。R08固定SHA扩大矩阵、R19当前/历史流水及R21默认排序规模验证仍待。

SQL 重构没有必须继续的结构性工作。下一阶段应以真实功能为驱动：

1. 继续以经济审查分配矩阵扩展具体行为模板，不再新增 Economic Type。
2. 数据量显著增长后，以 `EXPLAIN QUERY PLAN` 和端到端耗时决定是否增加复合索引。
3. 多币种只按原币种分别展示；当前模型明确不保存汇率、不做本位币估值。
4. 若未来单机 SQLite 的写并发或 CPU 解析经过测量成为瓶颈，再评估拆服务；当前没有迁移语言或引入 ES 的收益证据。

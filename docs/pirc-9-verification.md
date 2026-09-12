# PIRC-9 功能覆盖与验收记录

本记录描述当前目标运行时。旧 `/api/*` 工作台和 23 张兼容表仅保留为代码回归样本，不属于默认运行路径。

## 事实层

| 需求 | 当前实现 | 验证重点 |
| --- | --- | --- |
| 多源导入 | `/paam/import/v1/preview` 支持支付宝、微信、建行、农行和招行 | 来源识别、格式解析、真实样本与格式回归 |
| 预览后原子确认 | 预览状态限时保存在应用内存；确认时重新计算版本并在一个事务中写入 | 过期、版本变化、并发确认、重复请求 |
| 字段事实与原始证据分离 | `bill_fact` 保存规范字段；`bill_raw` 保存来源行与解析状态；`import_file` 保存文件级元数据 | 同一 Fact 可关联多份不同导出证据，详情可回钻 |
| 异常不另建 issue 表 | 解析问题保存在 `bill_raw`；事实冲突使用 `FACT_CONFLICT` Review | 解决、忽略、重开都保留确定性历史 |
| 多币种精确金额 | `amount_value + amount_scale + currency_code`，禁止 Float | 不同币种分开汇总，不隐式换汇 |

## 审查层

| 需求 | 当前实现 | 验证重点 |
| --- | --- | --- |
| 一个 Case 关联多笔 Bill | `review_case` + `review_case_bill` | 批量校验 ID，不逐笔查询；分配不能超额或冲突 |
| 多态业务类型 | AA、借入、借出、退款、转账、换汇、重复共用一套 Case/Line | 类型策略由后端校验，不在 Controller 或 UI 猜测 |
| 可确定还原 | `review_history` 追加 request/before/after、版本、哈希、反向事件和幂等键 | 创建、修改、确认、撤销、恢复均可重放核验 |
| 账户、标签、冲突统一审查 | ACCOUNT、TAG、FACT_CONFLICT 同样写 Review | 原始 Fact 不被修正覆盖，投影同步更新 |
| 隐式外键 | SQLite 不声明 Foreign Key；唯一约束、事务和离线一致性检查维护关系 | 孤儿、重复成员、版本和投影来源检查 |

## 增强层与 SQL 性能

| 路径 | SQL 约束 | 当前结果 |
| --- | --- | --- |
| 流水分页 | count + 明确字段的投影页 + `ledger_id IN (...)` 标签批量查询 | 1、10、100 行均固定 3 次 SELECT |
| 汇总 | 只聚合 `ledger_entry` 的整数金额字段 | 不读取 raw、Review 历史或标签明细 |
| 单条详情 | 只为用户点开的一个 `ledger_entry` 读取 Fact、raw、文件、相关 Review 与完整历史 | 查询数有固定上界，不随列表页行数增长 |
| Review/标签写入 | 先批量读取隐式关联，再批量写入并重建受影响投影 | 禁止循环 `get(id)` 和 `SELECT *` |

## 运行时切换

- `run.py` 默认启动 `app.target_main:app`。
- 首页脚本只调用 `/paam/import/v1`、`/paam/ledger/v1`、`/paam/review/v1`、`/paam/tag/v1`。
- 启动只调用 `init_target_db()`；开发 SQLite 已重建为精确 11 张目标表。
- 新 UI 已覆盖概览、流水分页和详情、导入预览/修订/确认、标签字典和分配、财务 Review 创建/修改/确认/撤销/恢复、账户修正与事实冲突处理。
- `app.main:app` 仍用于旧接口回归测试，不是产品启动入口，后续可连同旧页面和旧 Mapper 一起物理清理。

## 验证命令

```powershell
node --check app/static/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_target_ui.py
```

目标运行时专项覆盖还会验证：首页和静态资源可访问、前端没有旧热接口字符串、旧接口在目标应用返回 404、导入及 Review 生命周期可用、最终 SQLite 表集合与 `TARGET_TABLE_NAMES` 完全一致。

## 真实样本结果（2026-09-12）

本机 `E:\Worktable\Download` 的 7 份支付宝、微信、建行、农行和招行文件在独立临时数据库完成正序与逆序验收：

- 827 条来源记录全部留存，其中 803 个新 Fact、17 条补充证据、7 条非入账记录，解析错误为 0。
- 写入后得到 803 个 `ledger_entry`；Fact 数、Raw 数及投影数与预览完全一致。
- 两种文件顺序生成相同事实签名 `a43d8a2aafd669dfaf913f7f7d257c513ec8abe81fb6594908fb02ab8dbc8ac6`。
- 本机预览约 1.68–1.69 秒，确认约 1.54–1.82 秒。
- 反序重传和同 token 重试均未新增事实；100 行列表为 3 次 SELECT，汇总为 2 次 SELECT。

复验命令：`python reports/verify_smart_samples.py --commit --reverse`。脚本不复制或打印私人交易明细。

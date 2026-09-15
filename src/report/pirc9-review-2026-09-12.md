# PIRC-9 前后端一致性检查

检查对象：PAAM `5d051bc`（Remove PIRC-9 compatibility stack）。本报告描述当前版本可复现的缺陷，不将未经前后版本对照的问题断言为本次某个提交新引入。

结论：基础链路通过，但当前版本不宜视为完成验收。金额语义、跨页面异步响应、弹窗版本和组合状态存在缺陷。

## 验证环境与结果

- Windows、Python 3.10 独立 `.venv`、真实无头 Edge、Uvicorn HTTP 服务。
- 所有测试写入临时 SQLite；未操作开发账本或真实账单。
- 原有测试：50 项通过；覆盖导入、事实冲突、七种财务 Review、标签、账户、投影查询、表结构等。
- 原有浏览器冒烟：导入、流水、详情和 11 张表隔离通过。
- JavaScript 语法检查通过。
- 新增检查最终结果：11 项中 1 项通过、10 项失败；10 个失败均落在预期业务/界面断言，未将环境或测试脚本错误计作缺陷。普通财务审查的按钮生命周期通过。
- 新增独立检查文件：[test_pirc9_regression.py](test_pirc9_regression.py)。这些是以正确行为为断言的缺陷复现测试，当前失败是检查结果，不代表已修复；不在默认 `testpaths = src/test` 中。
- 规则依据：仓库 fact-layer、review-layer、ledger-api 模块技能与 `src/doc/data-model.md`；特别核对不可变事实、显式分配、原子投影和并发版本。

## 缺陷与建议

### 1. P1：部分退款分配未控制实际退款抵扣

- 复现：导入退款 100 元、原支出 300 元；创建 REFUND，两个成员各明确分配 60 元，确认。
- 实际：Review 保存了 60 元分配，但汇总 `refund_offset_value=10000`，按 100 元退款抵扣。
- 应有：已确认退款分配为 60 元时，退款抵扣为 60 元；剩余金额应遵循明确的未分配政策，不能静默视为全部已确认。
- 原因：[target_review_projection_service.py](../backend/service/target_review_projection_service.py) `publish()` 第 35–43 行直接把完整 Fact 传给 `_leg()`，金额计算未使用 Review Line 的分配。
- 建议：明确并实现分配和剩余金额的投影/汇总口径；补充分配 60、更新为 40、撤销、恢复及超额分配测试。
- 对应用例：`test_partial_refund_honors_confirmed_allocation`。

### 2. P1：无关 AA 事项在汇总前被合并抵消

- 复现：事项 A 支付 100 元、收回 60 元；事项 B 支付 50 元、收回 100 元，分别确认。
- 应有：按当前单事项净额规则，应分别计支出 40 元、收入 50 元。
- 实际：先按类型累计为流入 160 元、流出 150 元，再取正负，得到收入 10 元、支出 0 元。净额仍是 10 元，但“实际收入/实际支出”失真。
- 原因：[target_ledger_summary_service.py](../backend/service/target_ledger_summary_service.py) 第 38–43 行先按类型、币种聚合，再在 `_business_totals()` 第 93 行计算正负净额。日期分组同样不能保证逐事项口径。
- 建议：先逐 Ledger 计算业务收入/支出，再累计；同类型活动现金流仍可独立汇总。
- 对应用例：`test_summary_does_not_net_unrelated_aa_cases`。

### 3. P2：快速切页时旧请求覆盖当前页面

- 复现：延迟流水列表响应，立即切换标签页，等标签页显示后释放流水响应。
- 实际：地址和标题仍为标签页，内容被流水页覆盖，“新建维度”等操作消失。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) `render()` 第 117–139 行在 await 后无请求序号/路由一致性校验，成功及错误响应均可能覆盖新页面。
- 建议：为每轮渲染捕获路由参数和递增序号，只有最新请求可落 DOM；必要时取消旧请求。
- 对应用例：`test_navigation_ignores_late_previous_page_response`。

### 4. P2：子弹窗撤销成功，父详情仍持有旧投影和旧版本

- 复现：打开 AA 流水详情，再打开相关审查，点击撤销。
- 实际：审查弹窗关闭，父流水弹窗仍显示原 AA 和版本 2；后台流水已恢复普通支出、版本 3。父弹窗的标签/账户操作继续携带版本 2，会被后端拒绝。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) `transitionReview()` 第 402–404 行只关闭最近的 dialog，`render()` 仅刷新底层页面。账户和冲突 transition 采用同样模式。
- 建议：变更后关闭或重新获取所有受影响详情；若投影合并/拆分导致 ID 变化，跳回有效列表并解释变化。
- 对应用例：`test_nested_review_transition_refreshes_parent_detail`，实际观测 `shown=2, current=3`。

### 5. P2：已撤销财务审查无法从前端编辑

- 复现：创建、确认、撤销 AA，再打开审查详情。
- 实际：只有“恢复”，没有“编辑”；`editReview()` 本身也只接受 PENDING。
- 后端：[target_review_service.py](../backend/service/target_review_service.py) 第 203 行明确允许 PENDING、REVOKED 的 update。
- 前端：[target-ledger.js](../frontend/js/view/ledger.js) 第 277、295–298 行阻断了后端支持的“撤销后修订”流程，用户只能恢复旧决定或另建事项。
- 建议：对齐前后端状态转换规则，支持撤销后修订、再确认。
- 对应用例：`test_revoked_review_has_edit_action`。

### 6. P2：账户编辑框回填原始账户，可能覆盖已生效修正

- 复现：把 `original-wallet` 修正为 `corrected-wallet`；重新打开流水详情和“修正账户”。
- 实际：列表的有效账户正确，但编辑框仍填 `original-wallet`。直接保存会把有效账户修回旧值。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) 第 170 行把不可变 Fact 的 `account_code` 当作编辑默认值。展示“原始账户”本身合理，但编辑应使用该 Fact 的有效 ACCOUNT Review。
- 建议：详情明确返回每个 Fact 的原始/有效账户，编辑回填后者；不能对多 Fact 流水简单使用聚合的 MULTIPLE。
- 对应用例：`test_account_editor_prefills_effective_account`。

### 7. P2：离开导入页后，已生成的预览无法继续确认

- 复现：上传账单完成预览，切到标签页，再返回导入。
- 实际：预览和确认按钮消失；内存 `state.importPlan` 尚在，但返回时未重新渲染，只能重新选文件和预览。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) `importPage()` 第 193–197 行只返回空的 `#import-preview`；渲染完成后未调用 `renderImportPlan()`。
- 建议：返回时恢复有效预览，过期或账本已变化时提供重新计算入口；同时处理多个预览请求的乱序响应。
- 对应用例：`test_import_preview_survives_navigation`。

### 8. P2：重复提交创建两个 Review

- 复现：同一“新建审查”表单在前一请求完成前连续触发两次提交。
- 实际：数据库出现两个不同的 PENDING Review，而不是同一命令重放。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) `submitReview()` 第 386–392 行无提交中状态，且每次提交重新生成幂等键。
- 建议：提交期间锁定表单，对同一未决命令复用幂等键；网络错误重试也不能直接换键。后端已有幂等保护，但当前 UI 无法利用它处理同一次操作的重试。
- 对应用例：`test_double_submit_creates_one_review`，实际 `len(cases)=2`。尚未声称其他所有表单均产生重复写入。

### 9. P2：归档标签维度在财务合并后恢复，返回 HTTP 500

- 复现：两笔 Fact 的同一维度分别为 food、unclassified；归档该维度；确认两笔的 TRANSFER 合并；恢复维度。
- 实际：返回 `500 Internal Server Error`。维度仍归档，用户看不到具体标签冲突及恢复路径。
- 原因：[target_tag_projection_service.py](../backend/service/target_tag_projection_service.py) 第 52–56 行发现合并流水的历史标签不一致并抛 ValueError；[target_tag_service.py](../backend/service/target_tag_service.py) `set_view_status()` 没有转为领域错误，而 Controller 只捕获 TargetTagError。
- 建议：保留事务一致性，返回结构化 409/422 和冲突对象；提供先统一标签或撤销合并的可达流程，再允许恢复。不能直接选择一个标签掩盖冲突。
- 对应用例：`test_tag_restore_conflict_is_actionable`；该用例接受成功或结构化的业务拒绝，不要求忽略冲突强行恢复。

### 10. P1：超过 200 条 Review 后，旧待处理冲突失去入口

- 复现：先存在一个 PENDING FACT_CONFLICT，再新增 200 个 Review（批量标签分配也会产生许多 TAG Review），打开审查页。
- 实际：只展示 ID 最新的 200 条，没有分页、状态筛选、搜索或截断提示。旧冲突没有已接受 Fact 时，也不能靠流水详情找回。
- 原因：[target-ledger.js](../frontend/js/view/ledger.js) 第 264 行固定 `limit=200`；[target_review_mapper.py](../backend/mapper/target_review_mapper.py) `list()` 按 ID 倒序截断；[target_review.py](../backend/router/target_review.py) 第 106–113 行无游标/offset 参数。
- 建议：后端提供分页及状态筛选，前端显示总数和分页；优先提供待处理工作队列，避免 TAG/ACCOUNT 历史淹没未解决冲突。
- 对应用例：`test_older_pending_review_is_reachable`。

## 可重复执行

在 PAAM 目录执行：

```powershell
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe src/script/verify_target_ui.py
./.venv/Scripts/python.exe -m pytest src/report/test_pirc9_regression.py -q --tb=short
node --check src/frontend/target-ledger.js
```

独立检查还包括 `test_financial_review_ui_lifecycle_updates_ledger`：通过真实按钮完成创建、确认、撤销、恢复，并在每一步核对 HTTP 流水与汇总。该正常路径与嵌套详情陈旧问题分别验证。

本次没有修改业务实现、提交或推送。没有对真实账单、多进程部署、长时间压力、全浏览器兼容性或所有网络中断组合做穷尽验证；通过现有测试不等同于无回归。

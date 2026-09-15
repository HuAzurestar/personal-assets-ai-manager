# PIRC-9 验收记录

初次验收日期：2026-09-13；当前契约更新：2026-09-15。

## 当前状态

- 正式入口：`backend.target_main:app`。
- SQLite：准确 10 张目标表，没有显式外键；启动时原位迁移旧物理表。
- 代码：LedgerEntry 只发布 TRANSACTION、ACCOUNT_TRANSFER、CLAIM_CASHFLOW；旧列、旧读取 API 与 `ledger_entry_source` 已删除。
- 自动测试：覆盖 V2 创建、确认、更新、撤销、恢复、幂等、拆分、换汇、账户与标签联动，以及旧表到 11 字段 LedgerEntry 的迁移。
- JavaScript：`node --check` 通过。
- JavaScript 工作台：经济列表、按币种汇总、三段式分配矩阵、确认、撤销和恢复均已接入 V2 API。

## 真实样本

使用 `E:\Worktable\Download` 的 7 份支付宝、微信、建行、农行和招行文件，在临时空库完成正序/逆序验证：

- 原始记录：827。
- 规范 Fact：803。
- 热投影：803。
- 补充证据链接：17。
- 非入账记录：7。
- 解析错误：0。
- 正逆序 canonical signature：`a43d8a2aafd669dfaf913f7f7d257c513ec8abe81fb6594908fb02ab8dbc8ac6`。
- 最近一次预览：1.697 秒；确认：1.445 秒。
- 100 行流水分页：3 次 SELECT。
- 汇总：2 次 SELECT。

上述真实样本结果是旧 API 退役前留下的历史验收证据；旧验证脚本已随旧接口删除，不能作为当前可重复命令。

## 功能链路

- 导入：多文件预览、修订、原子确认、重复文件幂等、原始证据留存。
- 事实冲突：不改已接受 Fact；写入异常 Raw 和待审 Case，可连接旧 Fact 或创建新 Fact。
- Review：一次账本审查包含事实集合、LedgerEntry 集合和 Allocation 矩阵；行为说明与 Entry Type 分离。
- 账本：一个 Fact 可拆成多条 LedgerEntry，但一条 LedgerEntry 只对应一个 Fact；严格守恒并支持默认 TRANSACTION 回补。CLAIM_CASHFLOW 当前仅分类现金流水，不维护资产负债余额。
- 标签：字典、默认值、审查历史、热投影关联。
- 详情：由一个 `ledger_entry` 通过 Allocation 确定唯一 Fact，并批量取得 Raw、Import 和相关 Review。

## 重构后 Docker 回归

- 镜像：`paam:pirc9-final`，从当前 `requirements.txt` 与 `app/` 独立构建。
- 容器：`paam-pirc9-final2-fd82f6ef`，仅监听 `127.0.0.1:18770`，使用独立空数据卷。
- HTTP/无头 Edge 验收：15 项全部通过，覆盖快速切页、预览保留、部分金额、AA 汇总、嵌套弹窗、撤销后编辑、账户有效值、双击提交、标签恢复冲突、导入幂等、分页筛选、事实冲突和弹窗错误可见性。
- JUnit 结果：`src/report/docker-evidence/final-accepted-result.xml`；场景截图位于 `src/report/docker-evidence/`。

启动时自动升级为 10 表结构，并为尚无确认覆盖的历史 Fact 建立 DEFAULT Review 与等额 TRANSACTION。旧 Ledger 行先收敛为 11 个字段，来源追溯统一改由 Allocation 完成。

## 可重复命令

```powershell
node --check src/frontend/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q src/backend src/script src/test
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe src/script/verify_target_ui.py
```

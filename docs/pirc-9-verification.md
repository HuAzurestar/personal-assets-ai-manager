# PIRC-9 验收记录

验收日期：2026-09-13。

## 当前状态

- 正式入口：`app.target_main:app`。
- SQLite：准确 11 张目标表，没有显式外键；V2 采用现有物理表原位迁移。
- 代码：经济层只发布 TRANSACTION、ACCOUNT_TRANSFER、CLAIM；旧列/API 仅作迁移兼容。
- 自动测试：覆盖旧链路以及 V2 垫付、借贷、部分分配、幂等、换汇、冲正和旧聚合迁移。
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

脚本不复制或打印私人交易明细：

```powershell
.\.venv\Scripts\python.exe reports/verify_smart_samples.py --commit --reverse
```

## 功能链路

- 导入：多文件预览、修订、原子确认、重复文件幂等、原始证据留存。
- 事实冲突：不改已接受 Fact；写入异常 Raw 和待审 Case，可连接旧 Fact 或创建新 Fact。
- Review：一次经济审查包含事实集合、经济集合和 Allocation 矩阵；行为说明与 Economic Type 分离。
- 经济：支持 N:M Fact/Economic 分配、严格 Fact 金额守恒、默认 TRANSACTION 回补、CLAIM 余额和 TRANSACTION 冲正。
- 标签：字典、默认值、审查历史、热投影关联。
- 详情：由一个 `ledger_entry` 确定其全部 Fact、Raw、Import 和相关 Review。

## 重构后 Docker 回归

- 镜像：`paam:pirc9-final`，从当前 `requirements.txt` 与 `app/` 独立构建。
- 容器：`paam-pirc9-final2-fd82f6ef`，仅监听 `127.0.0.1:18770`，使用独立空数据卷。
- HTTP/无头 Edge 验收：15 项全部通过，覆盖快速切页、预览保留、部分金额、AA 汇总、嵌套弹窗、撤销后编辑、账户有效值、双击提交、标签恢复冲突、导入幂等、分页筛选、事实冲突和弹窗错误可见性。
- JUnit 结果：`reports/docker-evidence/final-accepted-results.xml`；场景截图位于 `reports/docker-evidence/`。

启动时自动升级现有 11 表结构，并为尚无 V2 覆盖的历史 Fact 建立 DEFAULT Review 与等额 TRANSACTION。共享多个 Fact 的旧聚合行不会被误复用为单条 Economic。

## 可重复命令

```powershell
node --check app/static/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_target_ui.py
```

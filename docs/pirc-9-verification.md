# PIRC-9 验收记录

验收日期：2026-09-12。

## 当前状态

- 正式入口：`app.target_main:app`。
- SQLite：准确 11 张目标表，没有旧表、兼容表或显式外键。
- 代码：旧 ORM/API/Service/Mapper/迁移器/页面已物理删除。
- 自动测试：50 项通过。
- JavaScript：`node --check` 通过。
- 浏览器：导入、流水、详情和 11 表隔离通过。

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
- Review：统一 Case/Line/History，支持创建、修改、确认、撤销、恢复。
- 投影：普通收支与 AA、借贷、退款、转账、换汇、重复等多态结果。
- 标签：字典、默认值、审查历史、热投影关联。
- 详情：由一个 `ledger_entry` 确定其全部 Fact、Raw、Import 和相关 Review。

## 可重复命令

```powershell
node --check app/static/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/verify_target_ui.py
```

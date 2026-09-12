# PAAM

PAAM 是一个 SQLite + Python 的分层模块化账本。PIRC-9 已完成从旧多表模型到“事实、审查、热投影”三层模型的切换，当前数据库固定为 11 张表。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
.\.venv\Scripts\python.exe run.py
```

入口为 `app.target_main:app`，浏览器访问 `http://127.0.0.1:8765`。正式接口只使用：

- `/paam/import/v1`
- `/paam/ledger/v1`
- `/paam/review/v1`
- `/paam/tag/v1`

## 架构

后端依赖方向固定为：

`Controller -> Service -> Data Mapper -> Model / SQLite`

- Controller：HTTP 参数、状态码和 DTO 校验。
- Service：业务规则、事务、幂等和投影更新。
- Mapper：显式字段 SQL、批量查询和 VO 组装。
- Model：只定义 11 张目标表。

不存在 Repository 层、显式 SQL 外键、DTO 内 SQL、循环 `get(id)` 或列表 `SELECT *`。Python 当前不是性能瓶颈；热列表固定 3 次 SELECT，汇总固定 2 次 SELECT，导入确认的 SELECT 数不随导入行数线性增长。

## 文档

- [11 表逐字段字典](docs/data-model.md)
- [SQL 重构清单](docs/sql-query-refactor.md)
- [PIRC-9 验收记录](docs/pirc-9-verification.md)

项目开发规则在 `AGENTS.md`，模块规则在 `.agents/skills/`。

## 验证

```powershell
node --check app/static/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe reports/verify_smart_samples.py --commit --reverse
.\.venv\Scripts\python.exe scripts/verify_target_ui.py
```

开发库没有生产数据，需要清空重建时可直接执行：

```powershell
.\.venv\Scripts\python.exe scripts/reset_target_database.py data/personal-assets-ai-manager.db --confirm-empty-reset
```

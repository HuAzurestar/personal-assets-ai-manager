# PAAM

PAAM 是一个 SQLite + Python 的分层模块化账本。当前模型按“事实 → 审查 → 账本”分层，TransactionFact 与 LedgerEntry 通过 Review 下的 Allocation 三元关系连接；数据库固定为 10 张表。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File src/script/setup.ps1
.\.venv\Scripts\python.exe run.py
```

入口为 `backend.target_main:app`，浏览器访问 `http://127.0.0.1:8765`。
`run.py` 自动加入 `src` 搜索路径；直接使用 Uvicorn 时运行
`python -m uvicorn backend.target_main:app --app-dir src --port 8765`。
正式接口只使用：

- `/paam/import/v1`
- `/paam/ledger/v2`
- `/paam/review/v2`（账本审查）
- `/paam/review/v1`（账户与事实冲突等辅助审查）
- `/paam/tag/v1`

## 架构

后端依赖方向固定为：

`Router -> Service -> Mapper -> Entity / SQLite`

- Router：HTTP 参数、状态码和 DTO 校验。
- Service：业务规则、事务、幂等和投影更新。
- Mapper：显式字段 SQL、批量查询和 VO 组装。
- Entity：按表拆分，只定义 10 张目标表。
- Parser：文件解析、来源字段解释，返回解析结果，由 Service 编排调用。

## 文件结构

```text
PAAM/
├── run.py
├── pytest.ini
├── requirements.txt
├── data/
└── src/
    ├── doc/
    ├── script/
    ├── test/
    ├── report/
    ├── asset/provider/
    ├── frontend/
    │   ├── target.html
    │   ├── target-ledger.js
    │   ├── css/
    │   └── js/                 # api、util、component、state、view
    └── backend/
        ├── core/
        ├── entity/
        ├── schema/
        ├── mapper/
        ├── service/
        ├── router/
        ├── parser/
        ├── target_main.py
        └── smart_import.py
```

自定义目录使用单数；工具约定目录和文件（例如 `.github/workflows`、
`.agents/skills`、`requirements.txt`）保留。Schema 仍按原业务文件组织；
Mapper 方法和导入计划的现有调用关系不变。
源码运行的数据仍位于项目根目录 `data/`，打包后的数据位于可执行文件旁的
`data/`；`PAAM_DATA_DIR` 和 `PAAM_DATABASE_URL` 仍可覆盖默认值。
前端通过 `/static/` 提供，图片通过 `/asset/` 提供。

不存在 Repository 层、显式 SQL 外键、DTO 内 SQL、循环 `get(id)` 或列表 `SELECT *`。金额使用整数值、精度与币种；经济层只允许 `TRANSACTION`、`ACCOUNT_TRANSFER`、`CLAIM`，不保存汇率，也不跨币种汇总。

## 文档

- [10 表逐字段字典](src/doc/data-model.md)
- [SQL 重构清单](src/doc/sql-query-refactor.md)
- [PIRC-9 验收记录](src/doc/pirc-9-verification.md)
- [文件迁移清单](src/doc/file-migration.md)

项目开发规则在 `AGENTS.md`，模块规则在 `.agents/skills/`。

## 验证

```powershell
node --check src/frontend/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q src/backend src/script src/test
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe src/script/verify_target_ui.py
```

以下命令会清空指定数据库，仅在明确需要重建时执行：

```powershell
.\.venv\Scripts\python.exe src/script/reset_target_database.py data/personal-assets-ai-manager.db --confirm-empty-reset
```

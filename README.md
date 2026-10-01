# PAAM

PAAM 是一个 SQLite + Python 的分层模块化账本。PIRC-35 模型为“来源 Fact → 不可变 Review → 现金 Flow / 数量 Position”，用两段分配关联；同一 SQLite 共 20 张表，包含三层账户身份、自动标签配置/审批和模型审计。现有 v1 原位升级，没有新建 ledger view status 或第二套账本。

## 启动

```powershell
powershell -ExecutionPolicy Bypass -File src/script/setup.ps1
.\.venv\Scripts\python.exe run.py
```

入口为 `backend.target_main:app`，浏览器访问 `http://127.0.0.1:8765`。
本地 PR 预览 Docker 镜像在同一端口的 `/sql/` 提供只读 SQLite 浏览器；
例如 `http://127.0.0.1:18779/sql/`。使用其他启动方式时需显式设置
`PAAM_SQL_WEB_ENABLED=1`。该页面可读取完整账本，端口只能绑定本机；
详情见 [模型调用审计](src/doc/llm-prompt-audit.md)。
`run.py` 自动加入 `src` 搜索路径；直接使用 Uvicorn 时运行
`python -m uvicorn backend.target_main:app --app-dir src --port 8765`。
正式接口只使用：

- `/paam/import/v1`
- `/paam/ledger/v1`（事实、Flow 与经济审查）
- `/paam/financial/v1`（Position 对象和有据数量）
- `/paam/tag/v1`
- `/paam/system/v1`（设置与共享调度诊断）

## 架构

后端依赖方向固定为：

`Router -> Service -> Mapper -> Entity / SQLite`

- Router：HTTP 参数、状态码和 DTO 校验。
- Service：业务规则、短串行事务、预览校验和不可变发布。
- Mapper：显式字段 SQL、批量查询和 VO 组装。
- Entity：按表拆分，定义 20 张目标表并使用当前物理表名。
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
导入预览的提示性超时默认是 30 分钟，可用
`PAAM_IMPORT_PREVIEW_TIMEOUT_MINUTES` 为特殊任务延长；超时只会把尚未确认的
Import File 标记为 `FAILED`；缓存未逐出时仍能明确选择行确认。逐出或重启后须重新预览，复用相同文件与来源行，已确认批次不丢失。
前端通过 `/static/` 提供，图片通过 `/asset/` 提供。

不存在 Repository 层、显式 SQL 外键、DTO 内 SQL、逐业务行查询或列表 `SELECT *`。现金用整数/单位，类型为 `TRANSACTION`、`ACCOUNT_TRANSFER`、`ASSET_LIABILITY`、`DUPLICATE`；重复证据保留但不计金额。Position 数量独立，不是余额快照、净值或估值；不保存汇率或跨币种汇总。写响应未知先核对实际状态，不自动重发。

旧库不得直接启动本分支。应用拒绝旧 `review_allocation` 或非规范时间，升级只在独立新副本进行；不启动时回填业务、修补覆盖或切库。详见 [PIRC-35 安全试用与副本迁移](src/doc/pirc35-delivery.md)。

## 文档

- [自动标签部署与就绪检查](src/doc/auto-tag-deployment.md)：部署层保持可用，在 Settings 中控制自动分析。
- [Docker 模型凭据](src/doc/model-credential-deployment.md)
- [标签分类配置](src/doc/tag-classification.md)
- [模型调用审计](src/doc/llm-prompt-audit.md)
- [PIRC-24 最终验收](src/report/pirc24-acceptance.md)
- [20 表模型与物理字段入口](src/doc/data-model.md)
- [PIRC-35 安全试用与副本迁移](src/doc/pirc35-delivery.md)
- [SQL 重构清单](src/doc/sql-query-refactor.md)
- [测试入口与人工验收边界](src/test/README.md)

项目开发规则在 `AGENTS.md`，模块规则在 `.agents/skills/`。

## 验证

```powershell
node --check src/frontend/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q src/backend src/script src/test
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest src/test/test_browser.py --run-browser -q
```

浏览器测试需安装 Playwright 和浏览器，完整命令见测试说明。普通 `pytest`
默认跳过九组浏览器集成测试；完整复验须加 `--run-browser`，CI 单独保存 JUnit。运维工具保留在
`src/script`，可重复的测试和夹具统一位于 `src/test`。原始截图、XML 等生成物
放到被 Git 忽略的 `artifacts/`；历史验收过程可通过 Git 历史恢复。

以下命令会清空指定数据库，仅在明确需要重建时执行：

```powershell
.\.venv\Scripts\python.exe src/script/reset_target_database.py data/personal-assets-ai-manager.db --confirm-empty-reset
```

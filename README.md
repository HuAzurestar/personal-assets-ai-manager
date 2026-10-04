# PAAM

PAAM 是一个 SQLite + Python 的分层模块化账本。当前模型按“事实 → 审查 → 经济”分层，Fact 与 Economic 通过 Review 下的 Allocation 三元关系连接；数据库固定为 14 张表，并在同一 SQLite 中保存自动标签设置、规则、审查请求和模型调用审计。

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
- `/paam/tag/v1`
- `/paam/system/v1`（设置与共享调度诊断）

## 架构

后端依赖方向固定为：

`Router -> Service -> Mapper -> Entity / SQLite`

- Router：HTTP 参数、状态码和 DTO 校验。
- Service：业务规则、事务、幂等和投影更新。
- Mapper：显式字段 SQL、批量查询和 VO 组装。
- Entity：按表拆分，只定义 14 张目标表并使用当前物理表名。
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
Import File 标记为 `FAILED`，仍在当前进程中的预览仍可继续修订或确认。
前端通过 `/static/` 提供，图片通过 `/asset/` 提供。

不存在 Repository 层、显式 SQL 外键、DTO 内 SQL、循环 `get(id)` 或列表 `SELECT *`。金额使用整数值、精度与币种；经济层只允许 `INCOME_AND_EXPENSE`、`INTERNAL_TRANSFER`、`ASSET_AND_LIABILITY`，不保存汇率，也不跨币种汇总。

## 文档

- [自动标签部署与就绪检查](src/doc/auto-tag-deployment.md)：部署层保持可用，在 Settings 中控制自动分析。
- [Docker 模型凭据](src/doc/model-credential-deployment.md)
- [标签分类配置](src/doc/tag-classification.md)
- [模型调用审计](src/doc/llm-prompt-audit.md)
- [PIRC-24 最终验收](src/report/pirc24-acceptance.md)
- [14 表逐字段字典](src/doc/data-model.md)
- [SQL 重构清单](src/doc/sql-query-refactor.md)
- [测试入口与人工验收边界](src/test/README.md)

项目开发规则在 `AGENTS.md`，模块规则在 `.agents/skills/`。

## 验证

PIRC-40 将公共能力集中到 `src/middleware/{llm,schedule,config}`，由 backend bootstrap
注入凭据与 SQLite 审计端口；仍只有现有 APScheduler/FIFO 引擎和 14 张表。
标签任务固定同次短读的规则/模型/披露/模板依据，再执行一次生成请求。
调用成功与业务校验/提交分开记录；未确定调用保守阻断，提交失败优先恢复已保存响应。

- Prompt 首期是随部署加载的只读文件资产；`/paam/system/v1/llm/prompt/*`
  提供列表、详情、引用和服务端虚构预览，不调用模型。规则指引仍独立编辑。
- `/paam/system/v1/llm/call/list` 与 `/call/statistics` 不查询正文。
  敏感 `/call/{id}` 默认禁止；仅在部署 `PAAM_LLM_AUDIT_DETAIL_TOKEN`
  （至少 32 字符）且请求提供匹配 `X-PAAM-Audit-Token` 时允许，不能通过 URL 传令牌。
- 模型目标默认 HTTPS；本地/内网明文 HTTP 必须在该保存连接上明确选择
  `allow_insecure_http=true`。生成、目录及连接检查均拒绝 URL 内嵌凭据和自动重定向。
  `PAAM_LLM_PROXY` 是显式部署代理，普通 HTTP_PROXY/HTTPS_PROXY 不被使用。
- `PAAM_SCAN_ENABLED=true/false`（严格 JSON 布尔值）可覆盖保存开关；
  设置页及 `/paam/system/v1/config` 区分保存、有效来源与安装状态。
  共享引擎的 `system:config-reconcile` 有界检查负责收敛，无第二条定时循环。
- pause 不取消已准入当前项；cancel/remove 撤销未提交结果。取消等待后仍排空真实
  SDK worker，不能立即复用同 key/全局 SDK 锁。默认部署仍限定单进程。

SDK 验收链固定为 LiteLLM 1.102.0 / OpenAI 2.54.0 / httpx 0.28.1 /
httpcore 1.0.9 / tiktoken 0.14.0；这不是完整传递依赖锁文件。
新增 `test_middleware_*.py` 和 `test_llm_audit_migration.py` 使用本地可计数 HTTP stub
及虚构数据，不访问真实模型。非标签月度摘要与非 LLM housekeeping 仅作独立扩展证明，
没有开放任意执行/重扫 API。

```powershell
node --check src/frontend/target-ledger.js
.\.venv\Scripts\python.exe -m compileall -q src/backend src/script src/test
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest src/test/test_browser.py --run-browser -q
```

浏览器测试需安装 Playwright 和浏览器，完整命令见测试说明。普通 `pytest`
默认跳过这四组浏览器集成测试；CI 单独运行并保存 JUnit 结果。运维工具保留在
`src/script`，可重复的测试和夹具统一位于 `src/test`。原始截图、XML 等生成物
放到被 Git 忽略的 `artifacts/`；历史验收过程可通过 Git 历史恢复。

以下命令会清空指定数据库，仅在明确需要重建时执行：

```powershell
.\.venv\Scripts\python.exe src/script/reset_target_database.py data/personal-assets-ai-manager.db --confirm-empty-reset
```

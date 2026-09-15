# 文件结构迁移验收

本次仅迁移文件、按原有边界拆分文件，以及适配运行路径。
迁移表见 [文件迁移清单](../doc/file-migration.md)。未调整 API 契约、数据库表结构、
业务类名、Mapper 方法归属、事务和导入规则。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 迁移前默认测试 | 67 passed |
| 迁移后默认测试 | 69 passed（新增 2 项资源/数据路径验证） |
| 迁移前浏览器回归 | 10 passed，1 failed |
| 迁移后浏览器回归 | 11 passed |
| 独立 target UI 验收 | PASS：Fact、Review、Economic、11 表隔离 |
| Python 编译、全部前端 JS 语法、PowerShell 语法 | 通过 |
| Entity 定义比对 | 11 个 Entity 和 TargetTable 的 AST 完全一致 |
| 业务模块比对 | 42 个 Service/Mapper/Schema/解析及工具模块仅导入变化 |
| 图片和历史 XML | 内容保留；文本比较忽略 Git 换行转换 |
| 源码启动 | 从无关工作目录启动，健康检查、模板、模块、样式、图片通过 |
| Windows 打包启动 | 独立 exe 启动，健康检查、模板、模块、样式、图片通过 |
| Docker | 镜像构建及启动、资源、预览、确认导入、账本读取通过 |
| Docker 浏览器测试脚本 | 15 项可收集；本次未执行该历史套件 |

基线浏览器失败为 `test_financial_review_ui_lifecycle_updates_ledger`：
导航回工作台后未找到预期 review-detail 按钮。迁移后未复现；未修改该用例的
业务函数或断言，因此不将其报告为本次修复。

默认测试保留两项依赖弃用警告（Starlette TestClient/httpx、AnyIO portal），
与迁移前一致。验证使用独立 `.venv-migration` 环境及临时 SQLite 数据库。

## 配套处理

- Python 启动入口和独立维护脚本按自身位置定位 `src` 和项目根目录。
- 打包脚本显式收集 `backend.target_main`，解决字符串入口无法自动发现的问题。
- 模板和静态资源从独立资源目录加载；源码默认数据仍位于项目根目录的 data。
- 更新入口资源版本，避免旧浏览器缓存继续导入已迁移的 JS 路径。
- 更新 pytest、CI、Docker、维护脚本、说明文档和模块规则中的路径。
- 原顶层 app/docs/scripts/tests/reports 的剩余生成缓存归档到
  `.venv-migration/obsolete-tree/`，可以恢复；源码均在 src 对应目录中。
- 历史 `compare_import_order.py`、`verify_target_sample.py` 在迁移前已依赖不存在的
  旧 app.database/app.main，保留为历史探针，不改写其业务。真实账单样本探针
  依赖本机外部样本目录，本次未运行。

## 重复验证

在 PAAM 根目录，使用已安装 requirements.txt 的 Python 环境：

```powershell
python -m pytest -q
python -m pytest src/report/test_pirc9_regression.py -q
python src/script/verify_target_ui.py
python src/script/build.py
docker build -f src/report/Dockerfile.pirc9 -t paam-layout-check .
```

浏览器验证另需 Playwright 与 Edge；打包另需 PyInstaller。

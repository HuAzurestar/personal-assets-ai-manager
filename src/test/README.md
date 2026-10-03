# 测试入口

在仓库根目录运行。业务实现仍位于 backend/frontend；本目录包含单元测试、
SQLite/API 集成测试、浏览器回归及其合成数据夹具。

## 默认离线测试

```text
python -m pytest -q --junitxml=test-result.xml
node src/test/automation_ui.cjs
node src/test/automation_m2_ui.cjs
node src/test/automation_setting_form.cjs
node src/test/tag_assignment_ui.cjs
```

不要继承真实服务的数据库、凭据或调度配置。Docker 测试进程应指定独立
`PAAM_DATA_DIR`、`PAAM_SQL_WEB_ENABLED=0`、`PAAM_AUTOTAG_REAL_ANALYSIS=1`，
使用 `--network none`；测试通过夹具模拟模型。这些参数仅用于测试容器，
不能用来覆盖正在运行的应用。默认 pytest 跳过 `test_browser.py` 注册的 browser 用例，当前为十八组。

## CI 浏览器回归

```text
python -m pip install -r requirements.txt playwright==1.51.0
python -m playwright install chromium
python -m pytest src/test/test_browser.py --run-browser -q --junitxml=browser-result.xml
```

Linux CI 使用 Ubuntu 22.04，并运行 `playwright install --with-deps chromium`。
Windows 场景使用本机 Edge。`test_browser.py` 按场景启动独立 Python 进程，
不继承 `PAAM_*` 部署配置，每组使用临时 SQLite、虚构密钥及数据。默认禁止
真实模型调用。失败通过 pytest 报告；浏览器 JUnit 由 CI 保存。

十八个场景保留独立入口：`verify_pr9_fix.py`、`verify_automation_repair.py`、
`verify_refresh_ui.py`、`verify_automation_presentation.py`、`verify_m2_ui.py`、
`verify_pirc35_account.py`、`verify_pirc35_account_scope.py`、`verify_pirc35_position.py`、
`verify_pirc35_import.py`、`verify_pirc35_import_plan.py`、`verify_pirc35_flow.py`、
`verify_pirc35_review.py`、`verify_pirc35_draft.py`、`verify_pirc35_unit.py`、
`verify_pirc35_filter.py`、`verify_pirc35_scene.py`、`verify_pirc35_member.py`、
`verify_pirc35_flow_state.py`。保留子进程是为了
隔离模块加载时的全局数据库/调度状态，不把浏览器行为误称为纯单元测试。

## 扩展及人工验收

`verify_pirc35_import_plan.py` 使用独立虚构库验证2500来源行完整选择、三次只保存意图的PUT、
1000/1000/500完整只读计划、全部本地明细分页及未解决风险阻止旧单批确认；
它不证明一次确认的串行金融执行已经完成。前端 `import_plan.cjs` 也由默认pytest执行，
验证完整读取失败不部分加入、取消/迟到/总数变化拒绝、范围完整性及本地分页。

以下是扩展/历史入口，不属于上述固定浏览器回归：

- `verify_target_ui.py`：旧工作台流程，字段未按PIRC-35适配，不作为本期验收证据。
- `verify_inspection_ui.py --matrix`：旧详情矩阵，字段未按PIRC-35适配；可显式指定
  `--samples <本地账单目录>` 导入临时数据库，真实账单不提交仓库。
- `audit_pirc24_dpi.py`：多 DPI/视口矩阵，默认输出到 `artifacts/ui-dpi`。
- `serve_pirc24_usability_fixture.py` 配合 `audit_pirc24_usability.py`：历史可用性
  专用夹具，客户端检查虚构标记；不能对实际部署运行。

这些矩阵依赖浏览器、样式和人工判断，不能用字符串断言等价替换。保留历史
入口，但历史成功记录不代表此次清理后逐一重跑。后续增补 CI 时应拆成具体场景。

Docker 卷/密钥重建、真实模型连通性和人工建议质量分别保留为环境验收；已有
`test_encrypted_credential_store.py`、`test_automation_persistence.py`、
`test_setting_api.py` 和 `test_auto_tag_schedule.py` 覆盖可离线验证的合同。
不要在默认测试中访问固定的本机端口、读取个人密钥或启动计费模型。

PIRC-35固定全量：`python -m pytest src/test -q --run-browser --junitxml=artifacts/pirc35-result.xml`。
先创建输出目录，在专用环境安装依赖/浏览器；JUnit不代表独立审查或用户验收。
`fixtures/pirc35` 的六份CSV各24行，只有格式/表头来自原账单，金额、时间、账户和商户全虚构。
迁移测试创建旧结构/WAL副本并注入故障，不迁移正式库。PDF/XLS/XLSX另有合成容器测试；CSV mock不证明每份原文件都能解析。

原 `report/compare_import_order.py` 使用已删除的 app 模型和私人 803 条样本。
其可自动回归部分已迁为 `test_target_import.py` 中批量/正序/倒序合成重叠账单
测试；不能据此声称复现了历史私人数据的全部覆盖。

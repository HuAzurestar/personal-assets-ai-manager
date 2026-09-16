# 文件迁移清单（历史）

路径相对 PAAM；本清单记录当时从 `app/` 迁移到 `src/` 的路径，
不是当前文件清单。其中 Ledger V1、旧投影与 `LedgerEntrySource` 路径
已在后续重构中删除。

| 原路径 | 新路径 |
| --- | --- |
| `app/__init__.py` | `src/backend/__init__.py` |
| `app/api/controllers/__init__.py` | `src/backend/router/__init__.py` |
| `app/api/controllers/target_economic.py` | `src/backend/router/target_economic.py` |
| `app/api/controllers/target_intake.py` | `src/backend/router/target_intake.py` |
| `app/api/controllers/target_ledger.py` | `src/backend/router/target_ledger.py` |
| `app/api/controllers/target_review.py` | `src/backend/router/target_review.py` |
| `app/api/controllers/target_tag.py` | `src/backend/router/target_tag.py` |
| `app/api/target_deps.py` | `src/backend/router/target_dep.py` |
| `app/config.py` | `src/backend/core/config.py` |
| `app/core/__init__.py` | `src/backend/core/__init__.py` |
| `app/core/errors.py` | `src/backend/core/error.py` |
| `app/core/intake_preview_store.py` | `src/backend/core/intake_preview_store.py` |
| `app/file_import.py` | `src/backend/parser/file_import.py` |
| `app/importing.py` | `src/backend/parser/importing.py` |
| `app/mappers/__init__.py` | `src/backend/mapper/__init__.py` |
| `app/mappers/target_account_mapper.py` | `src/backend/mapper/target_account_mapper.py` |
| `app/mappers/target_account_projection_mapper.py` | `src/backend/mapper/target_account_projection_mapper.py` |
| `app/mappers/target_economic_mapper.py` | `src/backend/mapper/target_economic_mapper.py` |
| `app/mappers/target_economic_read_mapper.py` | `src/backend/mapper/target_economic_read_mapper.py` |
| `app/mappers/target_fact_conflict_mapper.py` | `src/backend/mapper/target_fact_conflict_mapper.py` |
| `app/mappers/target_intake_mapper.py` | `src/backend/mapper/target_intake_mapper.py` |
| `app/mappers/target_ledger_mapper.py` | `src/backend/mapper/target_ledger_mapper.py` |
| `app/mappers/target_projection_mapper.py` | `src/backend/mapper/target_projection_mapper.py` |
| `app/mappers/target_review_mapper.py` | `src/backend/mapper/target_review_mapper.py` |
| `app/mappers/target_review_projection_mapper.py` | `src/backend/mapper/target_review_projection_mapper.py` |
| `app/mappers/target_tag_assignment_mapper.py` | `src/backend/mapper/target_tag_assignment_mapper.py` |
| `app/mappers/target_tag_mapper.py` | `src/backend/mapper/target_tag_mapper.py` |
| `app/mappers/target_tag_projection_mapper.py` | `src/backend/mapper/target_tag_projection_mapper.py` |
| `app/models/__init__.py` | `src/backend/entity/__init__.py` |
| `app/models/target.py` | `src/backend/entity/`（按表拆分，见下表） |
| `app/money.py` | `src/backend/core/money.py` |
| `app/provider_templates.py` | `src/backend/parser/provider_template.py` |
| `app/schemas/__init__.py` | `src/backend/schema/__init__.py` |
| `app/schemas/intake.py` | `src/backend/schema/intake.py` |
| `app/schemas/target_economic.py` | `src/backend/schema/target_economic.py` |
| `app/schemas/target_ledger.py` | `src/backend/schema/target_ledger.py` |
| `app/schemas/target_projection.py` | `src/backend/schema/target_projection.py` |
| `app/schemas/target_review.py` | `src/backend/schema/target_review.py` |
| `app/schemas/target_tag.py` | `src/backend/schema/target_tag.py` |
| `app/services/__init__.py` | `src/backend/service/__init__.py` |
| `app/services/target_account_projection_service.py` | `src/backend/service/target_account_projection_service.py` |
| `app/services/target_account_service.py` | `src/backend/service/target_account_service.py` |
| `app/services/target_economic_read_service.py` | `src/backend/service/target_economic_read_service.py` |
| `app/services/target_economic_service.py` | `src/backend/service/target_economic_service.py` |
| `app/services/target_fact_conflict_service.py` | `src/backend/service/target_fact_conflict_service.py` |
| `app/services/target_intake_service.py` | `src/backend/service/target_intake_service.py` |
| `app/services/target_ledger_service.py` | `src/backend/service/target_ledger_service.py` |
| `app/services/target_ledger_summary_service.py` | `src/backend/service/target_ledger_summary_service.py` |
| `app/services/target_projection_service.py` | `src/backend/service/target_projection_service.py` |
| `app/services/target_review_projection_service.py` | `src/backend/service/target_review_projection_service.py` |
| `app/services/target_review_service.py` | `src/backend/service/target_review_service.py` |
| `app/services/target_tag_assignment_service.py` | `src/backend/service/target_tag_assignment_service.py` |
| `app/services/target_tag_projection_service.py` | `src/backend/service/target_tag_projection_service.py` |
| `app/services/target_tag_service.py` | `src/backend/service/target_tag_service.py` |
| `app/smart_import.py` | `src/backend/smart_import.py` |
| `app/statement_parser.py` | `src/backend/parser/statement_parser.py` |
| `app/static/ledger.css` | `src/frontend/css/ledger.css` |
| `app/static/personal-assets-ai-manager.svg` | `src/asset/personal-assets-ai-manager.svg` |
| `app/static/providers/ATTRIBUTION.md` | `src/asset/provider/ATTRIBUTION.md` |
| `app/static/providers/LICENSE-uiw-icons-MIT.txt` | `src/asset/provider/LICENSE-uiw-icons-MIT.txt` |
| `app/static/providers/alipay.svg` | `src/asset/provider/alipay.svg` |
| `app/static/providers/wechat.svg` | `src/asset/provider/wechat.svg` |
| `app/static/target-ledger.js` | `src/frontend/target-ledger.js` |
| `app/static/target.css` | `src/frontend/css/target.css` |
| `app/static/target/accounts.js` | `src/frontend/js/view/account.js` |
| `app/static/target/core.js` | `src/frontend/js/util/core.js` |
| `app/static/target/navigation.js` | `src/frontend/js/navigation.js` |
| `app/static/theme.js` | `src/frontend/js/theme.js` |
| `app/static/themes.css` | `src/frontend/css/theme.css` |
| `app/target_database.py` | `src/backend/core/target_database.py` |
| `app/target_main.py` | `src/backend/target_main.py` |
| `app/templates/target.html` | `src/frontend/target.html` |
| `docs/data-model.md` | `src/doc/data-model.md` |
| `docs/pirc-9-verification.md` | `src/doc/pirc-9-verification.md` |
| `docs/sql-query-refactor.md` | `src/doc/sql-query-refactor.md` |
| `reports/Dockerfile.pirc9` | `src/report/Dockerfile.pirc9` |
| `reports/Dockerfile.pirc9.dockerignore` | `src/report/Dockerfile.pirc9.dockerignore` |
| `reports/compare_import_order.py` | `src/report/compare_import_order.py` |
| `reports/docker-evidence/dialog-error.xml` | `src/report/docker-evidence/dialog-error.xml` |
| `reports/docker-evidence/final-accepted-results.xml` | `src/report/docker-evidence/final-accepted-result.xml` |
| `reports/docker-evidence/results.xml` | `src/report/docker-evidence/result.xml` |
| `reports/docker-evidence/test_account_edit_reopens_with_effective_value.png` | `src/report/docker-evidence/test_account_edit_reopens_with_effective_value.png` |
| `reports/docker-evidence/test_dialog_error_is_visible_above_modal_backdrop.png` | `src/report/docker-evidence/test_dialog_error_is_visible_above_modal_backdrop.png` |
| `reports/docker-evidence/test_double_submit_is_one_command.png` | `src/report/docker-evidence/test_double_submit_is_one_command.png` |
| `reports/docker-evidence/test_fact_conflict_dismiss_reopen_resolve_via_ui.png` | `src/report/docker-evidence/test_fact_conflict_dismiss_reopen_resolve_via_ui.png` |
| `reports/docker-evidence/test_import_preview_survives_navigation.png` | `src/report/docker-evidence/test_import_preview_survives_navigation.png` |
| `reports/docker-evidence/test_ledger_filter_and_pagination.png` | `src/report/docker-evidence/test_ledger_filter_and_pagination.png` |
| `reports/docker-evidence/test_navigation_ignores_late_previous_page_response.png` | `src/report/docker-evidence/test_navigation_ignores_late_previous_page_response.png` |
| `reports/docker-evidence/test_nested_revoke_refreshes_parent_and_allows_next_operation.png` | `src/report/docker-evidence/test_nested_revoke_refreshes_parent_and_allows_next_operation.png` |
| `reports/docker-evidence/test_old_pending_case_remains_accessible_after_200_new_cases.png` | `src/report/docker-evidence/test_old_pending_case_remains_accessible_after_200_new_cases.png` |
| `reports/docker-evidence/test_revoked_review_exposes_edit.png` | `src/report/docker-evidence/test_revoked_review_exposes_edit.png` |
| `reports/docker-evidence/test_tag_restore_after_merge_returns_actionable_result.png` | `src/report/docker-evidence/test_tag_restore_after_merge_returns_actionable_result.png` |
| `reports/docker-evidence/test_ui_import_tag_account_review_and_duplicate_import.png` | `src/report/docker-evidence/test_ui_import_tag_account_review_and_duplicate_import.png` |
| `reports/pirc9-docker-acceptance.md` | `src/report/pirc9-docker-acceptance.md` |
| `reports/pirc9-review-2026-09-12.md` | `src/report/pirc9-review-2026-09-12.md` |
| `reports/test_pirc9_docker.py` | `src/report/test_pirc9_docker.py` |
| `reports/test_pirc9_regressions.py` | `src/report/test_pirc9_regression.py` |
| `reports/ui-rebuild-delivery.md` | `src/report/ui-rebuild-delivery.md` |
| `reports/ui-review-2026-09-08.md` | `src/report/ui-review-2026-09-08.md` |
| `reports/uiux-aspect-review-2026-09-08.md` | `src/report/uiux-aspect-review-2026-09-08.md` |
| `reports/uiux-depth-review-2026-09-08.md` | `src/report/uiux-depth-review-2026-09-08.md` |
| `reports/verify_smart_samples.py` | `src/report/verify_smart_sample.py` |
| `reports/verify_target_samples.py` | `src/report/verify_target_sample.py` |
| `scripts/build.py` | `src/script/build.py` |
| `scripts/import_test_samples.py` | `src/script/import_test_sample.py` |
| `scripts/reset_target_database.py` | `src/script/reset_target_database.py` |
| `scripts/run_import_test.py` | `src/script/run_import_test.py` |
| `scripts/setup.ps1` | `src/script/setup.ps1` |
| `scripts/update-and-run-dev.ps1` | `src/script/update-and-run-dev.ps1` |
| `scripts/update-and-run.ps1` | `src/script/update-and-run.ps1` |
| `scripts/verify_target_ui.py` | `src/script/verify_target_ui.py` |
| `tests/conftest.py` | `src/test/conftest.py` |
| `tests/test_query_plans.py` | `src/test/test_query_plan.py` |
| `tests/test_table_contract.py` | `src/test/test_table_contract.py` |
| `tests/test_target_account.py` | `src/test/test_target_account.py` |
| `tests/test_target_economic_v2.py` | `src/test/test_target_economic_v2.py` |
| `tests/test_target_import.py` | `src/test/test_target_import.py` |
| `tests/test_target_ledger_read.py` | `src/test/test_target_ledger_read.py` |
| `tests/test_target_ledger_summary.py` | `src/test/test_target_ledger_summary.py` |
| `tests/test_target_review.py` | `src/test/test_target_review.py` |
| `tests/test_target_runtime.py` | `src/test/test_target_runtime.py` |
| `tests/test_target_schema_migration.py` | `src/test/test_target_schema_migration.py` |
| `tests/test_target_tag.py` | `src/test/test_target_tag.py` |

## Entity 拆分

类定义、表名、字段、约束和索引不变。`entity/__init__.py` 汇总导出并注册全部表，
保留 `EconomicFlow`、`FlowAllocation` 两个现有别名。

| 原类名 | 新文件（相对 src/backend/entity） |
| --- | --- |
| TargetTable | `base.py` |
| TransactionImportFile | `transaction_import_file.py` |
| TransactionImportRow | `transaction_import_row.py` |
| TransactionFact | `transaction_fact.py` |
| ReviewCase | `review_case.py` |
| ReviewCaseBill | `review_case_bill.py` |
| ReviewHistory | `review_history.py` |
| LedgerEntry | `ledger_entry.py` |
| LedgerEntrySource | `ledger_entry_source.py` |
| TargetTagView | `tag_view.py` |
| TargetTag | `tag.py` |
| LedgerEntryTag | `ledger_entry_tag.py` |

## 前端拆分

- `target-ledger.js` 保留为入口，加载 `js/view/ledger.js` 中的原页面工作流。
- 原入口的状态整体移入 `js/state/ledger.js`，仍共享同一个状态对象。
- 原入口的 table、pager 函数移入 `js/component/table.js`。
- 原 core.js 的请求封装移入 `js/api/client.js`，toast 移入 `js/component/toast.js`。
- 其余 core.js 内容位于 `js/util/core.js`，账户展示位于 `js/view/account.js`。
- 复杂页面、弹窗、事件绑定的协作方式不重新设计。

## 路径与兼容边界

- `run.py` 自动定位 `src`，独立脚本从自身位置定位项目根目录。
- 源码默认数据库仍是 `PAAM/data/personal-assets-ai-manager.db`。
- 可执行文件的默认数据目录位于 exe 旁；资源位于打包资源目录。
- HTTP API 路径不变；前端资源使用 `/static/`，图片使用 `/asset/`。
- 工具约定名称、产品名、上游许可证名和已有生成证据名保留。
- 原 `app/api/__init__.py` 仅含包说明，迁移后不再保留这层包标记。
- `report/compare_import_order.py` 和 `report/verify_target_sample.py` 在迁移前
  已引用不存在的旧 `app.database`、`app.main`；作为历史探针保留，不改写其业务。

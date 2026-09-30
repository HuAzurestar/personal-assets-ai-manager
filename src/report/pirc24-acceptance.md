# PIRC-24 / PR #9 最终验收

检查日期：2026-09-30。远端候选 `bfbd5b7b4833842afacfe6a37b5e04a633ecba29`，
PR 分支 `feature-PIRC-24-m2-core`。本次清理不修改 backend/frontend 业务实现、
SQLite schema、模型配置、规则、用户账本或正在运行的容器。

## 交付范围

- 持久模型配置和受保护的凭据；按模型显式配置供应商和代理。
- Settings 持久自动分析开关；规则按 CRON 在共享队列扫描未分类流水。
- 脱敏输入、结构化模型输出、本地校验、逐次调用审计和安全诊断。
- 建议先进入待审申请；人工批准后生效，不自动改写金额或账务。
- 设置、规则、披露及模型版本变化保护游标和待审数据，保留人工标签。

## 最终检查

远端原候选的 [GitHub Actions](https://github.com/HuAzurestar/personal-assets-ai-manager/actions/runs/36590969242)
中 `test`、`browser` 均成功，`windows-package` 按仅 tag 构建的条件跳过。
PR 当前 open / mergeable / clean。这些状态对应原候选，不代表清理后的未推送改动。

本地清理后的本轮检查结果：

| 检查 | 结果 |
| --- | --- |
| 隔离 Docker 中全量 pytest | 655 passed、4 skipped；跳过项为单独执行的浏览器集成测试；1 条 Starlette/httpx 弃用警告 |
| `test_browser.py --run-browser` | 4 passed；本机隔离 Python 环境、Playwright 1.63 / Edge |
| 四组前端 Node 测试 | 全部通过：automation_ui、automation_m2_ui、automation_setting_form、tag_assignment_ui |
| Python 编译、前端 JS 语法、文档本地链接、Git diff 空白检查 | 通过 |
| `src/script/Dockerfile.preview` 构建 | 成功，未替换运行中的应用 |
| 已部署应用 `/api/health` | 正常，本轮不修改线上数据或配置 |

本机 JUnit 结果保存在忽略提交的 `artifacts/pr9-python.xml` 和
`artifacts/pr9-browser.xml`。CI 的浏览器环境仍为 Ubuntu / Playwright 1.45，
本机浏览器通过不能替代清理改动推送后的 CI 结果。

结论：按本文件交付范围和明确保留的限制，建议 PIRC-24 验收；本轮未发现新的阻塞项。

## 清理与迁移

| 原内容 | 处理 |
| --- | --- |
| script 中 11 个浏览器回归与夹具 | 移到 test；四个原 CI 场景通过 `test_browser.py --run-browser` 执行，并输出 JUnit |
| report 中应用 Dockerfile 和白名单 | 移到 `script/Dockerfile.preview` 及同名 `.dockerignore`，更新部署文档 |
| Dockerfile.pirc24-ui | 删除只依赖个人历史镜像的包装；虚构应用夹具保留在 test |
| compare_import_order.py | 删除失效的旧 app/私人路径实验；合成重叠账单的批量/正序/倒序验证进入默认 pytest |
| import_test_sample.py | 删除固定个人路径、端口和样本计数的导入脚本；API 导入和重复导入已有可重复测试 |
| 旧脚本的源码字符串测试 | 随被删除脚本移除；保留实际 API 合同和导入回归 |
| doc 的历史迁移清单、PIRC-9 验收、PR-8 修复计划 | 删除已完成过程记录，保留当前架构、数据字典、SQL 清单和部署说明 |
| report 中 17 份阶段性报告、旧 PNG/XML | 收敛为本记录；原文件通过 Git 历史恢复，不作为当前行为说明 |
| 当前会话的 7 个本机验收文件 | 移出仓库，保留在工作区 `acceptance-artifact/`，不把个人容器名和端口写进自动回归 |

历史 tracked 文件可以用 `git show bfbd5b7:src/report/<原文件名>` 等命令查看。
本次还生成了工作区外于仓库的 `acceptance-artifact/before-cleanup-bfbd5b7.zip`，
完整保存清理前的 doc/report/script；旧图片与 XML 也保存在该目录。没有删除
数据库、模型密钥、数据卷或运行中的部署。

## 不能替换为单元测试的验收

- 浏览器交互、焦点、滚动和 DPI：保留真实浏览器集成/人工矩阵；入口及是否纳入
  CI 见 [测试说明](../test/README.md)。未逐一重跑的扩展矩阵不声称通过。
- Docker 数据卷和独立密钥挂载：原本机验收已验证容器重建后配置/凭据保留；
  Python 测试覆盖加密、持久化和配置合同，但不替代实际挂载检查。
- 真实供应商：历史 2026-09-27 记录在自然 CRON 下得到真实建议，审批在临时库
  副本完成，主库建议保持待审。恢复该证据可读取原 `pirc24-runtime-acceptance-2026-09-27.md`。
  本轮不发起计费调用，也不根据离线测试宣称模型准确率合格。
- 私人 803 条账单：没有把私有文件复制进仓库；合成测试只覆盖通用顺序/重叠/幂等
  合同，不声称逐条复现旧样本结果。需要复测时用明确选择的账单与临时库。

## 已知限制与后续工作

用户明确将 prompt/LLM 模块收拢放到独立后续工作，不扩展本 PR。当前 prompt
与候选标签都经过保守词表过滤；未收录的自定义标签（例如本机新建的“其他”）
可能被省略，业务说明可能丢失措辞。应以 `llm_prompt_audit` 的实际消息检查，
不能把 Settings 的虚构披露示例当成真实规则预演。

PIRC-24 的原始输出合同允许多个候选建议；业务 prompt 写“选一个”并不等价于
Schema 强制单选。默认 JSON-object 路径严格校验结构/候选及理由安全性，但
不强制理由一定等于 `reason_options` 的某一项。后续统一 prompt 管理应同时
处理预览一致性、版本、单选业务约束、稳定前缀和 token/缓存成本统计。

以上限制透明保留，不因文件清理而声明已修复。最终接受这些边界并合并 PR
由用户决定；本次不会自动合并或把远端需求状态改为验收完成。

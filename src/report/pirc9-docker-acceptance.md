# PIRC-9 Docker 实际操作验收

## 环境

- 被测代码：PAAM `5d051bc`；业务代码未修复或替换。
- 镜像：`paam:pirc9-review-5d051bc`，基于 `python:3.10`，按项目 requirements 安装依赖。
- 容器：`paam-pirc9-review-fd82f6ef`。
- 地址：<http://127.0.0.1:18765>，仅绑定本机。
- 新数据卷：`paam-pirc9-review-fd82f6ef-data`，挂载 `/data`，与开发数据库隔离。
- 前端验证：宿主机真实无头 Edge 通过 HTTP 访问 Docker 内 Uvicorn/FastAPI；数据通过导入 API 或浏览器上传创建，不直接向数据库填充测试记录。
- 数据均为合成账单。账单日期按场景分开，便于对照汇总；200 条审查用于复现列表截断。

## 实际结果

验收日期：2026-09-12。

- Docker 内原有自动测试：**50 passed**，38.57 秒。
- 第一轮容器 HTTP/真实浏览器测试：**4 passed，10 failed**，61.37 秒；10 个失败全部落在业务/界面断言，与此前缺陷吻合。
- 截图检查发现错误提示被模态背景遮挡后，追加单项验证：**1 failed**，8.41 秒。合计检查 15 个场景，4 个正常场景通过、11 个缺陷场景复现；没有把测试工具或环境异常计作产品缺陷。
- 服务重启：42 条流水保持不变，返回的整页流水 JSON 及最新 200 条审查 ID 与重启前一致。
- 数据库：46 个 Fact、215 个 Review；准确包含目标 11 张表，`PRAGMA quick_check` 返回 `ok`。
- 容器内与宿主机 `target-ledger.js` SHA-256 一致：`3383acf37b886d8ebbd8198b5a4446ab241959ef306d2ce2e3db69e11f89ab64`。
- 服务已保留运行，测试数据保留在专用卷。业务代码未修改。

通过的四个场景包含多步操作：

1. 浏览器上传并确认导入；创建维度/标签；保存标签；标签及维度归档/恢复；修正账户并撤销/恢复；创建财务审查并确认/撤销/恢复；重复文件改名导入仍不增加流水；核对实际支出。
2. 30 条流水分页为 25+5 条、返回上一页、搜索无结果，HTTP 总数一致。
3. 无效账单预览不能确认，返回 422，流水总数不变。
4. 事实冲突在浏览器内忽略、重新打开、确认为新 Fact；历史追加 DISMISS/REOPEN/RESOLVE；原 Fact 的 10 元未被新证据的 20 元覆盖。

仍存在的十个已知问题：

- 退款只分配 60 元，实际抵扣 100 元。
- 两个 AA 事项应显示收入 50 元、支出 40 元，实际显示收入 10 元、支出 0 元。
- 快速切页后，旧流水响应覆盖标签页。
- 嵌套详情撤销后继续保存账户，实际返回 **409**：`ledger projection changed; reload before correcting the account`。
- 已撤销 Review 没有编辑入口。
- 账户修正后再编辑，回填的是原始账户标识。
- 双重提交生成两个 Review。
- 标签维度归档后合并流水，再恢复维度实际返回 **500**。
- 返回导入页后丢失预览确认入口。
- 旧待处理 Review 被 200 条上限截断，没有分页/筛选可找回。

### 新增缺陷：P2，弹窗内错误提示在背景后方，失败原因难以辨认

真实截图中，撤销后继续保存账户的 409 提示在模糊背景里。独立复现：新建财务审查输入 `0:AA_PAID`，提交返回 422；错误 toast 存在，但其中心点的顶层命中元素不是 toast，而是模态层，截图也确认提示被模糊。

原因：[target-ledger.js](../frontend/js/view/ledger.js) 的 `toast()` 把提示追加到 `document.body`，而 `modal()` 使用原生 `showModal()` 顶层弹窗；[ledger.css](../frontend/css/ledger.css) 第 520–522 行对 backdrop 应用模糊。普通 z-index 不能让 body 中的提示超越原生模态顶层。

建议：把表单失败原因直接显示在当前弹窗内，保留输入并引导用户重试/刷新；全局提示需要采用与当前模态层兼容的展示方式。

证据：

- [第一轮 JUnit 原始结果](docker-evidence/result.xml)。
- [错误遮挡单项结果](docker-evidence/dialog-error.xml)。
- [撤销后继续保存失败截图](docker-evidence/test_nested_revoke_refreshes_parent_and_allows_next_operation.png)。
- [表单错误提示遮挡截图](docker-evidence/test_dialog_error_is_visible_above_modal_backdrop.png)。
- [正常操作完成后的流水截图](docker-evidence/test_ui_import_tag_account_review_and_duplicate_import.png)。

## 复现文件

- [镜像定义](Dockerfile.pirc9)；[构建上下文白名单](Dockerfile.pirc9.dockerignore)。镜像只复制 requirements.txt 和 src 下的 backend、frontend、asset，不包含本地数据库、虚拟环境或真实账单。
- [容器 HTTP/浏览器验收脚本](test_pirc9_docker.py)。脚本先校验专用容器标签，再执行写操作。
- [此前的完整缺陷报告](pirc9-review-2026-09-12.md)。

## 执行命令

在 PAAM 目录构建和首次启动：

```powershell
docker build -t paam:pirc9-review-5d051bc -f src/report/Dockerfile.pirc9 .
docker volume create --label paam.purpose=pirc9-disposable-review paam-pirc9-review-fd82f6ef-data
docker run -d --name paam-pirc9-review-fd82f6ef --label paam.purpose=pirc9-disposable-review -p 127.0.0.1:18765:8765 --mount type=volume,source=paam-pirc9-review-fd82f6ef-data,target=/data paam:pirc9-review-5d051bc
./.venv/Scripts/python.exe -m pytest src/report/test_pirc9_docker.py -q --tb=short --junitxml=src/report/docker-evidence/result.xml
```

首次完整运行应使用新的空验收数据卷。整套脚本会保留测试数据；重复执行同一天的金额场景会累积金额，因此不能把非空库上的重跑当作独立验收。

本次为了同时执行原有 Linux 测试，实际启动还将 PAAM 工作目录只读挂载到 `/verification`；运行服务的代码仍来自镜像 `/app`。原有测试在独立进程中运行，按测试夹具隔离数据库：

```powershell
docker exec -e PYTHONPATH=/verification -w /verification paam-pirc9-review-fd82f6ef python -m pytest tests -q -p no:cacheprovider
```

停止或再次启动现有服务（保留测试数据）：

```powershell
docker stop paam-pirc9-review-fd82f6ef
docker start paam-pirc9-review-fd82f6ef
```

本次为诊断验收，不修改业务实现。没有进行外网暴露或生产数据迁移。

## 修复后复验

复验日期：2026-09-13。上述 11 个缺陷均已修复，并用当前工作树重新构建镜像 `paam:pirc9-final`。最终验收使用容器 `paam-pirc9-final2-fd82f6ef`、空数据卷 `paam-pirc9-final2-fd82f6ef-data` 和本机地址 <http://127.0.0.1:18770>。

- 项目测试：**54 passed**，24.83 秒。
- 静态/结构检查：`node --check`、`compileall`、`git diff --check` 和 `src/script/verify_target_ui.py` 均通过。
- 针对性回归：**11 passed**，46.89 秒。
- 全新 Docker HTTP/真实无头 Edge 验收：**15 passed**，59.97 秒。
- 最终 JUnit 证据：[final-accepted-result.xml](docker-evidence/final-accepted-result.xml)。

复验确认：部分退款按 Review line 金额投影；AA 按独立事项先净额化；旧待审事项可通过服务端筛选和分页访问；列表不再携带完整历史；快速导航和迟到请求不会覆盖新页面；修改类表单与状态按钮防止重复提交；409/422 错误在当前弹窗内可见；标签恢复冲突返回可操作的 409；撤销、恢复和继续编辑后显示值与后端有效状态一致。

最终容器保留运行以便人工查看。该容器及其数据卷只含验收生成的合成数据。由于投影规则已变化，曾由旧代码生成的开发数据库应先备份，再重建并重新导入/确认；不要把旧热投影直接当作修复后的结果。

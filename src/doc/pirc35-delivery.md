# PIRC-35 安全试用与副本迁移

本期不是单独账户页或view-tag索引迁移，而是让Fact、不可变解释、现金、账户身份和有据数量可追溯且一致。新增账户三表/Position三表，原导入/Review/标签复用升级。完整资产负债表、余额快照、净值估值、计划和绩效留给后续项目。

## 隔离试用

仓库根目录、专用Python依赖环境运行。使用独立新目录，关闭真实模型/SQL浏览器；不要继承真实数据库或密钥环境变量：

```powershell
$pirc35Trial = Join-Path $env:TEMP ('pirc35-trial-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $pirc35Trial | Out-Null
$env:PAAM_DATA_DIR = $pirc35Trial
$env:PAAM_DATABASE_URL = 'sqlite:///' + (($pirc35Trial -replace '\\','/') + '/trial.db')
$env:PAAM_AUTOTAG_REAL_ANALYSIS = '0'
$env:PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE = '0'
$env:PAAM_SQL_WEB_ENABLED = '0'
python -m uvicorn backend.target_main:app --app-dir src --host 127.0.0.1 --port 18785
```

用完停止进程、关闭该终端。访问 `http://127.0.0.1:18785`，导入 `src/test/fixtures/pirc35` 的CSV（manifest不是账单）。六schema各24行，仅沿用授权E盘样本表头/格式，账号/商户/金额/日期全虚构。CSV不覆盖原PDF/XLS/XLSX全部容器，不证明每份原文件已验证。

检查分批、单行来源和重传：新Fact默认只产生一次，重传只补证据。建立个人/集合、绑定可靠来源，换组只改当前筛选。工作台预览退款/分摊/转账/借还/重复证据等，核对现金、覆盖与独立数量；旧Review/关系可查。未知写先刷新核对，不盲重发。标签审批不改财务，改解释失效旧建议，相同金额不保证继承标签。

浏览器复验：`python -m pytest src/test/test_browser.py --run-browser -q`。当前22个注册场景自建临时库/随机本机端口，无真实provider；Windows用Edge，Linux用Chromium，见 [测试入口](../test/README.md)。测试不等于22项缺陷关闭、独立审查或用户验收。

## 超过1000行、失败与重启恢复

1000行是单次金融事务上限，不是整次导入上限。整次可明确选择最多20,000行，先保存选择并核验、查看完整处理计划，再明确批准；2500条独立纯导入行可按1000/1000/500串行提交。关联组不能拆散，无法满足预算的单组会明确阻断，不静默跳过或只取前1000。跨批不保证整体原子性。

纯导入每批最多1000行；跨源DUP复合批还同时受100Review组、2000Fact、4000输出、另4000链接及50000同步标签影响的边界约束。所有批仍受2秒锁等待和2秒锁内计算/写入限制；计数未超上限不保证任何负载下都能完成。已知提交前WRITE_BUSY完整回滚本批，先前成功批保留；必须保存剩余选择重新核验、查看新完整计划并再次明确批准，不能重发旧金融POST。若需要缩小范围，由用户明确选择新的范围，不能靠提高写入截止或自动重试通过。

进程重启或缓存失效返回410，不证明原金融请求未提交。浏览器只保留原文件hash、来源行定位和选择意图，不保存可重放回执。重新上传相同原文件后，先核对未知批的当前持久状态及完整当前/历史输出，明确确认已核对，再重新读取剩余范围、保存选择、查看新计划并再次批准。已接受行、原Fact和原默认不重建；服务器不会重启后台导入或自动继续剩余批。

## 离线旧库副本迁移

只支持已核验旧14表。源库只读；更早结构、损坏引用、超覆盖或无法证明原默认被拒绝。以下不切换运行库。

1. 固定记录完整40位应用SHA，停止源库所有写入，保留原库/WAL。复制主DB文件不等于一致性备份。
2. 独立目录提供不存在的新目标文件，空间≥源库两倍+16MiB，默认预算120秒。
3. 根目录运行并替换三个占位值为明确路径/SHA。可加 `--expected-source-manifest <已记录fingerprint>` 核对源快照。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -X utf8 src/script/pirc35_migrate.py '<只读旧库路径>' '<不存在的新副本路径>' --candidate-sha '<完整40位提交SHA>'
```

4. 保存JSON：candidate_sha、schema_fingerprint、input/output_manifest、coverage_gaps、normalized_timestamps、initialized_refs。READY只证明当时副本，不代表缺口修复/已合并/授权切库。
5. 切换前保持停写，调用 `backend.service.schema_migration_service.verify_ready(candidate_path, report)` 同快照只读复核。新写/schema/manifest变化令旧READY失效，不按标题/最新ID补默认/账户/债。
6. 提交前失败原库不变；中断/未知提交目标保留不复用，重试用另一个新路径。提交后失败不能删除新写。真实切换后若已有新增写入，不可覆盖回旧库，须确认保全/恢复方案。

目前提供函数级READY复核，不提供自动切库、部署或恢复向导。原账单、来源全文和迁移报告仅在受控本机保存，不上传PR。安全运维日志不含账单/金额/密钥/提示词；受保护模型尝试审计是独立用途。

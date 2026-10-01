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

浏览器复验：`python -m pytest src/test/test_browser.py --run-browser -q`。九场景自建临时库/随机本机端口，无真实provider；Windows用Edge，Linux用Chromium，见 [测试入口](../test/README.md)。测试不等于独立审查或用户验收。

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

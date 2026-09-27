# PIRC-24 部署修复与真实闭环记录

2026-09-27，用户授权修复P1/P2并提交PR #9；遇合并冲突停止，不自动消解。

## 根因和修复

原容器已运行 f67ec93，但进程继承 `PAAM_AUTOTAG_REAL_ANALYSIS=0`；启动注册直接返回。规则启用和健康接口成功都无法开启扫描。此次保留默认安全开关，修正已获授权的本地部署配置为 REAL_ANALYSIS=1 / SYNTHETIC_ACCEPTANCE=0，并增加只读就绪检查与运维说明。

代码提交 `ae572011f613ee64477f4698bd4bfca61f94cddb`；镜像 `paam:pirc24-ready-ae57201`，新容器 `paam-pirc24-real-enabled-ae57201`，本机端口18775。应用后端/前端与f67ec93一致，新增部署检查、测试、文档。最后的报告与README提交仅补记录，不改变运行代码。

一致性备份 `/data/backup/pirc24-before-enable-20260927T102402Z.db` 留在原私有数据卷，未上传。沿用原数据和凭据卷，停止并保留旧容器用于回退。用户规则1/3继续启用，规则2继续停用，未重置游标或自动批准申请。

## 本轮实测

| 检查 | 结果 |
| --- | --- |
| 修复前只读就绪检查 | 非零：REAL_ANALYSIS_NOT_ENABLED、RULE_1_NOT_REGISTERED、RULE_3_NOT_REGISTERED |
| 新增就绪回归 | 6 passed |
| 候选实际镜像全量、断网临时库 | 551 passed / 0 failed / 1 Starlette弃用警告，152.90秒 |
| 修复后就绪 | ready=true，REAL_READY，Worker HEALTHY；tag-scan:1和tag-scan:3注册 |
| 自然CRON + 真实供应商 | 已配置SiliconFlow/Qwen3.5-4B；新增申请#3来自规则1，#4/#5来自规则3；均PENDING，未注入模型响应 |
| 真实建议审批 | 将最新库一致性复制到临时SQLite；通过正式v1 API批准新申请#3，标签生效，重复返回ALREADY_APPROVED；原库#3仍PENDING |
| 审批副本数据保护 | 金额/币种、Fact/Allocation不变，其他View/其他流水标签不变 |
| 原容器替换、随后重启验证 | REAL_READY和两扫描任务恢复；5条既有申请保留、游标不倒退、795条Ledger及金融字段指纹不变；模型Key仅检查存在性，仍true |

重启后规则游标快照为1→7、2→0、3→2。原金融字段SHA256为 `793b5ca2d3cb9080eb6687e4eeab3174739c82faaf78fae81216468cd06762c1`，替换前后及重启后相同。

## 实际限制

运行中也观察到 `REQUEST_TIMEOUT` 和预算内 `RETRY_DEFERRED`：规则1在重启前累计失败1项，未把失败隐藏或当作无建议。供应商超时仍可能发生；此次证明能真实产生申请并审批，不是全部795笔已处理或分类准确率达标。批量处理按原CRON与预算继续，安全诊断保留可追溯错误；未改变模型、超时或计费配置。

PR #9仍是交付入口。主库新申请保留待用户审核；审批验收只在临时副本执行，副本已由测试清理。完整功能的历史人工验收记录不因本次技术验证自动改为通过。

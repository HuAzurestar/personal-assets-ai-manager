# 自动标签部署与就绪验证

正式部署默认开启自动分析，无需额外设置环境变量。配置可用模型/凭据并启用规则后，会按 CRON 分析账目，可能向供应商发送经隐私处理的内容并产生费用；不会自动审批建议。健康接口成功仍不等于模型或规则已就绪。

如需明确关闭，在应用进程启动环境设置 `PAAM_AUTOTAG_REAL_ANALYSIS=0`。显式 `1` 仍可用于启用：

```text
PAAM_AUTOTAG_REAL_ANALYSIS=1
PAAM_AUTOTAG_SYNTHETIC_ACCEPTANCE=0
```

开关在模块加载时读取。Docker 已创建容器的环境不会因 `docker restart`、宿主终端 `export` 或 `docker exec -e` 改变。修改 Compose/service 的持久配置并重新创建应用容器；手动部署也须创建替代容器。不要同时运行两个写入同一 SQLite 的应用实例。

旧部署若显式保留 `PAAM_AUTOTAG_REAL_ANALYSIS=0`，升级后仍保持关闭；删除该覆盖或改为 `1` 并重建容器。`Dockerfile.pirc9` 是正式入口，遵循默认开启；`Dockerfile.pirc24-ui` / `serve_m2_ui.py` 是纯虚构演示入口，刻意关闭真实分析，不能作为正式部署入口。显式请求虚构验收时不会默认开启真实分析；两者显式同时启用仍拒绝启动。

## 切换步骤

新建正式 Docker 部署可直接使用以下命令，不需要附加自动分析开关（示例只用于新部署，不覆盖已有容器）：

```text
docker build -f src/report/Dockerfile.pirc9 -t paam:production .
docker run -d --name paam-production --restart unless-stopped -p 127.0.0.1:8765:8765 --mount type=volume,source=paam-production-data,target=/data paam:production
```

仍须配置可用的凭据存储、保存模型密钥并启用规则；默认开启调度不会绕过这些条件，也不会自动添加模型或规则。

1. 核对镜像 revision、原容器入口/命令、端口、数据库卷和凭据卷；用 SQLite backup API 创建一致性备份，限制备份权限。不要打印或复制 API Key 到配置文件。
2. 保留用户已选规则、CRON、游标、申请和人工标签。创建使用原镜像/卷/凭据服务的新容器，只修改上述两个开关；保留其他环境与部署参数。
3. 停止旧容器后启动新容器。失败时先停新容器，再启动保留的旧容器；无需恢复旧数据库覆盖新数据。不要删除原卷。
4. 运行下面的只读检查。它在开关关闭、Worker 不健康、无启用规则、规则未注册或被阻塞时返回非零；不会触发模型或修改数据。

```powershell
python src/script/verify_auto_tag_runtime.py --base-url http://127.0.0.1:18775
```

5. 等待自然 CRON，观察规则游标、累计统计、安全诊断和新待审申请。`ready=true` 只证明调度已就绪，不证明供应商请求或分类正确。超时可能让供应商已执行但本地未获结果；以诊断、申请和游标共同核对，不将超时当作“无建议”。
6. 真实模型产生的申请默认保持待审。审批验收可在独立数据库副本执行，核对标签生效、其他 View 不变、金额与 Allocation 不变。未经用户选择不要审批正式库申请。

重启后再次执行步骤 4/5，确认设置、申请和游标恢复。新导入自动进入后续 CRON；无需重导旧账单或重置游标。

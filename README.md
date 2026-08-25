# personal-assets-ai-manager（个人资产管理基线）

本地优先的个人账单与资产管理程序。它提供一个可直接运行的 FastAPI 服务和 Web 面板，适合作为支付宝/微信账单导入、资产快照、LLM 智能打标的演进起点。

## 已包含

- Web 面板：总资产、收入/支出汇总，新增账单、资产快照和账单列表。
- REST API：`/api/health`、`/api/dashboard`、`/api/bills`、`/api/assets`、`/api/tag`，以及 FastAPI 自动文档 `/docs`。
- 本地 SQLite：默认存储在 `data/personal-assets-ai-manager.db`；备份此文件即可备份数据。
- LLM 边界：默认规则模拟器可离线运行；设置 `PAAM_LLM_PROVIDER=openai_compatible`、`PAAM_LLM_BASE_URL`、`PAAM_LLM_MODEL`、`PAAM_LLM_API_KEY` 后，可调用任意 OpenAI-compatible `/chat/completions` 接口并要求 JSON 输出。调用失败时自动回退本地规则。

## 当前初始化内容

```
app/                  # FastAPI 服务、SQLite 模型、LLM 分类器和 Web 静态资源
app/static/           # Web UI 与 personal-assets-ai-manager 图标
app/templates/        # 服务端 Web 页面
tests/                # API 冒烟测试
scripts/build.py      # PyInstaller Windows 目录式构建
.github/workflows/    # 测试和标签触发的 Windows 构建
```

基线已实现账单与资产快照的新增/查询、聚合面板、分类 API、SQLite 持久化和 OpenAI-compatible 适配边界；支付宝/微信账单导入、可编辑 LLM 配置页和自启动安装脚本留作下一阶段。
- 应用名称与图标：仓库、构建产物及服务标识为 `personal-assets-ai-manager`；界面展示名为 Personal Assets AI Manager；Web 使用 `app/static/personal-assets-ai-manager.svg`，Windows 打包脚本可在补充 `.ico` 后嵌入安装包图标。

## 本地运行

需要 Python 3.10 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

打开 `http://127.0.0.1:8765` 使用 Web 面板，或打开 `http://127.0.0.1:8765/docs` 调用 API。服务默认绑定 `0.0.0.0:8765`，可供局域网设备访问；个人使用时建议仅在可信网络开放端口。

## 验证

```powershell
pytest -q
```

## 编译和升级

```powershell
pip install pyinstaller
python scripts/build.py
```

打包结果位于 `dist/personal-assets-ai-manager/`。目录式产物便于保留 `data/personal-assets-ai-manager.db`、替换程序文件和回滚。GitHub Actions 在 `main` 的提交上执行测试，推送 `v*` 标签时生成 Windows 构建产物。

## 下一阶段建议

1. 增加支付宝/微信 CSV、Excel 导入器及去重规则。
2. 实现配置界面和真实 OpenAI-compatible LLM 适配器，输出结构化分类/标签。
3. 确认名称、Windows `.ico`、发布平台和对账单数据调用云端 LLM 的隐私策略后，发布首个 GitHub Release。

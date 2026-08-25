# AssetMind（个人资产管理基线）

本地优先的个人账单与资产管理程序。它提供一个可直接运行的 FastAPI 服务和 Web 面板，适合作为支付宝/微信账单导入、资产快照、LLM 智能打标的演进起点。

## 已包含

- Web 面板：总资产、收入/支出汇总，新增账单、资产快照和账单列表。
- REST API：`/api/health`、`/api/dashboard`、`/api/bills`、`/api/assets`、`/api/tag`，以及 FastAPI 自动文档 `/docs`。
- 本地 SQLite：默认存储在 `data/assetmind.db`；备份此文件即可备份数据。
- LLM 边界：默认规则模拟器可离线运行；设置 `ASSETMIND_LLM_PROVIDER=openai_compatible`、`ASSETMIND_LLM_BASE_URL`、`ASSETMIND_LLM_MODEL`、`ASSETMIND_LLM_API_KEY` 后，可调用任意 OpenAI-compatible `/chat/completions` 接口并要求 JSON 输出。调用失败时自动回退本地规则。
- 应用名称与图标：使用 `AssetMind` 名称，Web 使用 `app/static/icon.svg`，Windows 打包脚本可在补充 `.ico` 后嵌入安装包图标。

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

打包结果位于 `dist/AssetMind/`。目录式产物便于保留 `data/assetmind.db`、替换程序文件和回滚。GitHub Actions 在 `main` 的提交上执行测试，推送 `v*` 标签时生成 Windows 构建产物。

## 下一阶段建议

1. 增加支付宝/微信 CSV、Excel 导入器及去重规则。
2. 实现配置界面和真实 OpenAI-compatible LLM 适配器，输出结构化分类/标签。
3. 确认名称、Windows `.ico`、发布平台和对账单数据调用云端 LLM 的隐私策略后，发布首个 GitHub Release。

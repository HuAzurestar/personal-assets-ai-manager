# personal-assets-ai-manager（个人账本与资产管家）

### 多格式账单导入

支付宝、微信各自拥有独立的“预览导入”入口，支持 `CSV`、`XLS`、`XLSX`，以及仅包含一个上述账单文件的密码 ZIP。选择多个文件或一个文件夹后，服务端按对应平台的固定模板自动识别交易时间、交易方、金额、备注、收支和流水号，再逐文件预览并确认导入；不需要手工字段映射。

- 密码仅用于当前请求的内存解包；不会写入 SQLite、导入原始字段、日志或仓库。解析过程不创建临时解压文件。
- 每次导入保存批次标识、来源类型、显示文件名、格式、可选 ZIP 内文件名和 SHA-256 文件指纹，不保存原始文件二进制；同来源、同指纹的文件会被拒绝为重复导入。
- 上传大小、压缩包条目数量、解压条目大小和可解析行数均有限制；不支持的格式、压缩包路径穿越、错误/缺失密码会在预览或导入前返回明确错误。

API 对应单文件预览 `POST /api/imports/{source_type}/preview`、单文件确认 `POST /api/imports/{source_type}`，以及批量预览/确认 `POST /api/imports/{source_type}/batch/preview`、`POST /api/imports/{source_type}/batch`。批量接口接收文件名与 Base64 内容数组；密码仅可选地放在 `X-Import-Password` 请求头。完整结构见 `/docs`。

## 当前 MVP（以本节为准）

这是一个离线优先的个人账单事实源。技术标识保持 `personal-assets-ai-manager`，界面中文展示名为“个人账本与资产管家”。

- 已启用独立的支付宝、微信 CSV 导入入口；工行、农行等银行会以独立适配器按钮逐步加入。
- 每个导入批次保存来源、文件名和行数；每笔流水保留来源引用和原始字段快照。
- 流水可携带多个标签。规则建议、LLM 建议、人工确认和人工授权自动策略均写入审计历史；策略按数值置信度决定当前展示，低等级历史不会丢失。
- 同额同交易方的近似重复、同额反向流水的资产转移均只生成候选，必须由人工确认或忽略。MVP 不做余额校准，也不包含账单计划。

### 最短导入与验收

启动 `python run.py` 后打开 `http://127.0.0.1:8765`，选择支付宝或微信账单的 CSV、XLS、XLSX 或密码 ZIP（也可多选或选文件夹）；点击对应“预览账单”后检查逐文件结果，再直接确认导入。导入结果会显示流水数量和待复核候选；在流水表可执行“规则 / LLM / 人工”打标，在候选表确认或忽略。

对应 API：`POST /api/imports/alipay/preview`、`POST /api/imports/wechat/preview`、`POST /api/imports/{source_type}`、`POST /api/bills/{id}/tags`、`GET /api/candidates`、`POST /api/candidates/{id}`，完整交互文档在 `/docs`。导入接口请求体为账单原始字节，可选查询参数 `filename`。

> 后文的 Windows 安装和构建说明仍适用；其中旧的资产快照和“尚未支持真实导入”描述已被本节取代。

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
- 应用名称与图标：仓库、构建产物及服务标识为 `personal-assets-ai-manager`；界面展示名为 Personal Assets AI Manager；Web 使用已确认的 Gemini SVG 图标（`app/static/personal-assets-ai-manager.svg`）。PyInstaller 通过收集整个 `app/static/` 目录将其纳入构建；Windows 打包可在后续补充同款 `.ico` 后嵌入可执行文件图标。

## 本地运行

需要 Python 3.10 或更高版本：

Windows（包括 Anaconda/Conda 环境）建议直接运行一键安装脚本。它始终使用项目虚拟环境的解释器，并以 `--isolated` 忽略会强制 `--user` 的本机 pip 配置：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
.\.venv\Scripts\python.exe run.py
```

若 PowerShell 限制脚本执行，可仅对当前命令使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

### 更新已有克隆（Windows PowerShell）

若之前克隆时还没有 `scripts/setup.ps1`，先在仓库根目录拉取 `main`，再确认脚本存在并执行。注意虚拟环境解释器的正确相对路径是 `.\.venv\Scripts\python.exe`：

```powershell
git pull --ff-only origin main
Get-Item .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

然后在第一个 PowerShell 窗口启动服务：

```powershell
.\.venv\Scripts\python.exe run.py
```

保持该窗口运行，并在第二个 PowerShell 窗口验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

也可手动安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

打开 `http://127.0.0.1:8765` 使用 Web 面板，或打开 `http://127.0.0.1:8765/docs` 调用 API。服务默认绑定 `0.0.0.0:8765`，可供局域网设备访问；个人使用时建议仅在可信网络开放端口。

### Windows / Conda pip 故障排查

若出现 `Can not perform a '--user' install`，先检查配置来源：

```powershell
python -m pip config list -v
```

若输出中存在 `install.user='true'` 或 `global.user='true'`，可按需取消相应配置（不存在的键会提示错误，可忽略）：

```powershell
python -m pip config unset install.user
python -m pip config unset global.user
```

无需修改配置也可使用上述 `--isolated` 命令或 `scripts/setup.ps1`。在 Conda 中即使提示符同时显示 `(base)` 与 `(.venv)`，仍以 `.\.venv\Scripts\python.exe` 为准；若希望先建立独立 Conda 环境，可执行 `conda create -n paam python=3.10`、`conda activate paam` 后再运行安装脚本。

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

## 首发说明（v0.1.0 基线）

首发版本交付可运行的本地账单与资产管理底座：SQLite 数据持久化、Web 管理面板、REST API、离线规则打标与可配置 OpenAI-compatible LLM 调用边界。它不包含真实账单导入、自启动安装器或生产级安全策略；这些能力将在后续迭代补齐。待确认首发行为后，推送 `v0.1.0` 标签即可触发 Windows 构建产物。

## 下一阶段建议

1. 增加支付宝/微信 CSV、Excel 导入器及去重规则。
2. 实现配置界面和真实 OpenAI-compatible LLM 适配器，输出结构化分类/标签。
3. 确认名称、Windows `.ico`、发布平台和对账单数据调用云端 LLM 的隐私策略后，发布首个 GitHub Release。

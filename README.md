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
- 流水可携带多个标签。规则建议、LLM 建议、人工确认和授权自动均写入审计历史；策略按数值置信度决定当前展示，低等级历史不会丢失。
- 同额同交易方的近似重复、且具有两个不同账户证据的同额反向流水只生成候选，必须由人工处理；候选置信度不等于事实。MVP 不做余额校准，也不包含账单计划。

### 五页工作台

Web 面板有五个一级入口：**汇总**、**导入与数据**、**标签管理**、**重复与转移候选**、**数据库观察**。桌面端左侧栏可收缩并记住偏好；移动端改为顶部紧凑导航。候选复核和数据库观察都使用独立分页页。

- 汇总页展示收入、支出、净额、已导入流水数和按日趋势，全部直接从未被排除的流水计算。
- 数据页包含支付宝/微信预览导入、服务端分页列表和重复/转移复核。分页响应固定以 `occurred_at` 或 `amount` 加流水 ID 排序，显示总数、当前范围和上一页/下一页。
- 数据接口 `GET /api/transactions` 支持 `page`、`page_size`（最大 100）、`sort_by`、`sort_order`、日期、绝对金额、来源、收支/转移、搜索和按视图标签筛选。跨视图标签按 AND 组合；同一视图传入两个标签会返回 400。
- 标签管理页维护“标签视图”。同视图的显式标签原子替换，跨视图可组合；每个视图自动拥有受保护的“未分类”，没有赋值的流水即是该视图的未分类。

### 当前标签状态（JSON）

当前标签写模型是 `bills.tag_state_json`：一个受长度限制（最多 2048 字节）的 JSON 对象，键和值均为不可变的系统名，例如 `{"category":"food","scenario":"daily"}`。名称仅供 UI 展示；数据页的“选择标签”弹窗按名称选择，不要求或显示数字 ID。一个键只能保存一个值，因此同一视图天然互斥；多个键可同时存在，因而跨视图可组合。

- `PUT /api/transactions/{id}/tag-state` 接收 `{"tag_state": {"视图系统名": "标签系统名"}}`；未知、归档、非系统名字符或超长 JSON 均返回 `422`。`PUT /api/transactions/bulk-tag-state` 可对最多 100 条选中流水应用同一状态。
- `GET /api/transactions?tag=category:food` 使用系统名筛选；同一视图两个不同标签仍返回 `400`。消费类别和使用场景分别有 SQLite JSON 表达式分页索引，排序继续以时间/金额和流水 ID 稳定执行。
- 升级时，旧 `tags`、`bill_tags`、`bill_view_tags` 和旧 `bills.tags` 都只保留为兼容证据，不删除也不再写入；所有旧流水的当前 JSON 状态统一迁移为各有效视图的 `unclassified`，迁移事件写入 `tag_audits`。定义表与审计历史保留。

### 最短导入与验收

启动 `python run.py` 后打开 `http://127.0.0.1:8765`，选择支付宝或微信账单的 CSV、XLS、XLSX 或密码 ZIP（也可多选或选文件夹）；点击对应“预览账单”后检查逐文件结果，再直接确认导入。导入结果会显示流水数量和待复核候选；在流水表可执行“规则 0.45 / LLM 建议 0.70 / 人工确认 0.95 / 授权自动 1.00”，在候选表确认或忽略。

对应 API：`POST /api/imports/alipay/preview`、`POST /api/imports/wechat/preview`、`POST /api/imports/{source_type}`、`POST /api/bills/{id}/tags`、`GET /api/candidates`、`POST /api/candidates/{id}`，完整交互文档在 `/docs`。导入接口请求体为账单原始字节，可选查询参数 `filename`。

> 后文的 Windows 安装和构建说明仍适用；其中旧的资产快照和“尚未支持真实导入”描述已被本节取代。

## 打标策略：当前实际行为

默认配置 `PAAM_LLM_PROVIDER=mock`，所以服务默认离线：新建和导入流水一律使用内置关键词规则，审计记录的 `provider` 为 `local-rules`。只有明确设置 `PAAM_LLM_PROVIDER=openai_compatible` 且提供 Base URL、模型和 API Key 后，LLM 路径才会向兼容的 `/chat/completions` 端点发送交易方和备注；调用失败或未完成配置会改用本地规则，并把实际来源记录为 `fallback-rules` 或 `mock-rules`。

| 工作台按钮 / API `strategy` | 输入 | 结果与审计来源 | 默认置信度 |
| --- | --- | --- | --- |
| 规则 0.45 / `local_rules` | 交易方、备注；也可通过 API 提供分类和标签 | 强制本地关键词规则，绝不调用 LLM；`provider=local-rules` | 0.45 |
| LLM 建议 0.70 / `llm_suggestion` | 交易方、备注；也可通过 API 覆盖建议值 | 已配置时调用兼容 LLM；否则或失败时离线回退，`provider` 记录真实来源 | 0.70 |
| 人工确认 0.95 / `manual` | 工作台弹窗输入分类和至少一个标签 | 直接采用用户输入，不调用 LLM；`provider=manual` | 0.95 |
| 授权自动 1.00 / `authorised_auto` | 点击后再次确认本次自动打标 | 执行一次自动分类并记录真实来源；它是单次用户授权，不是后台定时或长期授权规则 | 1.00 |

`confidence` 是 0 到 1 的可选 API 字段；不给值时使用上表默认值。数值高于当前未失效记录时，新的分类和标签成为流水当前展示值；较低值仅留下 `superseded=true` 的审计历史。数值相等时，较新的记录成为当前值。每次动作都会新增 `tag_audits`，历史不覆盖、不删除。

## SQLite 数据关系与来源保留

```
asset_snapshots（账户名称/类型/余额快照；当前没有独立 Account 主表）

import_batches 1 ── * import_artifacts（文件名、格式、ZIP 条目、SHA-256）
       │
       └── * ledger_origins * ── 1 bills ── * tag_audits（策略、置信度、来源、失效标记）
                                      │
                                      ├── * bill_tags * ── 1 tags（当前标签关系）
                                      └── * review_candidates（本流水与 related_bill 两次关联：重复/转移候选）
```

`bills` 是账单事实源，保存规范化后的时间、交易方、备注、金额及当前分类/标签显示值；`ledger_origins` 保存来源类型、流水号、导入批次和解析出的原始字段 JSON。导入原始文件二进制不入库，`import_artifacts` 只保存文件引用元数据和 SHA-256 指纹。密码不持久化。`tags`/`bill_tags` 保存当前标签实体关系，而每次打标时的分类和标签快照留在 `tag_audits` 中，便于追溯。

当前扩展新数据源需要新增 provider 模板、在 `_validate_source_type` 中加入来源、并增加对应 UI 入口；模板只要映射交易时间、交易方、金额、备注、收支和流水号，导入批次、流水、标签和审计表不需要变化。

## 重复与资产转移候选：当前实际行为

候选表展示两笔原始流水的账户/支付渠道、收支方向、时间、金额、交易方、来源类型、来源批次和流水号，以及生成依据。支付宝和微信模板会在导出中存在支付/收款渠道时保存为交易账户；未提供账户时会明确显示“未提供账户”。因此新建资产转移候选的前提是：金额相等、方向相反、时间相差不超过 5 分钟，且两笔记录有两个不同的账户。仅有普通商户名称和相反金额不会再产生新的转移候选。

- **确认转移组**：只适用于具备两个不同账户证据的转移候选。两笔 `bills` 原始记录都会保留，写入同一个 `transfer_group_id`，并标记为不计入收入/支出聚合；账户、方向、金额仍保留，供回看资产流向。
- **解决重复**：必须选择“保留流水 A”或“保留流水 B”。被保留流水继续计入聚合；另一笔不删除，而是保留 `duplicate_of_id` 并标记为不计入聚合。处理结果会明确记录保留的流水 ID。
- **忽略 / 稍后处理**：不删除或修改任一流水，也不改变聚合；分别记录为 `ignored` 或 `deferred`，不会继续计入待处理数量。候选建议的百分比只是匹配强度，始终由人工判断。

旧版已经确认但没有账户证据或重复保留选择的候选，会迁移为明确的旧版结果说明，而不会伪造新的判断依据。

### 候选复核操作与汇总影响

候选独立页的服务端分页中，**每一行**都有“详情”及与其状态匹配的操作；详情弹窗展示两笔流水的完整字段、流水号、原始字段、来源类型/引用、导入批次、账户与方向证据、匹配依据、当前汇总影响和处理审计。旧版 `legacy_duplicate_needs_review` 也会作为可操作的重复候选呈现，可选择保留 A 或 B。

- **重复**：选择保留 A 或 B。另一笔仍保留为原始流水，但标记为重复排除，不参与收入、支出、净额和趋势；撤销会还原两笔流水与候选状态。
- **个人账户间转移**：仅在两笔具有不同账户证据时可确认。两笔建立转移组、继续追踪资产流向，并从收入、支出、净额和趋势排除；候选之外的手续费等真实成本不会被该操作移除。
- **他人资产转移/代收代付**：由人工确认，两笔原始流水和标签照常保留，标记为不追踪收支并从上述汇总排除；可随时撤销。
- **缺少账户证据的同额反向记录**：会显示“仅供人工核验”的低置信度候选，绝不自动认定为个人转移；可人工确认他人资产转移、忽略或稍后处理。

API 还提供 `GET /api/candidates/{id}/detail`、`POST /api/candidates/{id}`、`POST /api/candidates/batch` 和 `POST /api/candidates/{id}/undo`。批量操作支持忽略、稍后和同类转移确认；重复保留 A/B 始终要求逐条选择，避免误排除流水。

相同交易方、金额且相邻时间不超过五分钟的多笔流水会合并为**一个稳定的重复候选组**，不会按两两组合生成多条可互相处理的候选。列表与详情会显示组内 A/B/C… 所有成员；保留某一成员只排除同组其他成员。详情里的“关闭”只关闭面板，原始字段默认展开，且可直接完成与列表一致的保留、拒绝建议、稍后、人工核验或回退操作。相同处理请求是幂等的：不会重复排除或重复扣减汇总。

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

基线已实现账单与资产快照的新增/查询、聚合面板、真实的支付宝/微信多格式导入、分类 API、SQLite 持久化和 OpenAI-compatible 适配边界；LLM 配置页和自启动安装脚本留作后续迭代。
- 应用名称与图标：仓库、构建产物及服务标识为 `personal-assets-ai-manager`；界面展示名为“个人账本与资产管家”；Web 使用已确认的 Gemini SVG 图标（`app/static/personal-assets-ai-manager.svg`）。PyInstaller 通过收集整个 `app/static/` 目录将其纳入构建；Windows 打包可在后续补充同款 `.ico` 后嵌入可执行文件图标。

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

### 开发更新并直接运行（Windows PowerShell）

日常开发更新不需要编译。它会在发现本地未提交改动时停止；仅允许 `git fetch origin main` 后的快进同步，使用项目 `.venv` 安装/更新依赖、运行测试，随后直接执行 `run.py`。它不会安装 PyInstaller、不会构建发布产物，也不会删除、重置或覆盖用户文件：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update-and-run-dev.ps1
```

只想完成安全同步、依赖更新和测试而不启动服务：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update-and-run-dev.ps1 -NoStart
```

若测试需要暂时跳过，可额外传入 `-SkipTests`；修复后应重新运行不带该参数的命令。若提示本地改动或非快进关系，脚本会在更新前停止，按下文的失败恢复步骤处理即可。

### 发布构建：安全升级、构建、测试并运行（Windows PowerShell）

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update-and-run.ps1
```

脚本先检查工作区是否有未提交改动；有改动就停止，不会覆盖用户文件。随后只执行 `git fetch origin main` 和 `git merge --ff-only origin/main`，不是快进关系也会停止；更新成功后使用项目虚拟环境安装依赖、安装 PyInstaller、构建、运行测试，最后前台启动服务。只想验证升级链而不启动服务可使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update-and-run.ps1 -NoStart
```

失败恢复：若提示本地改动，先用 `git status` 查看并自行提交、暂存或备份；若提示不是快进关系，先比较 `git log --oneline HEAD..origin/main` 与 `git log --oneline origin/main..HEAD`，手工处理分支后再运行。脚本从不执行 `git reset`、强制检出或删除用户文件；依赖/构建失败时也不会回退或覆盖数据库，修正网络、Python 或依赖问题后重试即可。

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

`v0.1.0` 当前暂停，等待本轮导入、打标与数据模型重新验收；在确认前不得创建或推送该标签。现有基线包含 SQLite 持久化、Web 管理面板、REST API、离线规则打标、可配置 OpenAI-compatible LLM 边界，以及支付宝/微信多格式导入；自启动安装器和生产级安全策略尚未实现。

## 本轮候选复核与导航更新（验收基线）

工作台现在有五个一级入口：汇总、数据、标签管理、重复与转移候选、数据库观察。候选页使用服务端分页（`GET /api/candidates/page`，最大 100 条/页），显示两笔流水的账户、方向、时间、金额、交易方、来源和批次，以及匹配依据、当前状态和汇总影响。

- 重复候选必须明确选择保留 A 或 B；未保留的原始流水只标记为重复并排除聚合，不删除。
- 转移确认要求两个不同账户的同额反向证据；确认后写入同一转移组，保留两笔流水和资产流向，但从收入/支出汇总排除。
- 忽略和稍后处理不改变流水或汇总。候选页支持对选中的待处理项批量忽略、批量稍后和批量确认转移；重复保留 A/B 保持逐条明确选择。
- 每次处理均在 `candidate_action_logs` 中保存处理前状态和时间。`POST /api/candidates/{id}/undo` 会恢复候选及两笔流水的聚合、重复和转移字段，并把该审计记录标为已撤销；原始流水不删除。

左侧导航由汉堡按钮切换展开/收缩状态：收缩时保留窄图标栏，所有入口仍提供 `title` 和 `aria-label`；品牌文字截断而不溢出。小于 700px 时导航切换为紧凑顶部栏。静态 UI 测试覆盖四个入口、可访问名称、收缩选择器和移动断点。

`v0.1.0` 仍暂停，须在本轮候选验收后另行确认，不能创建或推送该标签。

## 无色导航符号与可访问性

工作台五个入口使用统一的无色符号：汇总 `[ ≡ ]`（ASCII 回退 `[SUM]`）、导入与数据 `[ ↓ ]`（`[IMP]`）、标签管理 `[ ¤ ]`（`[TAG]`）、重复与转移候选 `[ ! ]`（`[ERR]`）、数据库观察 `[ DB ]`（`[DB]`）。符号只作装饰，按钮本身始终提供中文 `aria-label` 和 `title`；收缩侧栏和移动端复用同一导航配置，因此不会出现 Emoji 或不同名称。ASCII 回退标识保存在页面的 `data-ascii-fallback` 属性中，供不支持该 Unicode 字符的宿主或后续壳层读取。

## SQLite 数据库观察（只读诊断）

工作台新增第五个入口“数据库观察”（`[ DB ]`）。它只面向本机维护：列出应用 SQLite 表、字段类型、主键和索引，并以稳定主键升序、服务端分页查看表行；页面显示总数、当前范围、页码和上一页/下一页。

- API：`GET /api/database/tables` 与 `GET /api/database/tables/{table_name}?page=1&page_size=25`。
- 安全边界：只允许应用定义的表白名单，表名不会被拼接成用户 SQL；分页由 SQLAlchemy 参数绑定，最大 100 行/页，单元格输出最多 600 字符。没有执行 SQL、写入、删除或修改的入口。
- 数据敏感性：账单原始字段、来源引用和审计记录可能包含个人信息。仅在受信任的本机服务上使用该页；观察器不记录访问内容、不写入数据库，也不会新建数据副本。超长值只在页面输出中截断，原库数据不被改变。

可用 `pytest -q` 验证空表、未知表、非法分页、稳定排序、字段/索引元数据和多表分页；该测试也回归账单、候选、标签和汇总 API。

## 下一阶段建议

1. 实现 LLM 配置界面与更完整的授权策略管理。
2. 增加工行、农行等独立 provider 模板和入口。
3. 重新验收后再决定首个 GitHub Release。

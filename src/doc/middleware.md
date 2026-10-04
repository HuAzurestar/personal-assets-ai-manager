# PAAM 中间件与 AI 任务接入

本版在同一应用进程和 SQLite 内建立 `backend.middleware`。应用启动时创建
`app.state.middleware`，包含共享 `ai` 执行器和 `platform` 配置、凭据、调度入口。
自动标签是首个接入任务；新的业务可以注册自己的任务，并复用调用和审计链。

## 能力边界

| 层 | 负责 | 入口 |
| --- | --- | --- |
| 业务 | 构造安全输入、任务语义校验、业务结果与检查点事务 | `TaskDefinition` 与业务 Service |
| AI | 任务注册、Prompt 解析、模型配置与凭据读取、供应商调用、调用记录与用量 | `middleware.ai.execute()` |
| 平台 | 已有设置事务、系统凭据库、单一后台调度器 | `middleware.platform` |

中间件内保留 Service → Mapper → Entity 的层次。HTTP Router 仍在扁平
`backend/router` 目录中，新的管理 API 属于既有 System 模块。
`composition.py` 负责应用接线，只有它知道自动标签的发布失效和虚构预览适配器。
通用运行时、供应商传输、调用审计均不依赖标签、规则或 Ledger ID。

`llm_adapter.py` 和 `ConfiguredLlmAnalyzer` 保留为兼容入口，实际执行已转交
中间件。原有模型、凭据、规则、扫描和调度 API 路径继续可用。
设置 Router 已使用平台配置工厂；后台任务复用原来的 `core/job_scheduler.py`。

## 业务调用

```python
from backend.middleware import ExecutionContext

result = await middleware.ai.execute(
    "auto_tag.classify",
    input=protected_input,
    model_id=model_id,
    context=ExecutionContext(run_id=job_context.run_id, attempt=1),
)
```

`input` 必须符合任务注册的 Pydantic 输入类型。自动标签要求经过
`LlmPrivacyService` 清洗并签封的 `ProtectedLlmAnalysisInput`；普通 DTO 或
自行构造的伪装输入会被拒绝。其他任务应在自己的安全输入适配器中实现适当披露。
业务 Service 在调用结束后用自己的事务提交结果，不能在写事务中等待模型。

每次 execute 最多发送一次供应商请求，不自动修复输出、切换模型或增加重试。
自动标签的三次重试、30 秒启动新工作的软预算、分页及活动状态检查，仍由
已有扫描协调器负责。这样暂停和规则版本检查继续限制每一次新调用。

取消等待不能强制终止已进入同步 SDK 的网络请求；该请求仍受 timeout 限制，
可能产生 token 与审计记录。扫描器会拒绝已取消或过期的业务结果。
调用记录的 `SUCCEEDED` 仅表示模型响应已通过任务校验，不表示业务已提交。

## 新任务的最小接入

定义输入/输出 Pydantic 模型、默认 Prompt、输入到消息的适配函数，以及
严格解析和语义校验函数，然后注册一个 `TaskDefinition`：

```python
from backend.middleware import PreparedTask, PromptVersion, TaskDefinition

TASK = TaskDefinition(
    key="summary.monthly",
    version=1,
    title="月度摘要",
    input_type=SafeMonthlyInput,
    output_type=MonthlyResult,
    default_prompt=PromptVersion(
        key="summary.monthly", version=1, instruction="根据已披露的统计生成摘要。",
    ),
    prepare=prepare_monthly_messages,
    parse=parse_and_validate_monthly_result,
)
```

`prepare(input, prompt, response_mode)` 返回 `PreparedTask(messages, response_format)`；
`parse(content, input)` 返回注册的输出模型，并实施相应的 JSON 与语义校验。
可选 `result_status(result)` 返回大写机器码，默认 `SUCCEEDED`。

把任务加入 `composition.py` 的默认注册集合。新增任务如果需要定时运行，
由业务 Service 通过 `middleware.platform.scheduler.register_cron()` 或
`register_interval()` 注册回调；不能新建调度器。注册 key 使用稳定 namespace。
新 namespace 的诊断字段仍须遵守共享调度诊断白名单。

业务无需读取供应商密钥、导入 LiteLLM、记录 Prompt 审计或提取用量。
任务注册拒绝同名覆盖；任务合同或校验语义改变时应推进任务 version。
新任务的固定预览样例和发布失效策略在 composition 中接入，避免通用运行时读取业务表。

## Prompt 生命周期

“设置 → AI 管理”（`#settings/ai`）提供任务、Prompt、调用与用量展示。
模型连接、金额披露及自动分析开关链接到既有 Automation 页面。

内置自动标签指引位于 `asset/ai_prompt/auto_tag.classify.v1.json`，固定隐私与
输出模板位于 `auto_tag.contract.v1.json`。模板只做一次命名变量替换；插入的
用户文本不会再次作为模板执行。规则指引、流水和候选仍以 user JSON 数据发送。

可编辑的任务指引以 `ai_prompt` 不可变内容版本保存：

1. 创建新版本，状态为 `DRAFT`；不覆盖旧版本的内容。
2. 使用固定虚构样例预览实际 system/user 消息，既不读取真实流水也不调用模型。
3. 发布时验证目标版本的 `updated_time` 和当前生产版本号。
4. 原生产版本改为 `RETIRED`，选中版本改为 `PRODUCTION`；历史版本也可恢复。

每个 prompt_key 最多一条生产记录，由部分唯一索引保证。启动只补齐缺失的
默认版本，不覆盖已发布内容；代码直调尚未种子初始化的库时使用注册的默认版本。
固定隐私与结构合同不能从编辑框删除，输出仍须经过原来的理由和候选校验。

自动标签发布会在同一个 `BEGIN IMMEDIATE` 事务中推进全部相关规则的
rule_revision/scan_epoch、重置游标并取消待审查旧建议。正在执行的旧扫描
使用旧 token，之后提交会返回 `RULE_TOKEN_CHANGED`。已经人工确认的标签不受影响。
版本计数耗尽、写冲突或陈旧生产版本会使整个发布回滚。

## 管理 API

前缀为 `/paam/system/v1/ai`，成功响应遵守 `status/message/body` 协议。

| 方法 | 相对路径 | 用途 |
| --- | --- | --- |
| GET | `/task/list` | 注册任务及当前生产 Prompt 版本 |
| GET | `/prompt/list` | Prompt 版本，按 ID 倒序 |
| POST | `/prompt` | 创建不可变草稿 |
| POST | `/prompt/{id}/preview` | 固定虚构样例预览 |
| POST | `/prompt/{id}/publish` | 发布或恢复版本 |
| GET | `/invocation/list` | 调用元数据，按 ID 倒序 |
| GET | `/usage` | 全部中间件调用的累计用量 |

三个 list 支持 `page_index/page_size`，每页最多 100；当前不支持 query/filter/sorter，
传入非空表达式会明确返回 422。预览只接受既有四个 sample 和披露参数，
不接受 Ledger ID、任意账单文本或 API Key。管理页操作不会直接运行 AI 任务。

## 用量与审计

调用前独立提交 `ai_invocation=STARTED`，写失败即停止供应商请求。返回后更新为
`SUCCEEDED/REJECTED/ERROR`，保存耗时、版本、run_id、attempt、用量与安全错误码。
校验失败的响应也保存供应商报告的 token。模型配置或凭据尚未通过本地校验时
没有发起请求，因此不会生成 Invocation。

input/output/total/cached token 使用 `-1` 表示 UNKNOWN。缺失 total 且 input/output
均已报告时可相加得到 total。费用只采集 SDK 返回的 response_cost，使用 USD
十进制文本；没有估算时存空串，不猜价格或伪造为零。汇总同时显示用量覆盖次数
和费用覆盖次数，金额在 Service 中使用 Decimal 求和。

请求快照只包含 messages 与 response_format。凭据、代理、鉴权头和异常原文
不进入记录。模型正文在本地最多保存 256 KiB 并标记截断；普通 HTTP 列表只读取
元数据，不加载正文。自动标签仍写原有 `llm_prompt_audit`，保留原来的业务审计合同。
进程中断后未完成的调用可能保留 STARTED，应视为未确定结果。

本版没有自动保留期、费用预算、RPM/TPM 门禁、独立 ProviderConnection/ModelProfile、
质量 Eval、Langfuse 或模型 fallback。虚构预览用于核查披露和渲染，不是分类质量评测。
这些扩展应接在中间件合同之后，不能让业务重新直接依赖供应商 SDK。

## 验证

`test_ai_middleware.py` 覆盖不依赖标签 ID 的第二个任务、调用前审计、请求期间
释放写锁、拒绝输出的用量、错误脱敏、发布冲突和原子失效，以及管理 API。
`verify_ai_ui.py` 使用专用临时库和虚构凭据检查草稿、预览、发布、恢复、分页与移动端。
默认测试不连接真实模型。

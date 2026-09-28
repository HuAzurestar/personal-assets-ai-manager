import { esc } from "../util/core.js";

export function helpTip(label, copy) {
  return `<span class="automation-tip"><button type="button" class="automation-tip-trigger" aria-label="${esc(label)}" aria-description="${esc(copy)}">?</button><span class="automation-tip-content" role="tooltip">${esc(copy)}</span></span>`;
}

const resultCopy = {
  APPROVED: "已通过并应用标签", REJECTED: "已拒绝，未改变标签",
  ALREADY_APPROVED: "此前已通过，本次不重复累计", ALREADY_REJECTED: "此前已拒绝，本次不重复累计",
  SCOPE_CONFLICT: "同一账目和维度只能选择一项，请调整选择",
  RULE_STALE: "规则版本已变化，请刷新核对", LEDGER_INACTIVE: "账目已失效，请刷新核对",
  MANUAL_TAG_CONFLICT: "已有人工标签，不能强制覆盖", NOT_FOUND: "请求不存在，请刷新核对",
  REQUEST_STATE_CONFLICT: "请求已处理或失效，请刷新核对", VIEW_INACTIVE: "标签维度已停用",
  TAG_INACTIVE: "候选标签已停用", UNKNOWN: "结果未知，请刷新核对后再决定重试",
  COUNTER_EXHAUSTED: "规则累计计数已达上限，本项未处理，请检查服务端诊断",
};

export function batchResults(body, ids, operation) {
  const records = Array.isArray(body?.items) ? body.items : [];
  return ids.map((id) => {
    const record = records.find((item) => (item.request_id ?? item.id) === id);
    // M1 returns persisted requests; M2's per-item result contract is also supported.
    const result = record?.result || (record?.status === (operation === "approve" ? 2 : 3)
      ? (operation === "approve" ? "APPROVED" : "REJECTED") : "UNKNOWN");
    return { id, result, copy: resultCopy[result] || resultCopy.UNKNOWN,
      category: ["APPROVED", "REJECTED"].includes(result) ? "success"
        : ["ALREADY_APPROVED", "ALREADY_REJECTED"].includes(result) ? "handled" : "failed" };
  });
}

export function batchResultMarkup(items, simulated = false) {
  const count = (kind) => items.filter((item) => item.category === kind).length;
  return `<div class="automation-result ${count("failed") ? "error" : ""}" role="status" aria-live="polite">
    <strong>${simulated ? "虚构交互演示 · 不写账本 · " : ""}成功 ${count("success")} · 已处理 ${count("handled")} · 失败/未知 ${count("failed")}</strong>
    <ul>${items.map((item) => `<li>Request #${esc(item.id)}：${esc(item.copy)}（${esc(item.result)}）</li>`).join("")}</ul>
    <span>成功项无需再次提交；失败项请先核对。不会自动重试或强制覆盖人工标签。</span></div>`;
}

// An atomic command error is not a per-request result. Only known pre-commit
// rejections prove that nothing was submitted; transport/unknown errors do not.
export function batchFailureMarkup(error, ids) {
  const rejections = {
    TAG_REQUEST_NOT_FOUND: [404, "至少一条请求不存在，请刷新核对选择。"],
    TAG_REQUEST_NOT_PENDING: [409, "至少一条请求已处理或失效，请刷新核对选择。"],
    TAG_REQUEST_STALE: [409, "至少一条申请的适用条件已变化（账目、规则、标签或人工来源），请刷新核对。"],
    TAG_REQUEST_SCOPE_CONFLICT: [409, "本次选择存在冲突：同一账目和维度只能选择一项，请调整选择。"],
  };
  const rejection = Object.hasOwn(rejections, error?.code) ? rejections[error.code] : null;
  const known = rejection && error?.status === rejection[0];
  return `<div class="automation-result error" role="status" aria-live="polite">
    <strong>${known ? "整批未提交" : "提交结果未知"}</strong>
    <p>${known ? rejection[1] : "可能已经执行，请先刷新核对，再决定是否重试。不会自动重发。"}</p>
    <p>本次选择：${ids.map((id) => `#${esc(id)}`).join("、")}。未获得逐项结果，不能据此判断每一项的原因。</p></div>`;
}

export function selectionConflict(items) {
  const scopes = new Set();
  for (const item of items) {
    const scope = `${item.ledger_id}:${item.view_id}`;
    if (scopes.has(scope)) return true;
    scopes.add(scope);
  }
  return false;
}

export function ruleStatistics(summary) {
  if (!summary) return '<p>统计暂不可用</p>';
  const rate = (value) => value == null ? "暂无样本" : `${(value * 100).toFixed(1)}%`;
  const fields = [["累计已分析", summary.analyzed_count], ["失败", summary.failed_count],
    ["建议请求", summary.suggested_count], ["人工通过", summary.accepted_count], ["人工拒绝", summary.rejected_count],
    ["执行成功率", `${rate(summary.execution_success_rate)}（${summary.execution_success_count} / ${summary.analyzed_count}）`],
    ["采纳率", `${rate(summary.acceptance_rate)}（${summary.accepted_count} / ${summary.decision_count} 次人工决定）`]];
  return `<dl class="automation-definition">${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
    ${helpTip("统计口径", "执行成功率 =（已分析 − 失败）/ 已分析；采纳率 = 人工通过 /（人工通过 + 人工拒绝）。已分析含未调用模型的条目；两项均非模型准确率。")}`;
}

const phaseCopy = { SCAN: "检查候选", CALL: "请求模型", RETRY_WAIT: "等待重试", COMMIT: "原子提交", FINISH: "本轮结束" };
const outcomeCopy = { NO_DATA: "无待分析数据", NO_CALL: "清洗后未调用模型", INSUFFICIENT: "模型依据不足",
  SUGGESTION: "已生成待审申请", SKIPPED: "不满足分析条件", RETRY_DEFERRED: "本项留到后续 CRON",
  SOFT_BUDGET_EXHAUSTED: "本轮预算结束", RUN_COMPLETED: "正常完成" };
const localTime = (value) => value ? new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Hong_Kong" }) : "—";

export function scheduleProgressMarkup(progress) {
  if (!progress) return "";
  return `<small>${esc(phaseCopy[progress.phase] || progress.phase)} · 已检查 ${esc(progress.inspected_count || 0)} / ${esc(progress.page_total || 0)} · 调用模型项 ${esc(progress.submitted_count || 0)} · 尝试 ${esc(progress.attempt || 0)} / 3</small>
    <small>未调用 ${esc(progress.no_call_count || 0)} · 依据不足 ${esc(progress.insufficient_count || 0)} · 待审申请 ${esc(progress.request_count || 0)} · 失败 ${esc(progress.failed_count || 0)} · 跳过 ${esc(progress.skipped_count || 0)}</small>`;
}

export function runtimeMarkup(schedule) {
  if (!schedule) return '<div class="automation-result error">调度状态未知，请检查服务连接。</div>';
  const tasks = schedule.tasks || [];
  const queue = tasks.filter((task) => task.queue_state === "QUEUED").sort((a, b) => a.queue_position - b.queue_position);
  const running = tasks.filter((task) => task.queue_state === "RUNNING");
  const rows = tasks.map((task) => `<tr><td><strong>${task.task_key === "system:import-preview-timeout" ? "导入预览超时清理（系统维护）" : esc(task.display_name || task.task_key)}</strong><small>${esc(task.task_key)}</small></td>
    <td>${esc(task.queue_state)}${task.queue_position == null ? "" : ` · 排队第 ${esc(task.queue_position)} 位 · 已等待 ${Math.floor((task.wait_ms || 0) / 1000)} 秒<small>入队 ${esc(localTime(task.enqueued_at))}</small>`}
    ${task.queue_state === "RUNNING" ? `<small>开始 ${esc(localTime(task.started_at))} · 已耗时 ${Math.floor((task.elapsed_ms || 0) / 1000)} 秒</small>${scheduleProgressMarkup(task.progress)}` : ""}
    ${task.queue_state === "BLOCKED" ? `<small>已阻塞，连续 ${esc(task.blocked_attempts || 1)} 轮；${task.last_error_code === "REGISTER_FAILED" ? "请重新保存规则" : "后续 CRON 只尝试恢复，不推进未处理项"}</small>` : ""}</td>
    <td>${esc(task.last_result || "尚未运行")}${task.last_error_code ? ` · ${esc(task.last_error_code)}` : ` · ${esc(outcomeCopy[task.last_outcome_code] || "")}`}
    ${scheduleProgressMarkup(task.last_progress)}${task.last_run_id ? `<small>诊断编号 <code>${esc(task.last_run_id)}</code></small>` : ""}</td><td>${esc(localTime(task.next_run_at))}</td></tr>`).join("");
  const failures = tasks.filter((task) => task.last_failure).map((task) => `<div class="automation-result error"><strong>${esc(task.task_key)} · 最近失败（不会被空扫描清除）</strong><p>${esc(task.last_failure.safe_message)}（${esc(task.last_failure.code)}）</p><small>${esc(localTime(task.last_failure.time))} · 诊断编号 <code>${esc(task.last_failure.run_id)}</code></small></div>`).join("");
  return `<p>调度器 ${esc(schedule.scheduler_state)} · Worker ${esc(schedule.worker_state)} · 等待 ${queue.length} · 运行 ${running.length}</p>
    <p>等待队列：${queue.length ? queue.map((task) => esc(task.task_key)).join(" → ") : "无"} ${helpTip("排队顺序", "任务按入队顺序执行，运行中的任务不计入等待数量。")}</p>
    <div class="automation-table-wrap"><table class="automation-table"><thead><tr><th>任务</th><th>当前状态</th><th>最近结果</th><th>下次触发</th></tr></thead><tbody>${rows || '<tr><td colspan="4">尚无已注册任务</td></tr>'}</tbody></table></div>
    ${failures}${schedule.diagnostics_health === "DEGRADED" ? '<div class="automation-result error" role="alert">安全诊断存储异常或部分历史损坏；当前仅保证最近 100 条内存记录，请检查本地日志目录权限和磁盘。</div>' : ""}
    <p class="automation-detail-note">更新于 ${esc(localTime(schedule.captured_at))} ${helpTip("运行记录", "队列和最近结果来自当前进程；累计数据与申请保存在数据库。诊断日志按容量轮转。")}</p>`;
}

export function diagnosticsMarkup(page, { taskKey = "", severity = "ERROR", code = "" } = {}) {
  const current = page?.page_index || 1;
  const last = Math.max(1, Math.ceil((page?.total || 0) / (page?.page_size || 10)));
  return `<section data-diagnostic-state data-page="${current}" data-task-key="${esc(taskKey)}"><h3>安全诊断记录</h3>
    <form class="form-grid three" data-form="schedule-diagnostics"><label>任务<input name="task_key" maxlength="96" value="${esc(taskKey)}" placeholder="例如 tag-scan:3"></label>
      <label>级别<select name="severity">${[["ERROR", "仅错误"], ["WARNING", "仅警告"], ["INFO", "仅正常结果"], ["", "全部"]].map(([value, label]) => `<option value="${value}" ${value === severity ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <label>错误码<input name="code" maxlength="96" value="${esc(code)}" placeholder="可留空"></label><button type="submit" class="quiet">筛选诊断</button></form>
    ${!page ? '<p class="error" role="alert">诊断记录读取失败，请刷新。</p>' : `<div class="automation-detail-list">${page.items.map((event) => `<div><strong>${esc(localTime(event.time))} · ${esc(event.task_key)} · ${esc(event.phase)}</strong><span>${esc(event.safe_message)}（${esc(event.code)}${event.detail_code ? ` / ${esc(event.detail_code)}` : ""}）</span><small>${event.ledger_id == null ? "" : `本地 Ledger #${esc(event.ledger_id)} · `}${event.attempt == null ? "" : `第 ${esc(event.attempt)} 次尝试 · `}诊断编号 <code>${esc(event.run_id)}</code></small><button type="button" class="quiet" data-action="copy-diagnostic" data-run-id="${esc(event.run_id)}">复制诊断编号</button></div>`).join("") || "<p>当前筛选无诊断记录。</p>"}</div>`}
    <div class="request-pager"><span>保留记录 ${esc(page?.total ?? "未知")} · 第 ${current}/${last} 页</span><div><button type="button" class="quiet" data-action="diagnostic-page" data-diagnostic-page="${current - 1}" ${current <= 1 ? "disabled" : ""}>上一页</button><button type="button" class="quiet" data-action="diagnostic-page" data-diagnostic-page="${current + 1}" ${current >= last ? "disabled" : ""}>下一页</button></div></div>
    </section>`;
}

// UI-phase examples, explicitly separated from live APIs and persisted counters.
export const interactionScenarios = {
  PARTIAL: "批量部分成功 / 已处理 / 过期冲突",
  MANUAL: "同值人工修改：保护人工来源",
  QUEUE: "排队 / 运行 / 安全诊断",
  EMPTY: "无新增数据 / 无建议 / 未调用 / 失败",
  ZERO: "统计零分母",
  LARGE: "统计超 JS 安全整数",
  OFFLINE: "离线 / 未知 / 注册失败",
};

export function interactionMarkup(scenario) {
  const intro = '<div class="automation-notice compact"><strong>虚构交互演示 · M2-UI</strong><span>以下全部为固定样例，不读取或修改真实账本，不发送批准请求，不是当前运行结果。M2-CORE 的实际结果请看运行状态及安全诊断。</span></div>';
  if (scenario === "PARTIAL" || scenario === "MANUAL") {
    const codes = scenario === "PARTIAL" ? ["APPROVED", "NOT_FOUND", "ALREADY_APPROVED", "RULE_STALE"] : ["MANUAL_TAG_CONFLICT", "SCOPE_CONFLICT"];
    const items = codes.map((result, index) => ({ request_id: 1001 + index, result }));
    return intro + batchResultMarkup(batchResults({ items }, items.map((item) => item.request_id), "approve"), true)
      + (scenario === "MANUAL" ? "<p>同值人工操作也成为人工来源；旧自动申请不得覆盖。只调整目标 View，其他 View 保持不变。此处仅为固定演示。</p>" : "");
  }
  if (scenario === "ZERO" || scenario === "LARGE") {
    const big = scenario === "LARGE";
    return intro + ruleStatistics({ analyzed_count: big ? "9223372036854775807" : "0", failed_count: "0",
      suggested_count: big ? "24" : "0", accepted_count: big ? "6" : "0", rejected_count: big ? "2" : "0",
      execution_success_count: big ? "9223372036854775807" : "0", execution_success_rate: big ? 1 : null,
      decision_count: big ? "8" : "0", acceptance_rate: big ? 0.75 : null });
  }
  if (scenario === "QUEUE") return intro + runtimeMarkup({ scheduler_state: "RUNNING", worker_state: "HEALTHY", captured_at: "虚构时刻",
    tasks: [{ task_key: "tag-scan:7", queue_state: "RUNNING", last_result: "PARTIAL_FAILURE", last_error_code: "OUTPUT_JSON_INVALID" },
      { task_key: "tag-scan:8", queue_state: "QUEUED", queue_position: 1 }] })
      + "<p>未来进度演示：规则 7 处于 CALLING，已检查 3 / 100，耗时 20 秒；规则 8 等待 20 秒。</p><p>安全诊断演示：PARSE · OUTPUT_JSON_INVALID · 第 1 次尝试 · 虚构 Ledger #104。只展示白名单定位字段，不展示 Prompt、原始模型输出或密钥。</p>";
  if (scenario === "OFFLINE") return intro + runtimeMarkup(null)
    + "<p>注册失败：REGISTRATION_FAILED，请检查 CRON / 时区；保存不等于成功注册。</p><p>鉴权失败：AUTH_ERROR，请检查模型凭据；不能用健康检查成功覆盖业务失败。</p><p>离线时保留旧快照仅供参考，不标为当前运行，也不自动重发人工确认。</p>";
  return intro + "<ul><li>无新增数据：本轮没有可分析条目，未调用模型。</li><li>清洗后无业务语义：不调用模型；按现有合同记为已分析，不产生建议。</li><li>模型依据不足：调用成功，decision=insufficient，不产生申请。</li><li>合法建议：后端校验后只生成待审 request，仍需人工批准或拒绝。</li><li>OUTPUT_SEMANTIC_INVALID：分析失败，不生成申请；新记录提供固定细分原因，旧日志不能补造具体字段。</li></ul><p>这些是不同结果，不把“已分析”都解释成模型已给出建议。</p>";
}

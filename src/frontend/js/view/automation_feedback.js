import { esc } from "../util/core.js";

const resultCopy = {
  APPROVED: "已通过并应用标签", REJECTED: "已拒绝，未改变标签",
  ALREADY_APPROVED: "此前已通过，本次不重复累计", ALREADY_REJECTED: "此前已拒绝，本次不重复累计",
  SCOPE_CONFLICT: "同一账目和维度只能选择一项，请调整选择",
  RULE_STALE: "规则版本已变化，请刷新核对", LEDGER_INACTIVE: "账目已失效，请刷新核对",
  MANUAL_TAG_CONFLICT: "已有人工标签，不能强制覆盖", NOT_FOUND: "请求不存在，请刷新核对",
  REQUEST_STATE_CONFLICT: "请求已处理或失效，请刷新核对", VIEW_INACTIVE: "标签维度已停用",
  TAG_INACTIVE: "候选标签已停用", UNKNOWN: "结果未知，请刷新核对后再决定重试",
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
  if (!summary) return '<p>统计当前不可用；不推算准确率或无建议数量。</p>';
  const rate = (value) => value == null ? "暂无样本" : `${(value * 100).toFixed(1)}%`;
  const fields = [["累计已分析", summary.analyzed_count], ["失败", summary.failed_count],
    ["建议请求", summary.suggested_count], ["人工通过", summary.accepted_count], ["人工拒绝", summary.rejected_count],
    ["执行成功率", `${rate(summary.execution_success_rate)}（${summary.execution_success_count} / ${summary.analyzed_count}）`],
    ["采纳率", `${rate(summary.acceptance_rate)}（${summary.accepted_count} / ${summary.decision_count} 次人工决定）`]];
  return `<dl class="automation-definition">${fields.map(([label, value]) => `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
    <p class="automation-detail-note">已分析包含失败、模型依据不足和清洗后未调用模型的条目。执行成功率 =（已分析 − 失败）/ 已分析，成功不代表产生建议；采纳率 = 人工通过 /（人工通过 + 人工拒绝）。自动取消不算拒绝；建议按请求数累计。这两项都不是模型准确率，不能据此反推无建议或待审数量。</p>`;
}

export function runtimeMarkup(schedule) {
  if (!schedule) return '<div class="automation-result error">调度状态未知，无法确认正在运行。请检查本地服务。</div>';
  const tasks = schedule.tasks || [];
  const queue = tasks.filter((task) => task.queue_state === "QUEUED").sort((a, b) => a.queue_position - b.queue_position);
  const running = tasks.filter((task) => task.queue_state === "RUNNING");
  const rows = tasks.map((task) => `<tr><td>${esc(task.task_key)}</td><td>${esc(task.queue_state)}${task.queue_position == null ? "" : ` · 排队第 ${esc(task.queue_position)} 位`}</td><td>${esc(task.last_result || "尚无结果")}${task.last_error_code ? ` · ${esc(task.last_error_code)}` : ""}</td><td>${esc(task.next_run_at || "—")}</td></tr>`).join("");
  return `<p>调度器 ${esc(schedule.scheduler_state)} · Worker ${esc(schedule.worker_state)} · 等待 ${queue.length} · 运行 ${running.length}</p>
    <p>共享 FIFO：${queue.length ? queue.map((task) => esc(task.task_key)).join(" → ") : "没有等待项"}。运行中的任务不计入等待数量。</p>
    <div class="automation-table-wrap"><table class="automation-table"><thead><tr><th>任务</th><th>当前状态</th><th>最近结果</th><th>下次触发</th></tr></thead><tbody>${rows || '<tr><td colspan="4">尚无已注册任务</td></tr>'}</tbody></table></div>
    <p class="automation-detail-note">真实进程快照：${esc(schedule.captured_at || "未提供时间")}。重启后瞬态队列和最近结果不恢复；数据库游标、累计与申请保留。当前接口不提供逐项阶段进度、安全诊断事件历史或日志轮转；这些属于 M2-CORE，调度健康不代表模型分析成功。</p>`;
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
  const intro = '<div class="automation-notice compact"><strong>虚构交互演示 · M2-UI</strong><span>以下全部为固定样例，不读取或修改真实账本，不发送批准请求，不是当前运行结果。逐项部分成功、完整保护与持久诊断仍待 M2-CORE。</span></div>';
  if (scenario === "PARTIAL" || scenario === "MANUAL") {
    const codes = scenario === "PARTIAL" ? ["APPROVED", "NOT_FOUND", "ALREADY_APPROVED", "RULE_STALE"] : ["MANUAL_TAG_CONFLICT", "SCOPE_CONFLICT"];
    const items = codes.map((result, index) => ({ request_id: 1001 + index, result }));
    return intro + batchResultMarkup(batchResults({ items }, items.map((item) => item.request_id), "approve"), true)
      + (scenario === "MANUAL" ? "<p>同值人工操作也应成为人工来源；旧自动申请不得覆盖。只调整目标 View，其他 View 保持不变。此处演示预期保护，不表示后端已补齐该竞态。</p>" : "");
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
  return intro + "<ul><li>无新增数据：本轮没有可分析条目，未调用模型。</li><li>清洗后无业务语义：不调用模型；按现有合同记为已分析，不产生建议。</li><li>模型依据不足：调用成功，decision=insufficient，不产生申请。</li><li>合法建议：后端校验后只生成待审 request，仍需人工批准或拒绝。</li><li>OUTPUT_SEMANTIC_INVALID：分析失败，不生成申请；不能从现有历史错误码猜测具体字段。</li></ul><p>这些是不同结果，不把“已分析”都解释成模型已给出建议。逐项安全诊断的真实历史展示将在 M2-CORE 接入。</p>";
}

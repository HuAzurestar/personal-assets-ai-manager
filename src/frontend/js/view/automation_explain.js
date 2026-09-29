import { esc, money, date } from "../util/core.js";
import { helpTip } from "./automation_feedback.js?v=20260928.6";

export function ruleExecution(rule, schedule, model, view) {
  const state = (label, reason, tone = "inactive") => ({ label, reason, tone });
  if (!rule.enabled) return state("已停用", "不再安排新扫描；已有待确认建议仍可处理");
  if (!schedule) return state("状态未知", "暂时无法读取调度状态，请检查连接");
  if (view && view.status !== "ACTIVE") return state("维度已停用", "请先恢复目标标签维度");
  if (!model?.enabled || !model?.key_configured) return state("模型不可用", !model?.enabled ? "请启用所选模型" : "请在设置中配置模型密钥");
  if (schedule.tag_scan_guard === "DISABLED") return state("自动分析已关闭", "");
  if (schedule.tag_scan_guard === "NON_SYNTHETIC_FACT") return state("安全阻断", "验收库含非虚构记录，扫描已暂停");
  const task = schedule.tasks?.find((item) => item.task_key === `tag-scan:${rule.id}`);
  if (!task) return state("尚未调度", "未注册扫描任务，请查看规则详情");
  if (task.queue_state === "PAUSED") return state("已暂停", "不会按定时计划触发，请查看诊断");
  if (task.queue_state === "BLOCKED") return state("执行受阻", "请查看规则详情中的失败原因");
  if (schedule.scheduler_state !== "RUNNING" || schedule.worker_state !== "HEALTHY") return state("调度不可用", "调度器或执行器未就绪，请查看运行概况");
  if (task.queue_state === "RUNNING") return state("分析中", "正在检查账目", "active");
  if (task.queue_state === "QUEUED") return state("排队中", task.queue_position == null ? "等待前面的任务完成" : `排队第 ${task.queue_position} 位`, "pending");
  if (task.queue_state === "IDLE") return state("等待定时触发", task.next_run_at ? `下次：${date(task.next_run_at)}（当前显示时区）` : "当前没有下次触发时间，请检查计划", "pending");
  return state("状态未知", "未识别的执行状态，请查看运行概况");
}

export function semanticRuleChange(original, draft) {
  return Boolean(original && (original.method_config.model_id !== Number(draft.model_id)
    || original.method_config.prompt.trim() !== String(draft.prompt).trim()
    || original.amount_mode !== Number(draft.amount_mode)));
}

export function ruleEditImpact(original, draft, pendingCount) {
  if (!semanticRuleChange(original, draft)) return "仅修改名称、定时计划或启用状态，不增加判断版本、不重置扫描进度，也不取消已有建议。";
  const count = pendingCount == null ? "数量尚未读取" : `当前 ${pendingCount} 条`;
  return `判断内容发生变化：规则修订 ${original.rule_revision} → ${original.rule_revision + 1}；扫描进度重置，将从头重新检查账目；${count}待确认建议将被取消，不能再通过。已通过的标签不会被直接撤销。实际影响以保存时校验为准。`;
}

const previewReasonNames = {
  ELIGIBLE: "满足本地筛选条件", RULE_DISABLED: "规则已停用", VIEW_INACTIVE: "标签维度不可用",
  MODEL_DISABLED: "模型已停用", NO_ACTIVE_TARGET_TAG: "没有可用目标标签", INACTIVE_LEDGER: "账目已失效",
  MISSING_VIEW_TAG: "缺少目标维度标签", ALREADY_CLASSIFIED: "已有非默认标签", EXISTING_REQUEST: "已有同版本建议",
};

export function candidatePreviewMarkup(body) {
  const reasons = Object.entries(body.reason_counts).map(([name, count]) => `<span>${esc(previewReasonNames[name] || `其他原因（${name}）`)} <strong>${esc(count)}</strong></span>`).join("");
  const samples = body.samples.map((item) => `<li><button type="button" class="quiet" data-preview-ledger="${item.ledger_id}">${esc(item.counterparty_name || item.summary || "未记录交易对方")} · ${esc(money(item))}</button><span>${esc(item.summary || "无摘要")} · ${esc(date(item.occurred_time))} · 账目 #${item.ledger_id}</span></li>`).join("");
  return `<strong>待分析账目 ${helpTip("本地筛选预览", "从已扫描位置之后检查最多 100 条，展示最多 20 条样例；不调用模型。")}</strong><p>已扫描至 #${body.scan_after_ledger_id} · 本次检查 ${body.inspected_count} 条 · 符合条件 ${body.eligible_count} 条</p>
    <div class="preview-reasons">${reasons || "此范围内没有账目"}</div>
    <ul class="candidate-samples">${samples || "<li>本次检查范围内没有可展示的待分析样例。</li>"}</ul>`;
}

export function requestVersionCopy(item, rule) {
  if (rule.rule_revision === item.rule_revision) return `规则版本 ${item.rule_revision}`;
  return item.status === 1
    ? `建议来自修订 ${item.rule_revision}，当前规则为修订 ${rule.rule_revision}；版本已过期，不能通过，可拒绝或等待刷新核对。`
    : `建议来自修订 ${item.rule_revision}，当前规则为修订 ${rule.rule_revision}；此建议已处理，仅供追溯，不影响已记录的处理结果。`;
}

export function scopeImpactMarkup(item, scope) {
  const effect = "通过后修改当前维度标签，同范围其他待确认建议将取消，原已通过建议将被替换。拒绝时保留原标签。";
  if (!scope) return `<p class="automation-detail-note">同范围建议暂时读取失败，影响数量未知；请刷新后核对。</p><p>${effect}</p>`;
  const pending = scope.pending.items.filter((other) => other.id !== item.id);
  const approved = scope.approved.items.filter((other) => other.id !== item.id);
  const otherCount = Math.max(0, scope.pending.total - (item.status === 1 ? 1 : 0));
  const row = (other) => `<li><button type="button" class="quiet" data-action="tag-request-detail" data-id="${other.id}">#${other.id} · ${esc(other.proposed_tag_name)}</button><span>${esc(other.rule_name)} · ${other.status === 1 ? "待确认" : "已通过"} · ${esc(other.reason_summary || "未提供理由")}</span></li>`;
  return `<p>同账目、同维度：其他待确认 ${otherCount} 条 · 已通过 ${scope.approved.total} 条。</p><p>${effect}</p>
    <ul class="candidate-samples">${[...pending, ...approved].map(row).join("") || "<li>没有其他待确认或已通过建议。</li>"}</ul>
    <small>每种状态最多展示最近 20 条，数量以当前读取为准，提交时会再次校验。</small>
    <button type="button" class="quiet" data-action="tag-request-scope" data-ledger-id="${item.ledger_id}" data-view-id="${item.view_id}">查看同账目同维度的全部建议</button>`;
}

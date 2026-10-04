import { request, jsonRequest, isUnknownWrite } from "../api/client.js";
import { esc, money, date, resourceId } from "../util/core.js";
import { mountPicker, workbenchDialog, metadataLabel } from "../component/workbench.js";
import { mountImportPlan, validateImportPlan } from '../component/import-plan.js';
import { completeImportScope } from '../util/import-scope.js';
import { importRowChoice, importRiskState, importRiskUnresolved, importRiskLabel, importIssueMessage } from '../util/import-decision.js';
import { openImportChoice, importIntentNames } from '../component/import-choice.js';
import { openImportBulk, importRangeFilter, mountImportDraftRows } from '../component/import-bulk.js';
import { openImportBinding, readImportBinding } from '../component/import-binding.js';
import { openImportPairing } from '../component/import-pairing.js';
import { importReconciliationInput, validateImportReconciliation, reconciliationRowLabel, reconciliationOutputLabel } from '../component/import-reconciliation.js';
import { createImportExecution, importKnownFailureMessage } from '../component/import-execution.js';

const pendingKey = "paam.import.pending.v1";
const remainingKey = 'paam.import.remaining.v1';
const contexts = new Map();
let controller, activeExecution;
export function stopImportRead() { controller?.abort();void activeExecution?.stop(); }
const identity = row => `${row.file_id}:${row.source_row_number}`;
const classifications = { NEW: "拟新增", EXISTING: "仅补证据", PROCESSED: "已接受（只读）", INVALID: "需要核对", AMBIGUOUS: "身份不唯一" };
const statusNames = { 0: "尚无处理状态", 1: "已接受", 2: "已跳过", 3: "有问题" };

function pending() {
  try { return JSON.parse(localStorage.getItem(pendingKey) || "null"); }
  catch { return { token: "", rows: [], files: [], malformed: true }; }
}

export async function mountImportBatch(host, initial, changed) {
  stopImportRead();
  controller = new AbortController();
  const signal = controller.signal;
  let context = contexts.get(initial.token);
  if (!context) {
    context = { plan: initial, selected: new Map(), dirty: false, page: 1, busy: false, generation: 0, unknown: !!pending(), verificationReady: false };
    contexts.set(initial.token, context);
    // Keep UI state bounded just as the server bounds preview residency.
    while (contexts.size > 128) contexts.delete(contexts.keys().next().value);
  }
  context.plan = initial;
  context.pageSize ||= 20;
  if (!context.selected.size) {
    try {
      const retained=JSON.parse(localStorage.getItem(remainingKey) || 'null');
      const sameFiles=retained?.files?.length && retained.files.every(proof=>initial.files.some(file=>file.file_id === proof.file_id && file.sha256 === proof.sha256));
      if ((retained?.token === initial.token || sameFiles) && retained.choices?.length) {
        if (retained.choices.length > 20000) throw new Error('剩余选择超过完整范围上限');
        const restored=new Map();
        for (const choice of retained.choices) {
          const row={file_id:resourceId(choice.file_id),source_row_number:resourceId(choice.source_row_number),classification:'INVALID',issue_codes:['RESTORE_REQUIRED']};
          if (restored.has(identity(row))) throw new Error('剩余选择重复');
          restored.set(identity(row),{row,choice});
        }
        context.selected=restored;context.dirty=true;context.restoreRequired=true;
      }
    } catch {context.restoreRequired=true;context.recoveryError='剩余范围定位无法完整恢复；请保留未知定位并按原文件核对，不能自动继续。';}
  }
  let page, scopeController;
  host.innerHTML = `<div class="preview-section"><h2 id="preview-title">检查导入结果</h2>
    <ol class="import-guide-steps"><li>检查文件与默认决定</li><li>核验并处理例外</li><li>核对计划后确认入账</li></ol>
    <p>正常记录默认接受，异常或疑似重复默认跳过；跳过保留原证据。先点击“核验当前筛选”，无需逐条选择来源卡或记录；不会自动入账。</p>
    <div data-batch-files></div><p data-batch-summary></p>
    <div class="import-batch-toolbar" data-batch-toolbar><p data-batch-selection></p><small data-batch-selected-scope></small>
      <p data-batch-guide-hint></p><div class="actions"><button type="button" class="primary" data-batch-guide>核验当前筛选（不入账）</button><button type="button" data-batch-select-page>选择本页未接受行</button><button type="button" data-batch-bulk>批量修改意图／决定</button><button type="button" data-batch-bind>批量绑定来源</button><button type="button" data-batch-pair>具名批配对</button>
      <button type="button" data-batch-save>核验导入（保存选择，不入账）</button><button type="button" data-batch-plan>查看完整处理计划（不写入）</button><button type="button" class="primary" data-batch-confirm>确认并写入本批</button>
      <button type="button" data-batch-restore>重新读取已保留的剩余范围</button><button type="button" class="primary" data-batch-execute>批准完整计划并依次入账</button><button type="button" data-batch-stop-execution>停止后续批次</button></div>
    </div><label class="import-operation-consent" data-batch-consent-panel><input type="checkbox" data-batch-consent>我已核对完整计划，理解各批独立提交；停止／失败／未知时保留已完成批，剩余须重新核对和批准。</label><p role="status" data-batch-execution></p>
    <div class="actions import-batch-filter"><label>文件<select data-batch-file><option value="">全部文件</option>${initial.files.map(file => `<option value="${file.file_id}">${esc(file.filename)} · #${file.file_id}</option>`).join("")}</select></label>
      <label>分类<select data-batch-classification><option value="">全部分类</option>${Object.entries(classifications).map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
      <label>每页<select data-batch-page-size>${[20,50,100].map(size => `<option value="${size}" ${context.pageSize === size ? 'selected' : ''}>${size}行</option>`).join('')}</select></label><button type="button" data-batch-refresh>读取当前预览</button>
    </div><details class="import-batch-scope" open><summary>跨页范围选择／清空／核对</summary><p>范围完整读取成功后才加入；不会自动接受新现金风险，失败／取消不加入部分范围。区间按文件原始行号，含起始、不含结束，分类筛选仍适用；只改变选择，不改变列表显示范围。</p>
      <div class="actions"><label>起始行（含）<input type="number" min="1" step="1" data-batch-range-start></label><label>结束行（不含）<input type="number" min="2" step="1" data-batch-range-end></label><button type="button" data-batch-select-range>选择指定文件行号区间</button></div>
      <div class="actions"><button type="button" data-batch-select-scope>选择当前筛选全部未接受行</button><button type="button" data-batch-stop-scope disabled>停止范围读取</button><button type="button" data-batch-clear>清空本批选择</button><button type="button" data-batch-verify>核对所选行当前持久状态</button><button type="button" data-batch-cancel>取消驻留预览</button></div>
    </details><section data-batch-risk-panel><p role="status" data-batch-risk-summary></p><div class="actions"><button type="button" data-batch-risk-list>查看所选风险行（跨页完整范围）</button><button type="button" data-batch-risk-skip>将未解决风险行设为跳过</button></div></section><p role="status" data-batch-status></p><div data-batch-rows></div>
    <div class="actions"><button type="button" data-batch-prev>上一页</button><span data-batch-page></span><button type="button" data-batch-next>下一页</button></div>
    <div data-batch-operation></div><div data-batch-verification></div></div>`;
  const find = selector => host.querySelector(selector);
  const live = () => host.isConnected && !signal.aborted;
  const status = message => {
    if (!live()) return;
    const slot = find('[data-batch-status]');
    if (slot) slot.textContent = message;
  };
  // Whole-plan execution has separate informed approval. The old button stays
  // single-batch <=1000, never silently promoting a paged action to all rows.
  const filtersFor = (range = false) => {
    const filters = [];
    const file = find('[data-batch-file]').value, classification = find('[data-batch-classification]').value;
    if (file) filters.push({key:'file_id',op:'=',val:resourceId(file)});
    if (classification) filters.push({key:'classification',op:'=',val:classification});
    if (range) filters.push(importRangeFilter(file,find('[data-batch-range-start]').value,find('[data-batch-range-end]').value));
    return filters.length ? filters.length === 1 ? filters[0] : {op:'AND',expression:filters} : null;
  };
  const invalidatePlan = () => {
    context.disclosure = null;
    context.preflightFailure = null;
    if (find('[data-batch-consent]')) find('[data-batch-consent]').checked=false;
    const panel = find('[data-batch-operation]');
    if (panel) panel.textContent = '';
  };
  const riskRows = () => [...context.selected.values()].filter(item => importRiskState(item.row) !== 'NONE_IN_SCOPE');
  const singleBatch = () => context.disclosure?.can_confirm && context.disclosure.source_preview_digest === context.plan.preview_digest && context.disclosure.batches.length === 1
    ? context.disclosure.batches[0].preview : null;
  const update = () => {
    if (!live()) return;
    const plan = context.plan;
    const failedFiles = plan.files.filter(file => file.parse_status === "FAILED").length;
    find("[data-batch-files]").innerHTML = plan.files.map(file => `<article class="preview-file-card"><strong>${esc(file.filename)} · #${file.file_id}</strong>
      ${file.parse_status === "FAILED" ? `<p class="error" role="alert" data-file-parse-error>解析失败 · ${esc(file.parse_issue_code)}：${esc(file.parse_issue_message)}</p><p>${esc(file.parse_recovery)}</p>`
        : file.parse_status === "EMPTY" ? '<p data-file-parse-empty>解析成功；有效空文件，没有交易记录。未创建新事实。</p>' : `<p>解析成功：${file.parsed_row_count} 行。</p>`}
      <p>已接受 ${file.accepted} · 跳过 ${file.skipped} · 无效记录 ${file.invalid} · 剩余 ${file.remaining}</p><small>活动范围 ${esc(file.activity_range.start || "未知")} 至 ${esc(file.activity_range.end || "未知")}（不是完整期间覆盖）</small></article>`).join("")
      + ((plan.issues || []).some(issue => issue.source_row_number > 0) ? `<details data-batch-issues><summary>查看本次行问题提示</summary>${plan.issues.filter(issue => issue.source_row_number > 0).map(issue => `<p>文件 #${issue.file_id} · 第 ${issue.source_row_number} 行：${esc(issue.code)}</p>`).join("")}${plan.has_more_issues ? "<p>此处仅为有界提示；其余问题请按文件及分类分页核对，不能把本提示当作完整行列表。</p>" : ""}</details>` : "");
    find("[data-batch-summary]").textContent = `拟新增 ${plan.counts.new} · 仅补证据 ${plan.counts.existing} · 已接受 ${plan.counts.processed} · 文件解析失败 ${failedFiles} · 行问题 ${Math.max(0, plan.issue_count - failedFiles)} · 总问题 ${plan.issue_count}（此处是解析／身份问题，不含新增现金风险；风险见所选范围核验）${plan.has_more_issues ? "（行问题请按分类分页核对；文件失败已全部显示）" : ""}${plan.timed_out ? "；预览超时提示，确认仍会重新核验" : ""}`;
    const readiness = context.unknown ? '提交结果未知，先核对持久状态' : context.dirty ? '选择有未保存更改，须核验导入'
      : context.revalidationRequired ? '上次选择已保存 · 当前前提须重新核验'
      : context.disclosure ? context.disclosure.can_confirm ? '选择已保存 · 完整计划可提交' : '选择已保存 · 完整计划有阻断，须处理'
      : context.preflightFailure ? '选择已保存 · 须重新核验' : context.selected.size ? '选择已保存 · 尚未取得可提交的完整计划' : '尚未取得可提交的完整计划';
    find("[data-batch-selection]").textContent = `本次明确选择 ${context.selected.size} 行 · ${readiness}${context.selected.size > 1000 ? ' · 先核对拆批计划' : ''}${context.restoreRequired ? ' · 保留范围须重读' : ''}`;
    const selectedRows = [...context.selected.values()], fileIds = new Set(selectedRows.map(item => item.row.file_id));
    const files=plan.files.filter(file=>fileIds.has(file.file_id)).map(file=>file.filename);
    find('[data-batch-selected-scope]').textContent = `文件范围：${files.length === 1 ? files[0] : `${fileIds.size}份（完整范围见文件摘要／计划）`}；待处理 ${selectedRows.filter(item => item.row.classification !== 'PROCESSED').length}，行问题 ${selectedRows.filter(item => item.row.issue_codes?.length).length}；接受 ${selectedRows.filter(item => item.choice.decision === 'ACCEPT').length}，跳过 ${selectedRows.filter(item => item.choice.decision === 'SKIP').length}。`;
    find('[data-batch-selected-scope]').title=files.join('、');
    const risks = riskRows(), unresolved = risks.filter(item => importRiskUnresolved(item.row,item.choice));
    find('[data-batch-guide-hint]').textContent = context.unknown ? '本批结果未知：先核对持久状态，不要再次提交。'
      : context.executor ? '正在按已批准计划依次处理；可以停止后续批，已提交批保留。'
      : context.restoreRequired ? '先重新读取保留的剩余范围，再重新核验。'
      : context.disclosure?.can_confirm ? '完整计划已核验：检查接受／跳过数量，再明确确认入账。'
      : context.disclosure ? '计划有阻断：按文件和行号处理例外，然后再次核验。'
      : '核验会加入当前筛选的全部未接受行，保留已有选择及人工决定，保存并读取完整计划；不写账。';
    find('[data-batch-risk-summary]').textContent = `所选完整范围：拟新增风险 ${risks.length} 行（疑似重复 ${risks.filter(item => importRiskState(item.row) === 'SUSPECTED').length}，未核验 ${risks.filter(item => importRiskState(item.row) === 'UNCHECKED').length}）；已跳过 ${risks.filter(item => item.choice.decision === 'SKIP').length}，接受但未解决 ${unresolved.length}，已明确意图 ${risks.filter(item => item.choice.decision === 'ACCEPT' && !importRiskUnresolved(item.row,item.choice)).length}（仍须计划核验）。${context.disclosure ? `后端计划未解决 ${context.disclosure.batches.reduce((sum,batch) => sum + (batch.preview.issues?.length || 0),0)} 行；${context.disclosure.blocked.length} 个预算阻断组。` : '仅为已读取选择的风险提示；最终可提交性须完整计划核验。'}`;
    host.querySelectorAll("button,input,select").forEach(node => {
      if (!node.closest?.('[data-batch-operation]') && !node.closest?.('[data-batch-verification]')) node.disabled = context.busy || context.unknown || !!context.guiding;
    });
    if (find('[data-batch-operation]')) find('[data-batch-operation]').inert = context.busy || context.unknown;
    if (find('[data-batch-verification]')) find('[data-batch-verification]').inert = context.busy;
    find("[data-batch-verify]").disabled = context.busy;
    find("[data-batch-refresh]").disabled = context.busy;
    if (find("[data-batch-observed]")) find("[data-batch-observed]").disabled = context.busy || !context.verificationReady || plan.status === "CONFIRMING";
    find("[data-batch-confirm]").disabled = context.busy || context.unknown || context.dirty || context.revalidationRequired || !context.selected.size || context.selected.size > 1000 || plan.status === "CONFIRMING" || !singleBatch();
    find('[data-batch-risk-list]').disabled = context.busy || context.unknown || !risks.length;
    find('[data-batch-risk-skip]').disabled = context.busy || context.unknown || !unresolved.length || plan.status === 'CONFIRMING';
    find('[data-batch-execute]').disabled=context.busy || context.unknown || context.dirty || context.revalidationRequired || context.restoreRequired || plan.status === 'CONFIRMING'
      || !context.selected.size || !context.disclosure?.can_confirm || !find('[data-batch-consent]').checked;
    find('[data-batch-execute]').hidden=!context.disclosure || !!context.executor;
    find('[data-batch-consent-panel]').hidden=!context.disclosure || !!context.executor;
    for (const selector of ['[data-batch-select-page]','[data-batch-bulk]','[data-batch-bind]','[data-batch-pair]','[data-batch-save]','[data-batch-plan]','[data-batch-confirm]']) find(selector).hidden=!!context.executor;
    find('[data-batch-confirm]').hidden ||= context.disclosure?.batches.length > 1;
    find('[data-batch-stop-execution]').disabled=!context.executor || context.executor.state.stop_requested;
    find('[data-batch-stop-execution]').hidden=!context.executor;
    find('[data-batch-restore]').disabled=context.busy || context.unknown || !context.restoreRequired;
    find('[data-batch-restore]').hidden=!context.restoreRequired;
    find('[data-batch-guide]').disabled = context.busy || context.unknown || !!context.guiding || context.restoreRequired || plan.status === 'CONFIRMING';
    find('[data-batch-guide]').hidden = !!context.executor;
    if (find('[data-batch-plan]')) find('[data-batch-plan]').disabled = context.busy || context.unknown || context.dirty || context.revalidationRequired || !context.selected.size || plan.status === 'CONFIRMING';
    if (find('[data-batch-stop-scope]')) find('[data-batch-stop-scope]').disabled = !context.scopeReading;
    find('[data-batch-bulk]').disabled ||= !context.selected.size || plan.status === 'CONFIRMING';
    find('[data-batch-bind]').disabled ||= !context.selected.size || plan.status === 'CONFIRMING';
    find('[data-batch-pair]').disabled ||= !context.selected.size || plan.status === 'CONFIRMING';
    if (context.restoreRequired) for (const selector of ['[data-batch-save]','[data-batch-plan]','[data-batch-bulk]','[data-batch-bind]','[data-batch-pair]']) find(selector).disabled=true;
    find("[data-batch-prev]").disabled ||= context.page <= 1;
    find("[data-batch-next]").disabled ||= !page || context.page * page.page_size >= page.total;
    host.querySelectorAll("[data-batch-processed]").forEach(node => { node.disabled = true; });
    if (context.unknown) status(context.verificationMessage || "提交结果未知。不要重发；先核对当前行状态。410/预览丢失不能证明未提交。");
  };
  const readPage = async requestedPage => {
    if (context.busy) return;
    const issued = ++context.generation;
    context.busy = true;
    update();
    const params = new URLSearchParams({ page_index: requestedPage, page_size: context.pageSize, preview_digest: context.plan.preview_digest });
    try {
      const filter = filtersFor();
      if (filter) params.set('filter', JSON.stringify(filter));
      const next = await request(`/paam/import/v1/preview/${context.plan.token}/row/list?${params}`, { signal });
      if (!live() || issued !== context.generation) return;
      page = next;
      context.page = requestedPage;
      find("[data-batch-page]").textContent = `第 ${requestedPage} 页 · 每页 ${next.page_size} 行 · 当前筛选共 ${next.total} 行`;
      find("[data-batch-rows]").innerHTML = next.items.map(row => {
        const selected = context.selected.get(identity(row));
        const choice = importRowChoice(row, selected?.choice);
        const refText = choice.account_ref_id == null ? "可靠来源自动匹配／否则待绑定"
          : choice.account_ref_id === 0 ? "明确待绑定" : selected?.refLabel || `#${choice.account_ref_id}`;
        const processed = row.classification === "PROCESSED";
        return `<article class="import-batch-row" data-batch-row="${identity(row)}">
          <label class="import-batch-select"><input type="checkbox" data-row-select aria-label="选择文件 #${row.file_id} 第 ${row.source_row_number} 行" ${selected ? "checked" : ""} ${processed ? "data-batch-processed disabled" : ""}></label>
          <div class="import-batch-main"><strong>${esc(row.parsed.summary || "摘要未知")}</strong><small>${esc(row.parsed.cash_direction || "方向未知")} · ${esc(row.parsed.occurred_time ? date(row.parsed.occurred_time) : "时间未知")} · 文件 #${row.file_id} 第 ${row.source_row_number} 行${row.existing_transaction_id ? ` · Fact #${row.existing_transaction_id}` : ""}</small></div>
          <div class="import-batch-amount"><strong>${esc(row.parsed.amount == null ? "金额未知" : money(row.parsed))}</strong><small>${esc(classifications[row.classification])}</small></div>
          <div class="import-batch-source"><span data-row-ref>来源卡：${choice.resolution === 'LINK_EXISTING' ? '保留目标Fact当前来源和解释' : esc(refText)}</span>${row.classification === "NEW" && choice.resolution !== 'LINK_EXISTING' ? '<button type="button" data-row-account>选择来源卡</button>' : ""}</div>
          <div class="import-batch-decision"><label><span class="visually-hidden">本行决定</span><select data-row-decision aria-label="文件 #${row.file_id} 第 ${row.source_row_number} 行决定" ${processed ? "data-batch-processed disabled" : ""}><option value="ACCEPT" ${choice.decision === "ACCEPT" ? "selected" : ""}>接受</option><option value="SKIP" ${choice.decision === "SKIP" ? "selected" : ""}>跳过（问题行保留INVALID）</option></select></label>${[2, 3].includes(row.persisted_row_status) ? `<label class="import-batch-recheck"><input type="checkbox" data-row-recheck ${choice.recheck ? "checked" : ""}>重新检查未接受行（旧状态：${statusNames[row.persisted_row_status]}）</label>` : ""}</div>
          ${!processed && (['NEW','INVALID','AMBIGUOUS'].includes(row.classification) || ['LINK_EXISTING','DUPLICATE'].includes(choice.resolution)) ? `<div class="import-batch-intent"><span>${esc(importIntentNames[choice.resolution || 'AUTO'])}${selected?.targetLabel ? ` → ${esc(selected.targetLabel)}` : ''}</span><button type="button" data-row-intent>核对意图／重复</button></div>` : ''}
          ${row.issue_codes.length || !processed && importRiskState(row) !== 'NONE_IN_SCOPE' ? `<p class="import-batch-issue" role="note">${!processed && importRiskState(row) !== 'NONE_IN_SCOPE' ? `<span data-row-risk>${esc(importRiskLabel(row))}；${choice.decision === 'SKIP' ? '已跳过，原证据保留' : importRiskUnresolved(row,choice) ? '接受尚未解决风险，不能直接入账' : '已明确意图，仍须后端完整计划核验'}${row.duplicate_hint?.reason_codes?.length ? ` · ${esc(row.duplicate_hint.reason_codes.join('、'))}` : ''}</span>` : ''}${row.issue_codes.length ? ` 行问题：${esc(row.issue_codes.join('、'))}` : ''}</p>` : ''}</article>`;
      }).join("") || "<p>当前筛选无行。</p>";
      host.querySelectorAll("[data-batch-row]").forEach(node => {
        const row = next.items.find(item => identity(item) === node.dataset.batchRow);
        const save = () => {
          const previous = context.selected.get(identity(row));
          if (node.querySelector("[data-row-select]").checked) {
            if (!previous && context.selected.size >= 20000) { node.querySelector("[data-row-select]").checked = false; status("一次选择最多20000行；请缩小范围。"); return; }
            const prior = previous?.choice || row.choice || {};
            const decision = node.querySelector('[data-row-decision]').value;
            const intent = decision === 'SKIP' ? {resolution:'AUTO', target:null, acknowledge_new_risk:false} : {};
            context.selected.set(identity(row), { row, refLabel: previous?.refLabel, targetLabel:decision === 'SKIP' ? null : previous?.targetLabel, choice: { ...prior, ...intent, file_id: row.file_id, source_row_number: row.source_row_number,
              decision: node.querySelector("[data-row-decision]").value, recheck: !!node.querySelector("[data-row-recheck]")?.checked,
              account_ref_id: previous?.choice.account_ref_id ?? row.choice?.account_ref_id ?? null } });
          } else context.selected.delete(identity(row));
          context.dirty = true;
          invalidatePlan();
          update();
        };
        node.querySelectorAll("input,select").forEach(input => { input.onchange = save; });
        const intent = node.querySelector('[data-row-intent]');
        if (intent) intent.onclick = () => {
          if (!live() || context.busy || context.unknown) return;
          const frozen = context.plan.preview_digest, previous = context.selected.get(identity(row));
          openImportChoice({row, choice:importRowChoice(row, previous?.choice),
            targetLabel:previous?.targetLabel, token:context.plan.token, digest:frozen, files:context.plan.files,
            selected:() => context.selected, signal,
            valid:() => live() && !context.busy && !context.unknown && context.plan.preview_digest === frozen && context.plan.status !== 'CONFIRMING',
            apply:(choice, targetLabel) => {
              if (!context.selected.has(identity(row)) && context.selected.size >= 20000) throw new Error('一次选择最多20000行；本次未加入');
              context.selected.set(identity(row), {row, choice, targetLabel,
                refLabel:choice.resolution === 'LINK_EXISTING' ? null : previous?.refLabel});
              context.dirty = true; invalidatePlan(); update(); void readPage(context.page);
            }});
        };
        const account = node.querySelector("[data-row-account]");
        if (account) account.onclick = () => {
          node.querySelector("[data-row-select]").checked = true;
          save();
          if (!context.selected.has(identity(row))) return;
          const dialog = workbenchDialog("选择来源卡（不修改Fact原身份）", '<div class="actions"><button type="button" data-ref-auto>使用可靠来源自动匹配</button><button type="button" data-ref-zero>明确待绑定0</button></div><div data-ref-picker></div>');
          const choose = (id, ref) => {
            if (!context.selected.has(identity(row))) { dialog.close(); return; }
            context.selected.get(identity(row)).choice.account_ref_id = id;
            node.querySelector("[data-row-ref]").textContent = id == null ? "来源卡：自动" : ref ? `来源卡：${metadataLabel(ref)}` : "来源卡：明确待绑定";
            context.selected.get(identity(row)).refLabel = ref ? metadataLabel(ref) : null;
            context.dirty = true;
            invalidatePlan();
            dialog.close();
            update();
          };
          dialog.querySelector("[data-ref-auto]").onclick = () => choose(null);
          dialog.querySelector("[data-ref-zero]").onclick = () => choose(0);
          mountPicker(dialog.querySelector("[data-ref-picker]"), { url: "/paam/ledger/v1/account-ref", searchKeys: ["display_label"], describe: metadataLabel, choose: ref => choose(ref.id, ref), signal });
        };
      });
    } catch (error) { if (live() && error.name !== "AbortError") status(`${error.code || "读取失败"}：${error.message}；刷新当前预览后重新选择。`); }
    finally { context.busy = false; update(); }
  };
  const retainRemaining = () => {
    if (!context.selected.size) {localStorage.removeItem(remainingKey);return;}
    // Safe user intent only, not raw source text, financial results or an
    // executable queue. Reload never automatically submits these choices.
    const choices=[...context.selected.values()].map(item=>({file_id:item.row.file_id,source_row_number:item.row.source_row_number,
      decision:item.choice.decision,resolution:item.choice.resolution || 'AUTO',recheck:!!item.choice.recheck,
      acknowledge_new_risk:!!item.choice.acknowledge_new_risk,
      ...(item.choice.account_ref_id != null ? {account_ref_id:item.choice.account_ref_id} : {}),
      ...(item.choice.target ? {target:{...item.choice.target}} : {})}));
    const encoded=JSON.stringify({token:context.plan.token,files:context.plan.files.map(file=>({file_id:file.file_id,sha256:file.sha256})),choices});
    if (new TextEncoder().encode(encoded).byteLength > 8 * 1024 * 1024) throw new Error('完整剩余定位超过浏览器安全存储预算；未发送下一批');
    localStorage.setItem(remainingKey,encoded);
  };
  const finishObserved = async retained => {
    if (retained?.serial) {
      retained.rows.forEach(row=>context.selected.delete(identity(row)));
      context.dirty=!!context.restoreRequired && !!context.selected.size;
      context.revalidationRequired=!!context.selected.size;
      const saved=JSON.parse(localStorage.getItem(remainingKey) || 'null'), handled=new Set(retained.rows.map(identity));
      if (saved?.choices) {
        saved.choices=saved.choices.filter(choice=>!handled.has(identity(choice)));
        if (saved.choices.length) localStorage.setItem(remainingKey,JSON.stringify(saved));else localStorage.removeItem(remainingKey);
      } else retainRemaining();
      context.executionProgress=null;
      find('[data-batch-execution]').textContent=`本批 ${retained.rows.length} 行的当前持久状态已由你核对；尚待处理 ${context.selected.size} 行。不是首次命令回执，未重发请求；剩余须重新核验并批准。`;
    } else {context.selected.clear();context.dirty=false;}
    localStorage.removeItem(pendingKey);context.unknown=false;context.verificationReady=false;context.verificationMessage=null;
    invalidatePlan();await refresh();
  };
  const refresh = async () => {
    if (context.busy) return;
    // Refresh is one read operation: freeze edits before the metadata GET,
    // not only after it resolves and starts replacing the visible row page.
    context.busy = true;
    update();
    let loaded = false;
    try {
      const plan = await request(`/paam/import/v1/preview/${context.plan.token}`, { signal });
      if (!live()) return;
      if (plan.preview_digest !== context.plan.preview_digest) { context.revalidationRequired = !!context.selected.size; invalidatePlan(); }
      context.plan = plan;
      changed(plan);
      loaded = true;
    } catch (error) {
      if (live() && error.name !== 'AbortError') status(`${error.code || "读取失败"}：${error.message}；如预览已丢失，重新上传原文件以继续未持久行。`);
    } finally { context.busy = false; update(); }
    // No await between releasing the metadata read and acquiring the page
    // read; an edit cannot enter between these two halves of the refresh.
    if (loaded && live()) await readPage(1);
  };
  const verify = async () => {
    if (context.busy) return;
    context.verificationReady = false;
    context.verificationMessage = null;
    const retained = pending();
    const rows = retained?.rows || [...context.selected.values()].map(item => ({ file_id: item.row.file_id, source_row_number: item.row.source_row_number }));
    if (!rows.length) { status("没有可核对的所选行定位；按文件sha256查询导入历史，不能凭无响应推断失败。"); return; }
    context.busy = true;
    update();
    const observed = [];
    try {
      // A prior SKIPPED/INVALID row is already terminal before a recheck.
      // Do not let that old state unlock writes while the original POST owns
      // the preview. A resident claimed token cannot be evicted by the server.
      let confirming = false;
      if (retained?.intents) {
        const input = importReconciliationInput(retained);
        const result = await readImportBinding(async readSignal => {
          const checkInFlight = async () => {
            if (!retained.token) return;
            try {
              const current = await request(`/paam/import/v1/preview/${retained.token}`, {signal:readSignal});
              confirming ||= current.status === 'CONFIRMING';
            } catch (error) {if (error.status !== 410) throw error;}
          };
          await checkInFlight();
          const value = await request('/paam/import/v1/import_file/reconcile',{method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify(input),signal:readSignal});
          validateImportReconciliation(value,input);
          // A terminal old source row is not proof that a still-running recheck
          // has finished. Recheck the preview lease after the same-snapshot read.
          await checkInFlight();
          return value;
        },{signal,valid:live,label:'当前持久状态核对'});
        if (!live()) return;
        const panel=find('[data-batch-verification]');
        panel.innerHTML=`<h3>当前持久状态（不是首次命令回执）</h3><p>核对时点 ${esc(result.observed_at)}；共 ${result.items.length} 行，${result.outputs.length} 条当前／历史现金输出。后续解释变化不等于本次失败；不会重发原请求、恢复解释或补造默认。</p><div data-reconcile-rows></div><details><summary>完整当前及历史输出（不截断）</summary><div data-reconcile-outputs></div></details>${result.fully_observed ? '<button type="button" data-batch-observed>我已核对这些行，开始新的明确批次</button>' : '<p>范围尚未完整核实；保留结果未知与原定位信息。</p>'}`;
        mountImportDraftRows(panel.querySelector('[data-reconcile-rows]'),result.items,reconciliationRowLabel,signal);
        mountImportDraftRows(panel.querySelector('[data-reconcile-outputs]'),result.outputs,reconciliationOutputLabel,signal);
        const button=find('[data-batch-observed]');
        context.verificationReady=!!button && !confirming;
        if (button) button.onclick=async()=>{
          if (context.busy || !context.verificationReady || context.plan.status === 'CONFIRMING') return;
          await finishObserved(retained);
        };
        return;
      }
      if (retained?.token) {
        try {
          const current = await request(`/paam/import/v1/preview/${retained.token}`, { signal });
          confirming = current.status === "CONFIRMING";
        } catch (error) {
          if (error.status !== 410) throw error;
        }
      }
      // Sequential bounded GETs, exact selected row numbers, no POST retry.
      const byFile = new Map();
      for (const row of rows) {
        if (!byFile.has(row.file_id)) byFile.set(row.file_id, []);
        byFile.get(row.file_id).push(row);
      }
      const relations = [];
      for (const [fileId, selectedRows] of byFile) {
        const numbers = selectedRows.map(row => row.source_row_number);
        const found = new Map();
        const params = new URLSearchParams({ filter: JSON.stringify({ key: "source_row_number", op: "between", val: { start: Math.min(...numbers), end: Math.max(...numbers) + 1 } }), page_size: "100" });
        for (let pageIndex = 1; ; pageIndex++) {
          params.set("page_index", String(pageIndex));
          const result = await request(`/paam/import/v1/import_file/${fileId}/row/list?${params}`, { signal });
          result.items.forEach(item => { if (numbers.includes(item.source_row_number)) found.set(item.source_row_number, item); });
          if (found.size === selectedRows.length || pageIndex * result.page_size >= result.total) break;
        }
        selectedRows.forEach(row => observed.push({ ...row, actual: found.get(row.source_row_number) }));
        const ids = [...found.values()].map(row => row.id);
        for (let offset = 0; offset < ids.length; offset += 100) {
          const relationParams = new URLSearchParams({ row_ids: JSON.stringify(ids.slice(offset, offset + 100)) });
          const result = await request(`/paam/import/v1/import_file/${fileId}/row/relations?${relationParams}`, { signal });
          relations.push(...result.items);
        }
      }
      if (!live()) return;
      find("[data-batch-verification]").innerHTML = `<h3>当前持久状态（不是首次命令回执）</h3>${observed.map(row => `<p>文件 #${row.file_id} 第 ${row.source_row_number} 行：${row.actual ? `${statusNames[row.actual.row_status]} · Fact #${row.actual.transaction_id}` : "尚无持久结果；不证明请求未提交"}</p>`).join("")}<p>金融效果按当前Review状态核对；重复证据不是额外现金。</p>${relations.filter(row => row.review_id).map(row => `<p>第 ${row.source_row_number} 行 · Review #${row.review_id} ${esc(row.review_status)} · Ledger #${row.ledger_id} · 来源卡 #${row.account_ref_id}</p>`).join("")}${observed.every(row => row.actual && [1, 2, 3].includes(row.actual.row_status)) ? '<button type="button" data-batch-observed>我已核对这些行，开始新的明确批次</button>' : ""}`;
      const observedButton = find("[data-batch-observed]");
      context.verificationReady = !!observedButton && !confirming;
      if (observedButton && confirming) {
        observedButton.disabled = true;
        status("原请求仍在执行；这些可能是重查前的旧状态。保持结果未知，稍后重新核对。");
      }
      if (observedButton) observedButton.onclick = async () => {
        if (context.busy || !context.verificationReady || context.plan.status === "CONFIRMING") return;
        await finishObserved(retained);
      };
    } catch (error) { context.verificationMessage=`无法核实：${error.message}。保持结果未知，不自动重发。`;status(context.verificationMessage); }
    finally { context.busy = false; update(); }
  };
  const selectScope = async (range = false) => {
    if (context.busy || context.unknown || context.plan.status === 'CONFIRMING') return;
    const digest = context.plan.preview_digest, issued = ++context.generation;
    const params = new URLSearchParams({preview_digest:digest});
    try {const filter = filtersFor(range);if (filter) params.set('filter',JSON.stringify(filter));}
    catch (error) {status(error.message);return;}
    let completed = false;
    scopeController = new AbortController();
    const abortScope = () => scopeController?.abort();
    signal.addEventListener('abort', abortScope, {once:true});
    context.scopeReading = true; context.busy = true; update();
    try {
      const rows = await completeImportScope((query, options) => request(`/paam/import/v1/preview/${context.plan.token}/row/list?${query}`, options), params,
        {signal:scopeController.signal, valid:() => live() && issued === context.generation && context.plan.preview_digest === digest});
      const selected = new Map(context.selected);
      for (const row of rows) {
        if (row.classification !== 'PROCESSED' && !selected.has(identity(row))) selected.set(identity(row),
          {row, choice:{...importRowChoice(row),file_id:row.file_id,source_row_number:row.source_row_number}});
      }
      if (selected.size > 20000) throw new Error('合并选择超过20000行；未部分加入，请缩小范围。');
      if (!live() || issued !== context.generation || context.plan.preview_digest !== digest) return;
      context.selected = selected; context.dirty = !!selected.size; invalidatePlan();
      completed = true;
      status(`${range ? '指定文件区间' : '当前筛选'}完整读取 ${rows.length} 行；已接受行不加入，新选择总计 ${selected.size} 行。未自动确认风险或写入账务。`);
    } catch (error) { if (live()) status(error.name === 'AbortError' ? '范围读取已停止；原选择保留，没有部分加入。'
      : `${error.code || '完整范围读取失败'}：${error.message}；原选择保留。`); }
    finally {
      signal.removeEventListener('abort', abortScope);
      scopeController = null; context.scopeReading = false; context.busy = false; update();
    }
    if (live()) await readPage(context.page);
    return completed && live();
  };
  find('[data-batch-select-scope]').onclick = () => selectScope();
  find('[data-batch-select-range]').onclick = () => selectScope(true);
  find('[data-batch-bulk]').onclick = () => {
    if (!live() || context.busy || context.unknown || !context.selected.size || context.plan.status === 'CONFIRMING') return;
    const frozen = {digest:context.plan.preview_digest,time:context.plan.updated_time,generation:context.generation,selected:context.selected};
    openImportBulk({selected:context.selected,files:context.plan.files,signal,
      valid:() => live() && !context.busy && !context.unknown && context.plan.preview_digest === frozen.digest
        && context.plan.updated_time === frozen.time && context.generation === frozen.generation && context.selected === frozen.selected && context.plan.status !== 'CONFIRMING',
      apply:result => {
        context.selected = new Map(context.selected);
        for (const [key,item] of result.updates) context.selected.set(key,item);
        context.dirty = true;invalidatePlan();update();
        status(`已修改 ${result.updates.size} 行草稿；${result.exceptions.length} 行例外保留原决定和选择。尚未保存或入账。`);
        void readPage(context.page);
      }});
  };
  find('[data-batch-bind]').onclick = () => {
    if (!live() || context.busy || context.unknown || !context.selected.size || context.plan.status === 'CONFIRMING') return;
    const frozen = {digest:context.plan.preview_digest,time:context.plan.updated_time,generation:context.generation,selected:context.selected};
    openImportBinding({selected:context.selected,files:context.plan.files,token:context.plan.token,
      digest:frozen.digest,time:frozen.time,signal,
      valid:() => live() && !context.busy && !context.unknown && context.plan.preview_digest === frozen.digest
        && context.plan.updated_time === frozen.time && context.generation === frozen.generation && context.selected === frozen.selected && context.plan.status !== 'CONFIRMING',
      apply:(selected,{modified,exceptions}) => {
        context.selected = selected;
        context.dirty = true;invalidatePlan();update();
        status(`已应用 ${modified} 行来源草稿；${exceptions} 行例外保留原选择和决定。尚未保存或入账。`);
        void readPage(context.page);
      }});
  };
  find('[data-batch-pair]').onclick = () => {
    if (!live() || context.busy || context.unknown || !context.selected.size || context.plan.status === 'CONFIRMING') return;
    const frozen = {digest:context.plan.preview_digest,time:context.plan.updated_time,generation:context.generation,selected:context.selected};
    openImportPairing({selected:context.selected,current:()=>context.selected,files:context.plan.files,token:context.plan.token,
      digest:frozen.digest,time:frozen.time,signal,
      valid:()=>live() && !context.busy && !context.unknown && context.plan.preview_digest === frozen.digest
        && context.plan.updated_time === frozen.time && context.generation === frozen.generation && context.selected === frozen.selected && context.plan.status !== 'CONFIRMING',
      apply:(selected,{modified,unchanged})=>{
        context.selected=selected;context.dirty=true;invalidatePlan();update();
        status(`已应用 ${modified} 行具名配对／人工草稿；其余 ${unchanged} 行保留原选择和决定。尚未保存或入账。`);
        void readPage(context.page);
      }});
  };
  if (find('[data-batch-stop-scope]')) find('[data-batch-stop-scope]').onclick = () => scopeController?.abort();
  const describeRisk = item => `文件 #${item.row.file_id} 第 ${item.row.source_row_number} 行：${importRiskLabel(item.row)}；${item.choice.decision === 'SKIP' ? '已跳过' : importRiskUnresolved(item.row,item.choice) ? '接受但未解决' : '已明确意图，仍须计划核验'}`;
  const closeRiskOnLeave = dialog => {
    const close=()=>{if (dialog.open) dialog.close();};
    signal.addEventListener('abort',close,{once:true});
    dialog.addEventListener('close',()=>signal.removeEventListener('abort',close),{once:true});
  };
  find('[data-batch-risk-list]').onclick = () => {
    if (!live() || context.busy || context.unknown) return;
    const rows = riskRows();
    const dialog = workbenchDialog('所选完整范围的拟新增风险', '<p>疑似不等于已证明重复；候选仅覆盖明确核验范围。按下列文件／原始行号，在导入行的“核对意图／重复”中处理。</p><div data-risk-rows></div>');
    closeRiskOnLeave(dialog);
    mountImportDraftRows(dialog.querySelector('[data-risk-rows]'),rows,describeRisk,signal);
  };
  find('[data-batch-risk-skip]').onclick = () => {
    if (!live() || context.busy || context.unknown || context.plan.status === 'CONFIRMING') return;
    const rows = riskRows().filter(item => importRiskUnresolved(item.row,item.choice));
    if (!rows.length) return;
    const frozen = {selected:context.selected,digest:context.plan.preview_digest,generation:context.generation,
      choices:rows.map(item=>JSON.stringify(item.choice))};
    const dialog = workbenchDialog('明确跳过所选未解决风险行', `<p>仅将下列 ${rows.length} 行设为跳过，保留原证据；其余选择及已明确的配对／新增意图不改变。本操作只改草稿，仍须核验后确认。</p><div data-risk-rows></div><label><input type="checkbox" data-risk-skip-ack>我已核对上述完整范围，明确将这些行设为跳过</label><p role="status" data-risk-status></p><button type="button" data-risk-skip-apply>应用跳过草稿</button>`);
    closeRiskOnLeave(dialog);
    mountImportDraftRows(dialog.querySelector('[data-risk-rows]'),rows,describeRisk,signal);
    dialog.querySelector('[data-risk-skip-apply]').onclick = () => {
      if (!dialog.open || !live() || context.busy || context.unknown || context.plan.status === 'CONFIRMING' || context.selected !== frozen.selected || context.plan.preview_digest !== frozen.digest || context.generation !== frozen.generation || rows.some((item,index)=>context.selected.get(identity(item.row)) !== item || JSON.stringify(item.choice) !== frozen.choices[index])) {
        dialog.querySelector('[data-risk-status]').textContent='选择或预览已变化，请关闭后重新核对';return;
      }
      if (!dialog.querySelector('[data-risk-skip-ack]').checked) {dialog.querySelector('[data-risk-status]').textContent='请先明确确认跳过范围';return;}
      context.selected=new Map(context.selected);
      for (const item of rows) context.selected.set(identity(item.row),{...item,targetLabel:null,
        choice:{...item.choice,decision:'SKIP',resolution:'AUTO',target:null,acknowledge_new_risk:false}});
      context.dirty=true;invalidatePlan();dialog.close();update();
      status(`已将 ${rows.length} 行未解决风险设为跳过草稿；其余决定保留，尚未保存或入账。`);
      void readPage(context.page);
    };
  };
  // One authoritative read-only preflight for every UI size and intent. Saving
  // draft choices is not proof that new-cash risk or other premises are valid.
  const readPlan = async () => {
    const frozen = {token:context.plan.token,time:context.plan.updated_time,digest:context.plan.preview_digest};
    const selected = [...context.selected.values()].map(item => ({file_id:item.row.file_id,source_row_number:item.row.source_row_number}));
    invalidatePlan();update();
    try {
      const result = await request(`/paam/import/v1/preview/${frozen.token}/operation-preview`, {method:'POST', signal,
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({expected_updated_time:frozen.time,preview_digest:frozen.digest,selected_rows:selected})});
      if (!live() || context.plan.updated_time !== frozen.time || context.plan.preview_digest !== frozen.digest) return;
      if (result.source_preview_digest !== frozen.digest) throw new Error('处理计划来源摘要不一致；请重新核对。');
      context.disclosure = validateImportPlan(result,selected);
      for (const batch of context.disclosure.batches) for (const pair of batch.preview.pairs || []) {
        const item=context.selected.get(identity(pair.row));
        if (item) item.row={...item.row,duplicate_hint:pair.duplicate_hint};
      }
      mountImportPlan(find('[data-batch-operation]'),context.disclosure,{signal});
      const issues=context.disclosure.batches.flatMap(batch=>batch.preview.issues || []);
      status(result.can_confirm ? '选择已保存，完整只读核验通过；请核对计划后明确确认入账。本次未发送金融确认。'
        : `选择已保存；完整计划未解决 ${issues.length} 行、预算阻断 ${result.blocked.length} 组。${issues.some(row=>row.code === 'IMPORT_REVIEW_REQUIRED') ? '新增现金风险须核对候选、明确跳过或确认另一笔真实交易。' : '请按完整计划的文件和行号处理。'}本次未发送金融确认，不会自动跳过。`);
    } catch (error) {
      if (live()) {invalidatePlan();context.preflightFailure=error.code || 'PLAN_READ_FAILED';
        if (error.name !== 'AbortError') status(`选择已保存，尚未取得可提交计划：${error.code || '处理计划读取失败'}。${importIssueMessage(error.code)}；重新核验，不必重新选择。本次没有发送金融确认。`);}
    }
  };
  const planButton = find('[data-batch-plan]');
  if (planButton) planButton.onclick = async () => {
    if (context.busy || context.unknown || context.dirty || context.revalidationRequired || !context.selected.size || context.plan.status === 'CONFIRMING') return;
    context.busy = true; update();
    try {await readPlan();}
    finally { context.busy = false; update(); }
  };
  find("[data-batch-save]").onclick = async () => {
    if (!live() || context.busy || context.unknown || context.restoreRequired || context.plan.status === 'CONFIRMING' || !context.selected.size) return;
    context.busy = true;
    update();
    try {
      invalidatePlan();
      const choices = [...context.selected.values()].map(item => item.choice);
      // PUT stores choices only, not accounting effects. Keep each request
      // <=1000 and carry the exact returned time; a failure retains all choices
      // as dirty and never automatically retries an already-sent request.
      for (let offset = 0; offset < choices.length; offset += 1000) {
        if (!live()) return;
        const plan = await jsonRequest(`/paam/import/v1/preview/${context.plan.token}`, "PUT", {
          expected_updated_time: context.plan.updated_time, choices: choices.slice(offset, offset + 1000) });
        if (!live()) return;
        context.plan = plan;
        changed(plan);
      }
      context.dirty = false;
      context.revalidationRequired = false;
      await readPlan();
    } catch (error) { context.dirty = true; status(`${error.code || "保存失败"}：${error.message}。读取最新预览后重新选择。`); }
    finally { context.busy = false; update(); }
  };
  // One explicitly requested read/save/preflight chain. Never acknowledge new
  // cash risk or send a financial POST here; a failed range cannot save a prefix.
  find('[data-batch-guide]').onclick = async () => {
    if (!live() || context.busy || context.guiding || context.unknown || context.restoreRequired || context.plan.status === 'CONFIRMING') return;
    context.guiding = true;update();
    try {
      if (await selectScope() && context.selected.size) await find('[data-batch-save]').onclick();
    } finally {context.guiding = false;update();}
  };
  find("[data-batch-confirm]").onclick = async () => {
    if (!live() || context.busy || context.unknown || context.dirty || context.revalidationRequired || context.restoreRequired || context.plan.status === 'CONFIRMING' || !context.selected.size || context.selected.size > 1000 || !singleBatch()) return;
    context.verificationReady = false;
    const selected = [...context.selected.values()].map(item => ({ file_id: item.row.file_id, source_row_number: item.row.source_row_number }));
    context.busy = true;
    update();
    // Persist only safe current locating context before a financial POST.
    try {
      localStorage.setItem(pendingKey, JSON.stringify({ token: context.plan.token, files: context.plan.files.map(file => ({ file_id: file.file_id, sha256: file.sha256 })), rows: selected,
        intents:[...context.selected.values()].map(item => ({file_id:item.row.file_id,source_row_number:item.row.source_row_number,
          resolution:item.choice.resolution || 'AUTO',decision:item.choice.decision,target:item.choice.target ? {...item.choice.target} : null})) }));
    } catch {
      context.busy = false;
      status("无法保存本批安全定位信息；本次未发送确认。请检查浏览器存储设置。");
      update();
      return;
    }
    try {
      const result = await jsonRequest(`/paam/import/v1/preview/${context.plan.token}/confirm`, "POST", {
        expected_updated_time: context.plan.updated_time, preview_digest: context.plan.preview_digest, selected_rows: selected,
        batch_preview_digest:singleBatch().batch_preview_digest });
      localStorage.removeItem(pendingKey);
      context.selected.clear();
      context.dirty = false;
      status(`本批完成：新事实 ${result.new_fact_count} · 补证据 ${result.linked_existing_count} · 跳过 ${result.skipped_count} · 问题 ${result.invalid_count} · 剩余 ${result.remaining_count}`);
      context.busy = false;
      await refresh();
    } catch (error) {
      context.unknown = isUnknownWrite(error);
      invalidatePlan();context.preflightFailure=error.code || 'CONFIRM_FAILED';
      if (!context.unknown) localStorage.removeItem(pendingKey);
      status(context.unknown ? "提交结果未知。先核对所选行当前持久状态；禁止自动重发。"
        : `${error.code || '提交失败'}：${importIssueMessage(error.code)}。选择已保存并保留，请重新核验；不会自动重发。`);
    } finally { context.busy = false; update(); }
  };
  find('[data-batch-consent]').onchange=update;
  find('[data-batch-stop-execution]').onclick=async()=>{if (context.executor) {await context.executor.stop();update();}};
  find('[data-batch-restore]').onclick=async()=>{
    if (context.busy || context.unknown || !context.restoreRequired) return;
    context.busy=true;update();
    try {
      const latest=await request(`/paam/import/v1/preview/${context.plan.token}`,{signal});
      const rows=await completeImportScope((params,options)=>request(`/paam/import/v1/preview/${context.plan.token}/row/list?${params}`,options),
        {preview_digest:latest.preview_digest},{signal,valid:live});
      const found=new Map(rows.map(row=>[identity(row),row])), selected=new Map();
      for (const [key,item] of context.selected) {
        const row=found.get(key);
        if (!row || row.classification === 'PROCESSED') throw new Error('剩余范围含已接受或缺失行；请按持久状态核对，未自动剔除或继续');
        selected.set(key,{...item,row});
      }
      if (!live()) return;
      context.selected=selected;context.plan=latest;context.dirty=!!selected.size;context.restoreRequired=false;
      context.recoveryError=null;invalidatePlan();changed(latest);status('完整剩余范围已重新读取；须保存选择、重新查看完整计划并再次明确批准。');
    } catch (error) {status(`剩余范围读取失败：${error.message}；保留原定位，不自动继续。`);}
    finally {context.busy=false;update();}
    if (live()) await readPage(1);
  };
  find('[data-batch-execute]').onclick=async()=>{
    if (!live() || context.busy || context.unknown || context.dirty || context.revalidationRequired || context.restoreRequired || !context.selected.size
        || !context.disclosure?.can_confirm || !find('[data-batch-consent]').checked || context.plan.status === 'CONFIRMING') return;
    const token=context.plan.token;
    try {
      retainRemaining();
      const executor=createImportExecution({plan:context.disclosure,current:context.plan,valid:live,unknown:isUnknownWrite,
        approve:body=>jsonRequest(`/paam/import/v1/preview/${token}/operation-approve`,'POST',body),
        confirm:body=>jsonRequest(`/paam/import/v1/preview/${token}/operation-confirm`,'POST',body),
        stop:body=>jsonRequest(`/paam/import/v1/preview/${token}/operation-stop`,'POST',body),
        prepare:rows=>{
          retainRemaining();
          const intents=rows.map(row=>{const choice=context.selected.get(identity(row))?.choice;if (!choice) throw new Error('冻结批次选择已丢失');
            return {...row,resolution:choice.resolution || 'AUTO',decision:choice.decision,target:choice.target ? {...choice.target} : null};});
          const retained={serial:true,token,rows,files:context.plan.files.map(file=>({file_id:file.file_id,sha256:file.sha256})),intents};
          importReconciliationInput(retained);
          localStorage.setItem(pendingKey,JSON.stringify(retained));
          context.verificationReady=false;
        },
        committed:(result,rows)=>{
          rows.forEach(row=>context.selected.delete(identity(row)));
          context.plan={...context.plan,preview_digest:result.preview_digest,updated_time:result.preview_updated_time,status:'READY'};
          retainRemaining();localStorage.removeItem(pendingKey);
        },
        failed:(error,state)=>{
          context.unknown=state.unknown || (state.stage === 'LOCAL_RESULT' && !!pending());context.revalidationRequired=!!context.selected.size;
          if (!context.unknown) localStorage.removeItem(pendingKey);
          context.verificationMessage=context.unknown ? '本批提交结果尚待核对；后续已停止，保留本批定位与未提交范围，不自动重发。' : null;
          status(context.unknown ? context.verificationMessage : importKnownFailureMessage(error));
        },
        progress:state=>{
          context.executionProgress=state;
          if (live()) find('[data-batch-execution]').textContent=`${state.phase === 'COMPLETE' ? '全部完成' : state.phase === 'UNKNOWN' ? '本批结果未知' : state.stop_requested ? '后续已停止' : '依次执行'}：已知完成 ${state.completed_batches}/${state.total_batches} 批、${state.completed_rows} 行；未获完成确认 ${state.remaining_rows} 行${state.in_flight ? '；本批正在提交，停止不会撤销本批。' : ''}${state.stop_error ? '；服务器停止确认失败，本地不会发送后续批。' : ''}`;
          update();
        }});
      context.executor=executor;activeExecution=executor;context.busy=true;update();
      const result=await executor.run();
      if (!context.unknown) {localStorage.removeItem(pendingKey);context.revalidationRequired=!!context.selected.size;}
      invalidatePlan();
      if (result.phase === 'COMPLETE') status('完整计划全部完成；各批已独立提交，没有后台继续任务。');
    } catch (error) {context.revalidationRequired=!!context.selected.size;status(`未继续执行：${error.message}；保留范围，须重新核验。`);}
    finally {
      if (activeExecution === context.executor) activeExecution=null;
      context.executor=null;context.busy=false;update();
    }
    if (live() && !context.unknown) await refresh();
  };
  find("[data-batch-clear]").onclick = () => { if (context.busy || context.unknown) return;context.selected.clear(); context.dirty = false; context.revalidationRequired=false;context.restoreRequired=false;localStorage.removeItem(remainingKey);invalidatePlan(); readPage(context.page); };
  find("[data-batch-select-page]").onclick = () => { host.querySelectorAll("[data-row-select]:not([data-batch-processed])").forEach(input => { if (!input.checked) { input.checked = true; input.dispatchEvent(new Event("change")); } }); };
  find("[data-batch-cancel]").onclick = async () => {
    if (context.busy || context.unknown) return;
    context.busy = true;
    update();
    try { await request(`/paam/import/v1/preview/${context.plan.token}`, { method: "DELETE" }); changed(null); host.textContent = "预览已取消；持久文件和来源证据未删除。"; }
    catch (error) { status(error.message); context.busy = false; update(); }
  };
  find("[data-batch-prev]").onclick = () => readPage(context.page - 1);
  find("[data-batch-next]").onclick = () => readPage(context.page + 1);
  find("[data-batch-refresh]").onclick = refresh;
  find("[data-batch-verify]").onclick = verify;
  find('[data-batch-page-size]').onchange = () => {
    const size = Number(find('[data-batch-page-size]').value);
    if (context.busy || ![20,50,100].includes(size)) return;
    context.pageSize = size;void readPage(1);
  };
  host.querySelectorAll("[data-batch-file],[data-batch-classification]").forEach(input => { input.onchange = () => readPage(1); });
  await readPage(context.page);
}

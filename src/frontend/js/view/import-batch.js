import { request, jsonRequest, isUnknownWrite } from "../api/client.js";
import { esc, money, date } from "../util/core.js";
import { mountPicker, workbenchDialog } from "../component/workbench.js";

const pendingKey = "paam.import.pending.v1";
const contexts = new Map();
let controller;
export function stopImportRead() { controller?.abort(); }
const identity = row => `${row.file_id}:${row.source_row_number}`;
const classifications = { NEW: "新事实", EXISTING: "仅补证据", PROCESSED: "已接受（只读）", INVALID: "需要核对", AMBIGUOUS: "身份不唯一" };
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
  let page;
  host.innerHTML = `<div class="preview-section"><h2 id="preview-title">显式分批导入</h2><p>每批仅处理明确选择的 1–1000 行；当前页最多 20 行，不代表整份账单。首次新事实建立默认解释，补来源证据不改变原审查。</p><div data-batch-files></div><p data-batch-summary></p><div class="actions"><label>文件<select data-batch-file><option value="">全部文件</option>${initial.files.map(file => `<option value="${file.file_id}">${esc(file.filename)} · #${file.file_id}</option>`).join("")}</select></label><label>分类<select data-batch-classification><option value="">全部分类</option>${Object.entries(classifications).map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label><button type="button" data-batch-refresh>读取当前预览</button></div><p role="status" data-batch-status></p><div data-batch-rows></div><div class="actions"><button type="button" data-batch-prev>上一页</button><span data-batch-page></span><button type="button" data-batch-next>下一页</button></div><p data-batch-selection></p><div class="actions"><button type="button" data-batch-select-page>选择本页未接受行</button><button type="button" data-batch-clear>清空本批选择</button><button type="button" data-batch-save>保存选择并重新核验</button><button type="button" class="primary" data-batch-confirm>确认并写入本批</button><button type="button" data-batch-verify>核对所选行当前持久状态</button><button type="button" data-batch-cancel>取消驻留预览</button></div><div data-batch-verification></div></div>`;
  const find = selector => host.querySelector(selector);
  const status = message => { if (host.isConnected) find("[data-batch-status]").textContent = message; };
  const live = () => host.isConnected && !signal.aborted;
  const update = () => {
    if (!live()) return;
    const plan = context.plan;
    find("[data-batch-files]").innerHTML = plan.files.map(file => `<article class="preview-file-card"><strong>${esc(file.filename)} · #${file.file_id}</strong><p>已接受 ${file.accepted} · 跳过 ${file.skipped} · 问题 ${file.invalid} · 剩余 ${file.remaining}</p><small>解析 ${file.parsed_row_count} 行；活动范围 ${esc(file.activity_range.start || "未知")} 至 ${esc(file.activity_range.end || "未知")}（不是完整期间覆盖）</small></article>`).join("");
    find("[data-batch-summary]").textContent = `新事实 ${plan.counts.new} · 仅补证据 ${plan.counts.existing} · 已接受 ${plan.counts.processed} · 问题 ${plan.issue_count}${plan.has_more_issues ? "（问题提示超过100条，请分页核对）" : ""}${plan.timed_out ? "；预览超时提示，确认仍会重新核验" : ""}`;
    find("[data-batch-selection]").textContent = `本批明确选择 ${context.selected.size} 行${context.dirty ? "；选择已修改，须先保存核验" : "；使用服务器最新摘要"}`;
    host.querySelectorAll("button,input,select").forEach(node => { node.disabled = context.busy || context.unknown; });
    find("[data-batch-verify]").disabled = context.busy;
    find("[data-batch-refresh]").disabled = context.busy;
    if (find("[data-batch-observed]")) find("[data-batch-observed]").disabled = context.busy || !context.verificationReady || plan.status === "CONFIRMING";
    find("[data-batch-confirm]").disabled = context.busy || context.unknown || context.dirty || !context.selected.size || plan.status === "CONFIRMING";
    find("[data-batch-prev]").disabled ||= context.page <= 1;
    find("[data-batch-next]").disabled ||= !page || context.page * page.page_size >= page.total;
    host.querySelectorAll("[data-batch-processed]").forEach(node => { node.disabled = true; });
    if (context.unknown) status("提交结果未知。不要重发；先核对当前行状态。410/预览丢失不能证明未提交。");
  };
  const readPage = async requestedPage => {
    if (context.busy) return;
    const issued = ++context.generation;
    context.busy = true;
    update();
    const params = new URLSearchParams({ page_index: requestedPage, page_size: "20", preview_digest: context.plan.preview_digest });
    const filters = [];
    if (find("[data-batch-file]").value) filters.push({ key: "file_id", op: "=", val: Number(find("[data-batch-file]").value) });
    if (find("[data-batch-classification]").value) filters.push({ key: "classification", op: "=", val: find("[data-batch-classification]").value });
    if (filters.length) params.set("filter", JSON.stringify(filters.length === 1 ? filters[0] : { op: "AND", expression: filters }));
    try {
      const next = await request(`/paam/import/v1/preview/${context.plan.token}/row/list?${params}`, { signal });
      if (!live() || issued !== context.generation) return;
      page = next;
      context.page = requestedPage;
      find("[data-batch-page]").textContent = `第 ${requestedPage} 页 · 当前筛选共 ${next.total} 行`;
      find("[data-batch-rows]").innerHTML = next.items.map(row => {
        const selected = context.selected.get(identity(row));
        const choice = selected?.choice || row.choice || { decision: "ACCEPT", recheck: false, account_ref_id: null };
        const processed = row.classification === "PROCESSED";
        return `<article class="review-ledger-row" data-batch-row="${identity(row)}"><label><input type="checkbox" data-row-select ${selected ? "checked" : ""} ${processed ? "data-batch-processed disabled" : ""}>文件 #${row.file_id} 第 ${row.source_row_number} 行</label><strong>${esc(classifications[row.classification])} · ${esc(row.parsed.amount == null ? "金额未知" : money(row.parsed))}</strong><p>${esc(row.parsed.cash_direction || "方向未知")} · ${esc(row.parsed.occurred_time ? date(row.parsed.occurred_time) : "时间未知")} · ${esc(row.parsed.summary)}</p><p>${esc(row.issue_codes.join("、"))}${row.existing_transaction_id ? ` · Fact #${row.existing_transaction_id}` : ""}</p><label>本行决定<select data-row-decision ${processed ? "data-batch-processed disabled" : ""}><option value="ACCEPT" ${choice.decision === "ACCEPT" ? "selected" : ""}>接受</option><option value="SKIP" ${choice.decision === "SKIP" ? "selected" : ""}>跳过（问题行将保留为INVALID）</option></select></label>${[2, 3].includes(row.persisted_row_status) ? `<label><input type="checkbox" data-row-recheck ${choice.recheck ? "checked" : ""}>明确重新检查未接受行（旧状态：${statusNames[row.persisted_row_status]}）</label>` : ""}<span data-row-ref>来源卡：${choice.account_ref_id == null ? "可靠来源自动匹配／否则待绑定" : choice.account_ref_id === 0 ? "明确待绑定" : `#${choice.account_ref_id}`}</span>${row.classification === "NEW" ? '<button type="button" data-row-account>选择来源卡</button>' : ""}</article>`;
      }).join("") || "<p>当前筛选无行。</p>";
      host.querySelectorAll("[data-batch-row]").forEach(node => {
        const row = next.items.find(item => identity(item) === node.dataset.batchRow);
        const save = () => {
          const previous = context.selected.get(identity(row));
          if (node.querySelector("[data-row-select]").checked) {
            if (!previous && context.selected.size >= 1000) { node.querySelector("[data-row-select]").checked = false; status("一批最多1000行；请先完成当前批。"); return; }
            context.selected.set(identity(row), { row, choice: { file_id: row.file_id, source_row_number: row.source_row_number,
              decision: node.querySelector("[data-row-decision]").value, recheck: !!node.querySelector("[data-row-recheck]")?.checked,
              account_ref_id: previous?.choice.account_ref_id ?? row.choice?.account_ref_id ?? null } });
          } else context.selected.delete(identity(row));
          context.dirty = true;
          update();
        };
        node.querySelectorAll("input,select").forEach(input => { input.onchange = save; });
        const account = node.querySelector("[data-row-account]");
        if (account) account.onclick = () => {
          node.querySelector("[data-row-select]").checked = true;
          save();
          if (!context.selected.has(identity(row))) return;
          const dialog = workbenchDialog("选择来源卡（不修改Fact原身份）", '<div class="actions"><button type="button" data-ref-auto>使用可靠来源自动匹配</button><button type="button" data-ref-zero>明确待绑定0</button></div><div data-ref-picker></div>');
          const choose = id => {
            if (!context.selected.has(identity(row))) { dialog.close(); return; }
            context.selected.get(identity(row)).choice.account_ref_id = id;
            node.querySelector("[data-row-ref]").textContent = id == null ? "来源卡：自动" : `来源卡：#${id}`;
            context.dirty = true;
            dialog.close();
            update();
          };
          dialog.querySelector("[data-ref-auto]").onclick = () => choose(null);
          dialog.querySelector("[data-ref-zero]").onclick = () => choose(0);
          mountPicker(dialog.querySelector("[data-ref-picker]"), { url: "/paam/ledger/v1/account-ref", describe: ref => `#${ref.id} ${ref.name} ${ref.reference} ${ref.status}`, choose: ref => choose(ref.id), signal });
        };
      });
    } catch (error) { if (live() && error.name !== "AbortError") status(`${error.code || "读取失败"}：${error.message}；刷新当前预览后重新选择。`); }
    finally { context.busy = false; update(); }
  };
  const refresh = async () => {
    if (context.busy) return;
    try {
      const plan = await request(`/paam/import/v1/preview/${context.plan.token}`, { signal });
      if (!live()) return;
      if (plan.preview_digest !== context.plan.preview_digest) context.dirty = !!context.selected.size;
      context.plan = plan;
      changed(plan);
      await readPage(1);
    } catch (error) { status(`${error.code || "读取失败"}：${error.message}；如预览已丢失，重新上传原文件以继续未持久行。`); }
  };
  const verify = async () => {
    if (context.busy) return;
    context.verificationReady = false;
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
        localStorage.removeItem(pendingKey); context.unknown = false; context.verificationReady = false;
        context.selected.clear(); context.dirty = false; await refresh();
      };
    } catch (error) { status(`无法核实：${error.message}。保持结果未知，不自动重发。`); }
    finally { context.busy = false; update(); }
  };
  find("[data-batch-save]").onclick = async () => {
    if (context.busy || context.unknown || !context.selected.size) return;
    context.busy = true;
    update();
    try {
      const plan = await jsonRequest(`/paam/import/v1/preview/${context.plan.token}`, "PUT", {
        expected_updated_time: context.plan.updated_time, choices: [...context.selected.values()].map(item => item.choice) });
      if (!live()) return;
      context.plan = plan;
      context.dirty = false;
      changed(plan);
      status("本批选择已保存并核验。确认仅处理这些行。");
    } catch (error) { context.dirty = true; status(`${error.code || "保存失败"}：${error.message}。读取最新预览后重新选择。`); }
    finally { context.busy = false; update(); }
  };
  find("[data-batch-confirm]").onclick = async () => {
    if (context.busy || context.unknown || context.dirty || !context.selected.size) return;
    context.verificationReady = false;
    const selected = [...context.selected.values()].map(item => ({ file_id: item.row.file_id, source_row_number: item.row.source_row_number }));
    context.busy = true;
    update();
    // Persist only safe current locating context before a financial POST.
    try {
      localStorage.setItem(pendingKey, JSON.stringify({ token: context.plan.token, files: context.plan.files.map(file => ({ file_id: file.file_id, sha256: file.sha256 })), rows: selected }));
    } catch {
      context.busy = false;
      status("无法保存本批安全定位信息；本次未发送确认。请检查浏览器存储设置。");
      update();
      return;
    }
    try {
      const result = await jsonRequest(`/paam/import/v1/preview/${context.plan.token}/confirm`, "POST", {
        expected_updated_time: context.plan.updated_time, preview_digest: context.plan.preview_digest, selected_rows: selected });
      localStorage.removeItem(pendingKey);
      context.selected.clear();
      context.dirty = false;
      status(`本批完成：新事实 ${result.new_fact_count} · 补证据 ${result.linked_existing_count} · 跳过 ${result.skipped_count} · 问题 ${result.invalid_count} · 剩余 ${result.remaining_count}`);
      context.busy = false;
      await refresh();
    } catch (error) {
      context.unknown = isUnknownWrite(error);
      context.dirty = true;
      if (!context.unknown) localStorage.removeItem(pendingKey);
      status(context.unknown ? "提交结果未知。先核对所选行当前持久状态；禁止自动重发。"
        : error.code === "WRITE_BUSY" ? "本批未提交，选择已保留。稍后读取最新预览、保存核验选择后再确认；不会自动重发。"
        : `${error.code || "提交失败"}：${error.message}；读取最新预览后重新选择。`);
    } finally { context.busy = false; update(); }
  };
  find("[data-batch-clear]").onclick = () => { context.selected.clear(); context.dirty = false; readPage(context.page); };
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
  host.querySelectorAll("[data-batch-file],[data-batch-classification]").forEach(input => { input.onchange = () => readPage(1); });
  await readPage(context.page);
}

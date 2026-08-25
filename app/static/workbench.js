const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" });
const request = async (url, options) => {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || await response.text());
  return response.status === 204 ? null : response.json();
};
const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const now = () => new Date().toISOString().slice(0, 16);

document.head.insertAdjacentHTML("beforeend", '<link rel="stylesheet" href="/static/providers.css">');

document.querySelector(".shell").innerHTML = `
  <header><div class="brand"><img src="/static/personal-assets-ai-manager.svg" alt=""><span>个人账本与资产管家</span></div><p>账单流水是事实源 · 本地优先 · 可审计打标</p></header>
  <section class="cards" id="summary"><article><span>累计收入</span><strong>--</strong></article><article><span>累计支出</span><strong>--</strong></article><article><span>净额</span><strong>--</strong></article><article><span>待复核候选</span><strong>--</strong></article></section>
  <section class="grid">
    <article class="panel" id="import-panel"></article>
    <article class="panel"><h2>手工录入流水</h2><form id="bill-form"><label>交易时间<input name="occurred_at" type="datetime-local" required></label><label>交易方<input name="merchant" required></label><label>金额（收入为正，支出为负）<input name="amount" type="number" step="0.01" required></label><label>备注<input name="note"></label><button>保存流水并生成规则建议</button></form></article>
  </section>
  <section class="panel"><h2>流水工作台</h2><p class="hint">规则建议、LLM 建议、人工确认和授权自动策略均记录为审计事件；数值更高的置信度覆盖当前展示，不删除历史。</p><div class="table-wrap"><table><thead><tr><th>时间</th><th>交易方</th><th>金额</th><th>当前分类 / 标签</th><th>来源</th><th>主动打标</th></tr></thead><tbody id="bills"></tbody></table></div></section>
  <section class="panel"><h2>重复与转移候选</h2><p class="hint">系统只生成候选，必须由你确认或忽略；确认转移后，后续统计可据此排除收支。</p><div class="table-wrap"><table><thead><tr><th>类型</th><th>两笔流水</th><th>依据</th><th>置信度</th><th>状态</th><th>处理</th></tr></thead><tbody id="candidates"></tbody></table></div></section>`;

document.querySelector('input[name="occurred_at"]').value = now();

const importPanel = document.querySelector("#import-panel");
importPanel.innerHTML = `<h2>导入账单</h2>
  <p class="hint">支付宝、微信使用各自的内置账单模板。可一次选择多个 CSV、XLS、XLSX 或 ZIP，也可选择文件夹；ZIP 密码只用于本次解析，不会保存。</p>
  <label>账单文件（可多选）<input id="import-files" type="file" multiple accept=".csv,.xls,.xlsx,.zip,text/csv,application/zip"></label>
  <label>或选择账单文件夹<input id="import-folder" type="file" multiple webkitdirectory></label>
  <label>ZIP 密码（可选，应用于本批 ZIP）<input id="import-password" type="password" autocomplete="off"></label>
  <div class="actions"><button class="provider-import" data-preview-source="alipay" aria-label="预览并导入支付宝账单"><img class="provider-import__icon" src="/static/providers/alipay.svg" alt="" aria-hidden="true"><span>预览支付宝账单</span></button><button class="provider-import" data-preview-source="wechat" aria-label="预览并导入微信账单"><img class="provider-import__icon" src="/static/providers/wechat.svg" alt="" aria-hidden="true"><span>预览微信账单</span></button></div>
  <section id="import-preview" class="import-preview" hidden aria-live="polite"></section><small id="import-result" aria-live="polite"></small>`;

let pendingBatch = null;
const newBatchToken = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function importHeaders() {
  const passwordInput = document.querySelector("#import-password");
  return passwordInput.value ? { "X-Import-Password": passwordInput.value } : {};
}

function clearImportPassword() {
  document.querySelector("#import-password").value = "";
}

function selectedFiles() {
  const seen = new Set();
  return [document.querySelector("#import-files"), document.querySelector("#import-folder")].flatMap(input => [...input.files]).filter(file => {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.readAsDataURL(file);
  });
}

async function batchPayload(files, batchToken) {
  return { batch_token: batchToken, files: await Promise.all(files.map(async file => ({ filename: file.name, content_base64: await readAsBase64(file) }))) };
}

function previewTable(preview) {
  const rows = preview.preview_rows.map(row => `<tr>${preview.columns.map(column => `<td>${escapeHtml(row[column])}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table><thead><tr>${preview.columns.map(column => `<th>${escapeHtml(column)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function renderImportPreview(batch) {
  const files = batch.files.map(item => {
    if (!item.ok) return `<article class="batch-file batch-file--error"><strong>${escapeHtml(item.filename)}</strong><span>无法导入：${escapeHtml(item.error)}</span></article>`;
    return `<article class="batch-file"><strong>${escapeHtml(item.filename)}${item.duplicate ? "（已导入，将跳过）" : ""}</strong><span>${escapeHtml(item.preview.file_format.toUpperCase())} · ${item.preview.row_count} 条${item.preview.archive_entry ? ` · ${escapeHtml(item.preview.archive_entry)}` : ""}</span>${previewTable(item.preview)}</article>`;
  }).join("");
  document.querySelector("#import-preview").hidden = false;
  document.querySelector("#import-preview").innerHTML = `<h3>${batch.source === "alipay" ? "支付宝" : "微信"}批量预览</h3><p class="hint">已按内置模板自动识别字段。确认后将逐文件导入，异常或重复文件会保留在结果中。</p>${files}<button id="confirm-import">确认导入本批账单</button>`;
}

document.querySelectorAll("[data-preview-source]").forEach(button => button.addEventListener("click", async () => {
  const files = selectedFiles();
  if (!files.length) return alert("请先选择账单文件或账单文件夹");
  const source = button.dataset.previewSource;
  try {
    const preview = await request(`/api/imports/${source}/batch/preview`, { method: "POST", headers: { "Content-Type": "application/json", ...importHeaders() }, body: JSON.stringify(await batchPayload(files, newBatchToken())) });
    pendingBatch = { source, files, batchToken: preview.batch_token };
    renderImportPreview({ source, files: preview.files });
    document.querySelector("#import-result").textContent = "预览完成。为降低暴露范围，ZIP 密码已清空；确认导入 ZIP 时请重新输入。";
  } catch (error) { alert(`预览失败：${error.message}`); } finally { clearImportPassword(); }
}));

document.querySelector("#import-preview").addEventListener("click", async event => {
  if (event.target.id !== "confirm-import" || !pendingBatch) return;
  try {
    const result = await request(`/api/imports/${pendingBatch.source}/batch`, { method: "POST", headers: { "Content-Type": "application/json", ...importHeaders() }, body: JSON.stringify(await batchPayload(pendingBatch.files, pendingBatch.batchToken)) });
    const imported = result.files.filter(item => item.status === "imported").length;
    document.querySelector("#import-result").textContent = `本批已导入 ${imported} 个文件，跳过或失败 ${result.files.length - imported} 个；每个文件的批次与 SHA-256 引用已记录。`;
    document.querySelector("#import-preview").hidden = true;
    pendingBatch = null;
    refresh();
  } catch (error) { alert(`导入失败：${error.message}`); } finally { clearImportPassword(); }
});

async function refresh() {
  const [summary, bills, candidates] = await Promise.all([request("/api/dashboard"), request("/api/bills"), request("/api/candidates")]);
  const values = [summary.income, summary.spending, summary.net, summary.candidate_count];
  document.querySelectorAll("#summary strong").forEach((node, index) => node.textContent = index === 3 ? values[index] : money.format(values[index]));
  document.querySelector("#bills").innerHTML = bills.map(bill => `<tr><td>${new Date(bill.occurred_at).toLocaleString()}</td><td>${escapeHtml(bill.merchant)}<br><small>${escapeHtml(bill.note)}</small></td><td class="${bill.amount < 0 ? "negative" : "positive"}">${money.format(bill.amount)}</td><td>${escapeHtml(bill.category)}<br><small>${bill.tags.map(escapeHtml).join(" · ") || "未标记"}</small></td><td>${escapeHtml(bill.source_type || "手工")}</td><td class="tag-actions"><button data-tag="local_rules" data-id="${bill.id}">规则</button><button data-tag="llm_suggestion" data-id="${bill.id}">LLM</button><button data-tag="manual" data-id="${bill.id}">人工</button></td></tr>`).join("") || '<tr><td colspan="6">尚无流水</td></tr>';
  document.querySelector("#candidates").innerHTML = candidates.map(item => `<tr><td>${item.candidate_type === "duplicate" ? "重复" : "资产转移"}</td><td>${escapeHtml(item.bill.merchant)} ↔ ${escapeHtml(item.related_bill.merchant)}</td><td>${escapeHtml(item.reason)}</td><td>${Math.round(item.confidence * 100)}%</td><td>${item.status === "pending" ? "待确认" : item.status === "confirmed" ? "已确认" : "已忽略"}</td><td>${item.status === "pending" ? `<button data-candidate="confirmed" data-id="${item.id}">确认</button> <button class="delete" data-candidate="ignored" data-id="${item.id}">忽略</button>` : "—"}</td></tr>`).join("") || '<tr><td colspan="6">暂无候选</td></tr>';
}

document.querySelector("#bill-form").addEventListener("submit", async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.currentTarget));
  body.amount = Number(body.amount);
  try { await request("/api/bills", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); event.currentTarget.reset(); document.querySelector('input[name="occurred_at"]').value = now(); refresh(); } catch (error) { alert(`保存失败：${error.message}`); }
});

document.querySelector("#bills").addEventListener("click", async event => {
  const button = event.target.closest("[data-tag]");
  if (!button) return;
  const payload = { strategy: button.dataset.tag };
  if (payload.strategy === "manual") {
    const tags = prompt("输入标签，使用逗号分隔：");
    if (!tags) return;
    payload.tags = tags.split(",").map(value => value.trim()).filter(Boolean);
    payload.category = prompt("输入当前分类（可留空）：") || "人工分类";
  }
  try { const result = await request(`/api/bills/${button.dataset.id}/tags`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); alert(result.superseded ? "已记录为较低置信度审计历史。" : `已应用：${result.category}`); refresh(); } catch (error) { alert(`打标失败：${error.message}`); }
});

document.querySelector("#candidates").addEventListener("click", async event => {
  const button = event.target.closest("[data-candidate]");
  if (!button) return;
  try { await request(`/api/candidates/${button.dataset.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: button.dataset.candidate }) }); refresh(); } catch (error) { alert(`处理失败：${error.message}`); }
});

refresh().catch(error => alert(`加载失败：${error.message}`));

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
    <article class="panel"><h2>导入账单</h2><p class="hint">选择 CSV 文件后，按来源导入。已启用支付宝、微信适配器；银行适配器可按真实样本逐步增加。</p><input id="import-file" type="file" accept=".csv,text/csv"><div class="actions"><button class="provider-import" data-import="alipay" aria-label="导入支付宝账单 CSV 文件"><img class="provider-import__icon" src="/static/providers/alipay.svg" alt="" aria-hidden="true"><span>导入支付宝账单</span></button><button class="provider-import" data-import="wechat" aria-label="导入微信账单 CSV 文件"><img class="provider-import__icon" src="/static/providers/wechat.svg" alt="" aria-hidden="true"><span>导入微信账单</span></button></div><div class="actions secondary"><button disabled>工行适配器（待接入）</button><button disabled>农行适配器（待接入）</button></div><small id="import-result"></small></article>
    <article class="panel"><h2>手工录入流水</h2><form id="bill-form"><label>交易时间<input name="occurred_at" type="datetime-local" required></label><label>交易方<input name="merchant" required></label><label>金额（收入为正，支出为负）<input name="amount" type="number" step="0.01" required></label><label>备注<input name="note"></label><button>保存流水并生成规则建议</button></form></article>
  </section>
  <section class="panel"><h2>流水工作台</h2><p class="hint">规则建议、LLM 建议、人工确认和授权自动策略均记录为审计事件；数值更高的置信度覆盖当前展示，不删除历史。</p><div class="table-wrap"><table><thead><tr><th>时间</th><th>交易方</th><th>金额</th><th>当前分类 / 标签</th><th>来源</th><th>主动打标</th></tr></thead><tbody id="bills"></tbody></table></div></section>
  <section class="panel"><h2>重复与转移候选</h2><p class="hint">系统只生成候选，必须由你确认或忽略；确认转移后，后续统计可据此排除收支。</p><div class="table-wrap"><table><thead><tr><th>类型</th><th>两笔流水</th><th>依据</th><th>置信度</th><th>状态</th><th>处理</th></tr></thead><tbody id="candidates"></tbody></table></div></section>`;

document.querySelector('input[name="occurred_at"]').value = now();

async function refresh() {
  const [summary, bills, candidates] = await Promise.all([request("/api/dashboard"), request("/api/bills"), request("/api/candidates")]);
  const values = [summary.income, summary.spending, summary.net, summary.candidate_count];
  document.querySelectorAll("#summary strong").forEach((node, index) => node.textContent = index === 3 ? values[index] : money.format(values[index]));
  document.querySelector("#bills").innerHTML = bills.map(bill => `<tr><td>${new Date(bill.occurred_at).toLocaleString()}</td><td>${escapeHtml(bill.merchant)}<br><small>${escapeHtml(bill.note)}</small></td><td class="${bill.amount < 0 ? "negative" : "positive"}">${money.format(bill.amount)}</td><td>${escapeHtml(bill.category)}<br><small>${bill.tags.map(escapeHtml).join(" · ") || "未标记"}</small></td><td>${escapeHtml(bill.source_type || "手工")}</td><td class="tag-actions"><button data-tag="local_rules" data-id="${bill.id}">规则</button><button data-tag="llm_suggestion" data-id="${bill.id}">LLM</button><button data-tag="manual" data-id="${bill.id}">人工</button></td></tr>`).join("") || '<tr><td colspan="6">尚无流水</td></tr>';
  document.querySelector("#candidates").innerHTML = candidates.map(item => `<tr><td>${item.candidate_type === "duplicate" ? "重复" : "资产转移"}</td><td>${escapeHtml(item.bill.merchant)} ↔ ${escapeHtml(item.related_bill.merchant)}</td><td>${escapeHtml(item.reason)}</td><td>${Math.round(item.confidence * 100)}%</td><td>${item.status === "pending" ? "待确认" : item.status === "confirmed" ? "已确认" : "已忽略"}</td><td>${item.status === "pending" ? `<button data-candidate="confirmed" data-id="${item.id}">确认</button> <button class="delete" data-candidate="ignored" data-id="${item.id}">忽略</button>` : "—"}</td></tr>`).join("") || '<tr><td colspan="6">暂无候选</td></tr>';
}

document.querySelectorAll("[data-import]").forEach(button => button.addEventListener("click", async () => {
  const file = document.querySelector("#import-file").files[0];
  if (!file) return alert("请先选择支付宝或微信导出的 CSV 文件");
  try {
    const result = await request(`/api/imports/${button.dataset.import}?filename=${encodeURIComponent(file.name)}`, { method: "POST", headers: { "Content-Type": "text/csv" }, body: await file.arrayBuffer() });
    document.querySelector("#import-result").textContent = `已导入 ${result.imported_count}/${result.row_count} 条，生成 ${result.candidate_count} 个待复核候选。`;
    refresh();
  } catch (error) { alert(`导入失败：${error.message}`); }
}));

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

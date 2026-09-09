// Manual review tools share the workbench's request, modal and error handling.
export function reviewTools(h) {
  const { $, $$, esc, money, date, request, jsonRequest, modal, input, select, table, formSave, confirmReview, render, state, toast } = h;
  const roles = [["expense", "本人支出 / 手续费"], ["income", "本人收入"], ["receivable", "替人垫付 / 借出"], ["payable", "代管款 / 借入"], ["repayment_received", "收回垫款 / 本金"], ["repayment_paid", "归还代管款 / 本金"], ["transfer", "本人账户转移"]];
  const key = () => crypto.randomUUID();
  const saved = async (d, text) => { d.close(); await render({ preservePosition: true }); toast(text); };

  async function matters() {
    const items = await request("/api/review-matters");
    const d = modal("手工事项与往来", `<p>将一次 AA、代付、借还款或分次转账的金额分别解释。建议不影响这里已确认的分配。</p><button data-new>新建事项</button>${items.map(m => `<section class="panel"><h3>${esc(m.title)} · ${m.status === "confirmed" ? "已确认" : "已撤销"}</h3><p>${m.scenarios.map(esc).join(" / ")}</p><p>${m.status === "confirmed" ? m.balances.map(b => `${b.kind === "receivable" ? "应收" : "应付"} ${esc(b.party)} ${money(b.amount_cents / 100)}`).join("；") || "无往来余额" : "当前不占用流水金额"}</p><button data-edit="${m.id}">${m.status === "confirmed" ? "修改关联与分配" : "查看 / 重新确认"}</button> <button data-history="${m.id}">修改历史</button>${m.status === "confirmed" ? ` <button data-revoke="${m.id}">撤销事项</button>` : ""}</section>`).join("") || "<p>暂无手工事项。可从流水页选中记录后建立。</p>"}`, { wide: true });
    $("[data-new]", d).onclick = () => { d.close(); editor().catch(e => toast(e.message)); };
    $$('[data-edit]', d).forEach(b => b.onclick = () => { d.close(); editor(items.find(m => m.id === Number(b.dataset.edit))).catch(e => toast(e.message)); });
    $$('[data-history]', d).forEach(b => b.onclick = () => {
      const m = items.find(m => m.id === Number(b.dataset.history));
      modal("事项修改历史", m.history.map(row => `<section><h3>第 ${row.version} 次 · ${row.action === "revoke" ? "撤销" : "确认"}</h3><p>${date(row.created_at)} · ${esc(row.actor)} · ${esc(row.reason) || "未填写说明"}</p><pre>${esc(JSON.stringify(row.snapshot, null, 2))}</pre></section>`).join(""));
    });
    $$('[data-revoke]', d).forEach(b => b.onclick = () => formSave(d, async () => {
      const m = items.find(m => m.id === Number(b.dataset.revoke));
      if (!(await confirmReview(`撤销“${m.title}”的全部分配和往来解释；原始流水保留，金额将按剩余有效决定重新计算。`))) return;
      await jsonRequest(`/api/review-matters/${m.id}/undo`, "POST", { expected_version: m.version, idempotency_key: key(), reason: "用户撤销事项" });
      await saved(d, "事项已撤销，历史保留");
    }));
  }

  async function editor(existing = null, selected = []) {
    const result = await request("/api/transactions?scope=all&page_size=100");
    const catalog = new Map(result.items.map(b => [b.id, b]));
    selected.forEach(b => catalog.set(b.id, b));
    (existing?.lines || []).forEach(l => catalog.set(l.bill_id, { id: l.bill_id, merchant: l.merchant, amount: l.bill_amount, account_name: l.account_name, occurred_at: l.occurred_at }));
    const d = modal(existing ? "修改事项关联与分配" : "建立手工事项", `<form data-matter-form>${input("title", "事项名称", "text", existing?.title || "", 'required maxlength="160"')}${input("scenarios", "场景标签（逗号分隔，可组合）", "text", (existing?.scenarios || []).join(","))}<p class="notice">费用、代垫、本金与手续费分别列行。同一流水可拆分多行；未分配的余额会显示为待解释。收回款项请与垫付放在同一事项，并填写相同的往来对象。</p><div class="toolbar">${input("search", "查找其他流水", "search", "", 'placeholder="交易方或备注"')}<button type="button" data-search>搜索流水</button></div><p data-search-note class="muted">默认列出最近 100 条，搜索可查找其他记录。</p><div data-lines></div><button type="button" data-add>＋ 添加金额分项</button><label class="inline-check"><input type="checkbox" name="own" ${existing?.own_accounts_confirmed ? "checked" : ""}>转移分项涉及的账户均属于本人（仅本人转移需要）</label>${input("reason", "修改 / 确认说明", "text", "", 'required maxlength="500"')}<p class="notice" data-impact role="status"></p><button class="primary">核对并确认分配</button></form>`, { wide: true });
    const options = () => [...catalog.values()].map(b => [b.id, `${date(b.occurred_at)} · ${b.merchant} · ${money(b.amount)} · ${b.account_name}`]);
    const readLines = () => $$('[data-line]', d).map(row => ({ bill_id: Number($('[name=bill]', row).value), amount: Number($('[name=amount]', row).value), role: $('[name=role]', row).value, party: $('[name=party]', row).value.trim() }));
    const impact = () => {
      const lines = readLines();
      const income = lines.filter(l => l.role === "income").reduce((s, l) => s + Math.round(l.amount * 100), 0);
      const expense = lines.filter(l => l.role === "expense").reduce((s, l) => s + Math.round(l.amount * 100), 0);
      $('[data-impact]', d).textContent = `本事项确认收入 ${money(income / 100)}，本人支出 ${money(expense / 100)}；往来本金及转移部分不作为个人收支。提交前将校验所有金额和往来余额。`;
    };
    function add(line = {}) {
      const row = document.createElement('section'); row.dataset.line = ''; row.className = 'panel';
      const chosen = line.bill_id || [...catalog.keys()][0];
      row.innerHTML = `${select("bill", "来源流水", options(), chosen)}<div class="filter-grid">${input("amount", "分配金额（正数）", "number", line.amount ?? Math.abs(catalog.get(chosen)?.amount || 0), 'required min="0.01" step="0.01"')}${select("role", "这部分金额的用途", roles, line.role || (catalog.get(chosen)?.amount > 0 ? "income" : "expense"))}${input("party", "往来对象（涉及应收应付时必填）", "text", line.party || "", 'maxlength="120"')}</div><button type="button" data-remove>移除此分项</button>`;
      $('[data-remove]', row).onclick = () => { row.remove(); impact(); };
      $('[data-lines]', d).append(row); impact();
    }
    const lines = existing?.lines || selected.map(b => ({ bill_id: b.id, amount: Math.abs(b.amount) }));
    (lines.length ? lines : [{}]).forEach(add);
    $('[data-add]', d).onclick = () => add();
    $('[data-matter-form]', d).addEventListener('input', impact);
    let searchVersion = 0;
    $('[data-search]', d).onclick = async () => {
      const version = ++searchVersion;
      try {
        const found = await request(`/api/transactions?scope=all&page_size=100&q=${encodeURIComponent($('[name=search]', d).value)}`);
        if (!d.isConnected || version !== searchVersion) return;
        found.items.forEach(b => catalog.set(b.id, b));
        $$('[data-line] [name=bill]', d).forEach(el => { const value = el.value; el.innerHTML = options().map(([id, label]) => `<option value="${id}">${esc(label)}</option>`).join(''); el.value = value; });
        $('[data-search-note]', d).textContent = `找到 ${found.total} 条，已加入前 ${found.items.length} 条供选择；可进一步缩小搜索。`;
      } catch (error) { toast(error.message); }
    };
    let retryPayload = null, fingerprint = '';
    $('[data-matter-form]', d).onsubmit = e => {
      e.preventDefault();
      const payload = { title: $('[name=title]', d).value, scenarios: $('[name=scenarios]', d).value.split(/[,，]/).map(x => x.trim()).filter(Boolean), lines: readLines(), own_accounts_confirmed: $('[name=own]', d).checked, expected_version: existing?.version || 0, reason: $('[name=reason]', d).value };
      const next = JSON.stringify(payload);
      if (next !== fingerprint) { fingerprint = next; retryPayload = { ...payload, idempotency_key: key() }; }
      formSave(d, async () => {
        if (!(await confirmReview($('[data-impact]', d).textContent))) return;
        await jsonRequest(existing ? `/api/review-matters/${existing.id}` : '/api/review-matters', existing ? 'PUT' : 'POST', retryPayload);
        await saved(d, "事项已确认，可在手工事项中修改或撤销");
      });
    };
  }

  async function refunds() {
    const items = await request('/api/refunds');
    const d = modal('退款记录', `<p>退款不属于收入。撤销分配后，金额回到未分配退款；纠正退款性质需要先撤销全部有效分配。</p>${items.map(item => `<section class="panel"><h3>${esc(item.bill.merchant)} · ${money(item.bill.amount)}</h3><p>未分配退款 ${money(item.unallocated)}</p><button data-allocate="${item.bill.id}">关联原支出</button> <button data-nature="${item.bill.id}">纠正退款性质</button>${item.allocations.map(a => `<p>原支出流水 ${a.expense_bill_id} · ${money(a.amount)} · ${a.status === 'confirmed' ? '已确认' : '已撤销'} ${a.status === 'confirmed' ? `<button data-undo-refund="${a.id}">撤销此分配</button>` : ''} <button data-audit="${a.id}">查看审计</button></p>`).join('')}</section>`).join('') || '<p>暂无退款。在正向流水详情中选择“确认为退款”。</p>'}`, { wide: true });
    $$('[data-allocate]', d).forEach(b => b.onclick = () => { d.close(); refundAllocation(Number(b.dataset.allocate)).catch(e => toast(e.message)); });
    $$('[data-audit]', d).forEach(b => b.onclick = async () => { try { const audits = await request(`/api/refund-allocations/${b.dataset.audit}/audits`); modal('退款分配审计', `<pre>${esc(JSON.stringify(audits, null, 2))}</pre>`); } catch(e) { toast(e.message); } });
    $$('[data-undo-refund]', d).forEach(b => b.onclick = () => formSave(d, async () => {
      if (!(await confirmReview('只撤销选中的退款分配，其他分配保持有效；本笔金额回到未分配退款。'))) return;
      await jsonRequest(`/api/refund-allocations/${b.dataset.undoRefund}/undo`, 'POST', { reason: '用户撤销退款分配' });
      d.close(); await render({ preservePosition: true }); await refunds();
    }));
    $$('[data-nature]', d).forEach(b => b.onclick = () => formSave(d, async () => {
      const item = items.find(i => i.bill.id === Number(b.dataset.nature));
      if (!(await confirmReview('撤销退款性质后，该正向流水会恢复为普通流入并进入原有收入口径。'))) return;
      await jsonRequest(`/api/transactions/${item.bill.id}/nature`, 'PUT', { nature: 'ordinary', expected_audit_id: item.nature_audit_id, reason: '用户纠正错误的退款性质' });
      await saved(d, '退款性质已纠正');
    }));
  }

  async function refundAllocation(billId) {
    const d = modal('关联退款与原支出', `<form>${input('search', '查找原支出（交易方或备注）', 'search', '')}<button type="button" data-search-expense>搜索</button><div data-expenses></div>${input('amount', '本次抵扣金额', 'number', '', 'required min="0.01" step="0.01"')}${input('reason', '关联依据', 'text', '', 'required maxlength="500"')}<p>允许部分分配；未分配部分仍是退款，不会转为收入。已有往来分摊的支出暂不支持混合退款，将明确提示冲突。</p><button class="primary">确认分配</button></form>`);
    let version = 0;
    async function search() {
      const current = ++version;
      const data = await request(`/api/transactions?direction=expense&page_size=100&q=${encodeURIComponent($('[name=search]', d).value)}`);
      if (d.isConnected && current === version) $('[data-expenses]', d).innerHTML = select('expense', '原支出', data.items.map(b => [b.id, `${date(b.occurred_at)} · ${b.merchant} · ${money(b.amount)} · ${b.account_name}`]), '') + `<small>找到 ${data.total} 条，展示前 100 条。</small>`;
    }
    $('[data-search-expense]', d).onclick = () => search().catch(e => toast(e.message));
    await search();
    let previous = '', retry = null;
    $('form', d).onsubmit = e => {
      e.preventDefault();
      const payload = { refund_bill_id: billId, expense_bill_id: Number($('[name=expense]', d).value), amount: Number($('[name=amount]', d).value), reason: $('[name=reason]', d).value };
      if (JSON.stringify(payload) !== previous) { previous = JSON.stringify(payload); retry = {...payload, idempotency_key: key()}; }
      formSave(d, async () => { await jsonRequest('/api/refund-allocations', 'POST', retry); await saved(d, '退款分配已确认'); await refunds(); });
    };
  }

  async function issues() {
    const items = await request('/api/import-issues');
    const pending = items.filter(i => !i.resolved_at);
    const d = modal('导入数据问题', `<p role="status">${pending.length} 条待核验。原始字段已保存；这些记录在修正前不进入正式金额。</p>${items.map(i => `<section class="panel"><h3>${esc(i.filename)} · 第 ${i.row_number} 行</h3><p class="${i.resolved_at ? 'muted' : 'error'}">${esc(i.error)}</p><details><summary>原始字段与处理依据</summary><pre>${esc(JSON.stringify(i.raw_fields, null, 2))}</pre><pre>${esc(JSON.stringify(i.history, null, 2))}</pre></details>${i.resolved_at ? `<p>已处理 · ${i.bill_id ? `流水 ${i.bill_id}` : '非实际人民币收付'} · ${date(i.resolved_at)}</p>${!i.bill_id ? `<button data-reopen="${i.id}">重新核验</button>` : ''}` : `<button data-correct="${i.id}">核对并修正</button>`}</section>`).join('') || '<p>没有数据问题。</p>'}`, { wide: true });
    $$('[data-reopen]', d).forEach(b => b.onclick = () => formSave(d, async () => { await jsonRequest(`/api/import-issues/${b.dataset.reopen}/reopen`, 'POST', {reason: '用户重新核验原始记录'}); d.close(); await issues(); }));
    $$('[data-correct]', d).forEach(b => b.onclick = () => {
      const item = items.find(i => i.id === Number(b.dataset.correct));
      const edit = modal('修正导入记录', `<pre>${esc(JSON.stringify(item.raw_fields, null, 2))}</pre><form>${input('occurred_at', '实际交易时间', 'datetime-local', '', 'required step="1"')}${input('merchant', '交易方', 'text', '', 'required maxlength="200"')}${input('amount', '人民币金额（流出填负数）', 'number', '', 'required step="0.01"')}${input('account_name', '账户', 'text', '', 'required maxlength="120"')}${input('note', '备注', 'text', '')}${input('reason', '修正依据', 'text', '', 'required maxlength="500"')}<p>请核验确实发生了人民币收付。外币或失败交易不能当作人民币付款补入。</p><button class="primary">确认修正并入账</button></form>`);
      $('form', edit).onsubmit = e => { e.preventDefault(); const payload = Object.fromEntries(new FormData(e.currentTarget)); payload.amount = Number(payload.amount); formSave(edit, async () => { await jsonRequest(`/api/import-issues/${item.id}/resolve`, 'POST', payload); d.close(); await saved(edit, '记录已修正，原始字段保留'); }); };
      $('form', edit).insertAdjacentHTML('beforeend', '<button type="button" data-not-posted>确认不是实际人民币收付</button>');
      $('[data-not-posted]', edit).onclick = () => formSave(edit, async () => {
        const reason = $('[name=reason]', edit).value;
        if (!(await confirmReview('将此记录确认为非实际人民币收付，不新增账目；原始字段和处理依据保留。'))) return;
        await jsonRequest(`/api/import-issues/${item.id}/dismiss`, 'POST', {reason});
        d.close(); await saved(edit, '处理依据已保存，原始记录保留');
      });
    });
  }

  async function drill(metric) {
    const params = new URLSearchParams(state.params);
    const data = await request(`/api/ledger/drilldown?${params}`);
    const rows = data.contributions.filter(r => metric === 'count' || (metric === 'net' ? true : r[metric] !== 0));
    modal('汇总组成与回钻', `<p>口径 ${esc(data.basis_version)} · 生成于 ${date(data.generated_at)}</p>${table(['流水', '实际收付', '收入贡献', '支出贡献', '退款抵扣', '净额贡献'], rows.map(r => `<tr><td>${esc(r.merchant)}<small>${date(r.occurred_at)} · 流水 ${r.bill_id}</small></td><td>${money(r.cash_amount)}</td><td>${money(r.income)}</td><td>${money(r.spending)}</td><td>${money(r.refund_offset)}</td><td>${money(r.net)}</td></tr>`))}<p>原始收付与个人收支可能不同，退款、往来和转移分别解释。</p><details><summary>关联事实与修订证据</summary><pre>${esc(JSON.stringify(data, null, 2))}</pre></details>`, { wide: true });
  }

  async function account(bill) {
    const d = modal('修订账户', `<form>${input('account_name', '账户名称', 'text', bill.account_name, 'required maxlength="120"')}${input('reason', '修订依据', 'text', '', 'required maxlength="500"')}<button class="primary">确认修订</button></form>`);
    let previous = '', retry = null;
    $('form', d).onsubmit = e => { e.preventDefault(); const payload = Object.fromEntries(new FormData(e.currentTarget)); if (JSON.stringify(payload) !== previous) { previous = JSON.stringify(payload); retry = {...payload, idempotency_key: key()}; } formSave(d, async () => { await jsonRequest(`/api/transactions/${bill.id}/account`, 'PUT', retry); await saved(d, '账户已修订'); }); };
  }
  function decorate(page, root) {
    $$('[data-decision=confirm_third_party_transfer]', root).forEach(button => button.remove());
    if (['candidates', 'data', 'summary'].includes(page)) {
      root.insertAdjacentHTML('afterbegin', '<div class="toolbar"><button data-action="review-matters">手工事项与往来</button><button data-action="review-refunds">退款记录</button><button data-action="review-issues">数据问题</button></div>');
    }
    if (page === 'data') $('[id=bill-selection]', root)?.insertAdjacentHTML('beforeend', '<button data-action="new-matter">建立手工事项</button>');
    if (page === 'summary') {
      const s = state.summary;
      const metrics = [['net', '个人净收支', s.net], ['income', '个人收入', s.income], ['spending', '本人支出（抵扣前）', Math.abs(s.spending)], ['refund_offset', '退款抵扣', s.refund_offset], ['count', '参与解释的流水', s.effective_count]];
      $('.cards', root).innerHTML = metrics.map(([metric, label, value]) => `<button class="metric" data-action="drill" data-metric="${metric}"><span>${label}</span><strong>${metric === 'count' ? value : money(value)}</strong><small>点击核对金额组成及依据</small></button>`).join('');
      $('.cards', root).insertAdjacentHTML('afterend', `<p class="notice">实际现金净变化 ${money(s.cash_net)} · 未分配退款 ${money(s.unallocated_refund)} · 事项待解释金额 ${money(s.unresolved_amount)}</p><p class="muted">口径 ${esc(s.basis_version)} · ${date(s.generated_at)} · 退款抵扣按退款发生期间统计，可关联期外原支出。现金变化保留本人转移。</p>${s.issue_count ? `<p class="error" role="alert">全账本有 ${s.issue_count} 条导入记录待核验，尚未进入金额。请打开“数据问题”。</p>` : ''}`);
      if (s.unreviewed_count) $('.cards', root).insertAdjacentHTML('afterend', `<p class="muted">其中 ${s.unreviewed_count} 条尚未建立金额解释，暂按账单收付方向统计；借款、代付等请在手工事项中确认。</p>`);
      if (s.review_warnings.length) root.insertAdjacentHTML('afterbegin', `<div class="error" role="alert">有 ${s.review_warnings.length} 项历史复核需要核验，当前金额不能作为最终核定结果。${s.review_warnings.map(w => `<p>候选 ${w.candidate_id}：${esc(w.reason)}${w.conflicts_with ? `（冲突候选 ${w.conflicts_with}）` : ''}</p>`).join('')}</div>`);
    }
  }

  async function billActions(d, bill) {
    const [audits, accounts, nature] = await Promise.all([request(`/api/bills/${bill.id}/tags`), request(`/api/transactions/${bill.id}/account-revisions`), request(`/api/transactions/${bill.id}/nature`)]);
    if (!d.isConnected) return;
    const current = audits.find(a => !a.superseded);
    $('.dialog-body', d).insertAdjacentHTML('beforeend', `<div class="actions"><button data-account>修订账户</button>${bill.amount > 0 && !bill.aggregate_excluded ? '<button data-refund>确认为退款 / 查看退款</button>' : ''}</div><details open><summary>标签建议与修改历史</summary>${audits.map(a => `<p>${date(a.created_at)} · ${a.action === 'suggest' ? '系统建议' : a.action === 'undo' ? '撤销记录' : '人工决定'} · ${esc(a.category)} · ${Object.values(a.tag_state).map(esc).join(' / ')} ${a.undone ? '（已撤销）' : a.superseded ? '（非当前值）' : '（当前值）'}${a.action === 'suggest' ? ` <button data-accept-tag="${a.id}">采用此建议</button>` : ''}${a.id === current?.id ? ` <button data-undo-tag="${a.id}">${a.action === 'undo' ? '恢复撤销前标签' : '撤销此标签修改'}</button>` : ''}</p>`).join('')}</details><details><summary>账户修订历史</summary>${accounts.map(a => `<p>${date(a.created_at)} · ${esc(a.before_account)} → ${esc(a.after_account)} · ${esc(a.reason)}${a.action === 'confirm' && !a.undone ? ` <button data-undo-account="${a.revision_id}">撤销这次修订</button>` : ''}</p>`).join('') || '<p>暂无修订</p>'}</details>`);
    $('[data-account]', d).onclick = () => { d.close(); account(bill).catch(e => toast(e.message)); };
    const refundButton = $('[data-refund]', d);
    if (refundButton) refundButton.onclick = () => formSave(d, async () => {
      if (nature.nature !== 'refund') {
        if (!(await confirmReview('确认这笔流入属于退款：整笔退出收入口径；尚未关联原支出的部分保留为未分配退款。'))) return;
        await jsonRequest(`/api/transactions/${bill.id}/nature`, 'PUT', { nature: 'refund', reason: '用户在流水详情确认退款性质', expected_audit_id: nature.audit_id });
      }
      d.close(); await render({ preservePosition: true }); await refunds();
    });
    $$('[data-undo-tag]', d).forEach(b => b.onclick = () => formSave(d, async () => { await jsonRequest(`/api/bills/${bill.id}/tags/${b.dataset.undoTag}/undo`, 'POST', {reason: '用户撤销标签修订'}); await saved(d, '标签已恢复，可继续修改'); }));
    $$('[data-accept-tag]', d).forEach(b => b.onclick = () => formSave(d, async () => {
      const suggestion = audits.find(a => a.id === Number(b.dataset.acceptTag));
      await jsonRequest(`/api/transactions/${bill.id}/tag-state`, 'PUT', {tag_state: suggestion.tag_state, strategy: 'manual', expected_audit_id: current?.id || 0, reason: `用户采用建议 ${suggestion.id}`});
      await saved(d, '建议已由本人确认');
    }));
    $$('[data-undo-account]', d).forEach(b => b.onclick = () => formSave(d, async () => { await jsonRequest(`/api/transactions/${bill.id}/account-revisions/${b.dataset.undoAccount}/undo`, 'POST', {reason: '用户撤销账户修订'}); await saved(d, '账户修订已撤销'); }));
  }
  return { matters, editor, refunds, issues, drill, account, decorate, billActions };
}

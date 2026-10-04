import { request, jsonRequest, isUnknownWrite } from "../api/client.js";
import { esc, date, resourceId } from "../util/core.js";
import { table } from "../component/table.js";
import { namedChoice, bindNamedChoice, metadataLabel, mountPicker } from '../component/workbench.js';
import { candidateScan, scanControls } from '../util/candidate-scan.js';
import { financialIssueMessage, financialStateLabel } from '../util/financial-copy.js';

const base = "/paam/ledger/v1";
const endpoint = { party: "account-party", account: "account", ref: "account-ref" };
const labels = { party: "个人", account: "管理集合", ref: "具体来源卡" };
let readController, refScan, mountedHost, mountedScan, mountedRoute;

export function stopAccountRead() {
  readController?.abort();
  refScan?.stop(true);
  if (mountedScan !== refScan) mountedScan?.stop(true);
  if (mountedHost && mountedRoute !== location.hash) {
    mountedScan?.reset();
    mountedHost = mountedScan = mountedRoute = undefined;
  }
}

// A failed same-route read leaves the last rendered scope and results intact.
// Rebind its controls to a live lifetime, not the cancelled page request. The
// mounted scan retains its cursor; a failed replacement read cannot replace it.
export function restoreAccountManagement(root, reload) {
  const host = root.querySelector('[data-account-management]');
  if (!host || host !== mountedHost) return;
  stopAccountRead();
  if (refScan !== mountedScan) refScan?.reset();
  readController = new AbortController();
  refScan = mountedScan;
  bindAccountManagement(root, reload);
}

const input = (name, label, value = "", extra = "") => `<label>${label}<input name="${name}" value="${esc(value)}" ${extra}></label>`;

export function accountNavigation(params, changes) {
  const next = new URLSearchParams(params);
  Object.entries(changes).forEach(([key, value]) => next.set(key, value));
  next.delete('party_page'); next.delete('account_page');
  return `#workbench/account?${next}`;
}

export function accountScope(params) {
  const party = resourceId(params.get('party') || 0, {allowZero: true});
  const account = resourceId(params.get('account') || 0, {allowZero: true});
  const unassigned = params.get('unassigned') === '1';
  if (unassigned && (party || account)) throw new Error('未分组是独立范围；请清除个人和集合条件后选择');
  const pageSize = resourceId(params.get('page_size') || 20);
  const page = resourceId(params.get('ref_page') || 1);
  if (![20, 50, 100].includes(pageSize)) throw new Error('账户每页仅支持20、50或100项');
  if (page > Math.floor(Number.MAX_SAFE_INTEGER / pageSize)) throw new Error('账户页码超出精确范围');
  const status = params.get('status') || '';
  if (!['', 'ACTIVE', 'CLOSED'].includes(status)) throw new Error('账户状态条件无效');
  const word = (params.get('word') || '').trim();
  if (word.length > 128) throw new Error('字面搜索最多128个字符');
  return {party, account, unassigned, pageSize, page, status, word};
}

export function accountRefFilter(scope) {
  const expressions = [];
  if (scope.unassigned) expressions.push({key:'account_id', op:'=', val:0});
  else {
    if (scope.party) expressions.push({key:'party_id', op:'=', val:scope.party});
    if (scope.account) expressions.push({key:'account_id', op:'=', val:scope.account});
  }
  if (scope.status) expressions.push({key:'status', op:'=', val:scope.status});
  return expressions.length > 1 ? {op:'AND', expression:expressions} : expressions[0];
}

function pageControls(result) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  return `<div class="actions account-list-pager"><button type="button" data-account-prev data-account-page="${result.page_index - 1}" ${result.page_index <= 1 ? 'disabled' : ''}>上一页</button>
    <span data-account-count>${result.page_index} / ${pages}，共 ${result.total} 项</span>
    <button type="button" data-account-next data-account-page="${result.page_index + 1}" ${result.page_index >= pages ? 'disabled' : ''}>下一页</button></div>`;
}

function cardList(result, searching) {
  const rows = result.items.map(row => `<tr data-ref-id="${row.id}">
    <td data-label="来源卡"><strong>${esc(row.institution || row.display_label?.split(' · ')[0] || row.source_namespace || '来源机构未登记')} · ${esc(row.name || '未命名来源')}</strong><small>${esc(row.source_identity || row.reference || '账号未知')} · #${row.id}</small></td>
    <td data-label="归属">${esc(row.party_name || '未分组')}<small>${esc(row.account_name || '无管理集合')}</small></td>
    <td data-label="状态">${esc(financialStateLabel('account',row.status))}</td>
    <td data-label="资料"><span>${esc({RELIABLE:'可靠来源', WEAK:'弱来源', UNKNOWN:'身份未知'}[row.identity_strength])}</span><small>历史来源入库：${row.latest_source_time ? esc(date(row.latest_source_time)) : '未知'}</small></td>
    <td data-label="操作"><div class="actions"><button type="button" data-account-edit="ref" data-id="${row.id}">维护</button><button type="button" data-account-move="${row.id}">变更归属</button></div></td></tr>`);
  const empty = searching && result.has_more ? '尚无命中；本次查找仍未结束。' : '当前范围没有来源卡。';
  return `${rows.length ? table(['来源卡', '个人 / 集合', '状态', '资料 / 来源', '操作'], rows) : `<p class="account-empty" role="status">${empty}</p>`}
    ${searching ? scanControls('account', result) : pageControls(result)}`;
}

export async function accountManagementPage(params) {
  stopAccountRead();
  readController = new AbortController();
  const signal = readController.signal, route = location.hash;
  const scope = accountScope(params);
  const account = scope.account ? await request(`${base}/account/${scope.account}`, {signal}) : null;
  if (account && scope.party && account.party_id !== scope.party) throw new Error('所选集合不属于当前个人，请清除集合或重新选择');
  if (account) scope.party = resourceId(account.party_id);
  const party = scope.party ? await request(`${base}/account-party/${scope.party}`, {signal}) : null;
  const query = new URLSearchParams({page_size: scope.pageSize});
  const predicate = accountRefFilter(scope);
  if (predicate) query.set('filter', JSON.stringify(predicate));
  // Page GET cancellation is separate from the last mounted search lifetime.
  // stopAccountRead stops both scans explicitly; successful mounting retires
  // the old scan. This permits retrying a retained cursor after a read failure.
  refScan = candidateScan(`${base}/account-ref`, 'account', row => row.id);
  if (scope.word) query.set('query', JSON.stringify([{key:'display_label', word:scope.word}]));
  else query.set('page_index', scope.page);
  const result = scope.word ? await refScan.read(query, route) : await request(`${base}/account-ref/list?${query}`, {signal});
  const partyLabel = party ? metadataLabel(party) : '全部个人';
  const accountLabel = account ? metadataLabel(account) : '全部集合';
  const compact = typeof matchMedia === 'function' && matchMedia('(max-width:1000px)').matches;
  return `<div data-account-management data-party="${scope.party}" data-account="${scope.account}" data-account-params="${esc(params.toString())}"
    data-party-label="${esc(partyLabel)}" data-account-label="${esc(accountLabel)}">
    <div class="account-workspace">
      <aside class="panel account-metadata-scope" aria-label="账户归属范围"><details ${compact ? '' : 'open'}><summary>归属范围<span>${scope.unassigned ? '已识别但未分组' : `${esc(party?.name || '全部个人')} / ${esc(account?.name || '全部集合')}`}</span></summary><div class="account-scope-fields">
        ${namedChoice('party_id', '个人', {value:scope.party, text:partyLabel, pick:'选择个人', clear:'全部个人'})}
        ${namedChoice('account_id', '管理集合', {value:scope.account, text:accountLabel, pick:'选择集合', clear:'全部集合'})}
        <a href="${esc(accountNavigation(params, {party:0, account:0, unassigned:1, ref_page:1}))}" data-account-unassigned>已识别但未分组（独立范围）</a>
        <a href="#workbench/account">清除全部条件</a>
        <div class="actions"><button type="button" data-account-manage="party">管理个人</button><button type="button" data-account-manage="account">管理集合</button></div>
        <div class="actions"><button type="button" data-account-create="party">新建个人</button><button type="button" data-account-create="account">新建集合</button></div>
        <small>维护个人和集合无需先建卡。来源身份、登记账号与交易订单号不同；关闭不删除历史现金。</small>
      </div></details></aside>
      <section class="panel account-card-surface"><div class="account-card-head"><div><h1>具体来源卡</h1><p>${scope.unassigned ? '未分组：不归属任何个人或集合。' : `${esc(party?.name || '全部个人')} / ${esc(account?.name || '全部集合')}`}</p></div><div class="actions"><a href="#workbench/review?complete_source=1" data-account-complete-source>补齐历史流水来源</a><button type="button" data-account-create="ref">新建来源卡</button></div></div>
        <form data-account-filter class="account-filter"><label class="account-word">字面搜索<input name="word" value="${esc(scope.word)}" maxlength="128" placeholder="机构、名称、遮罩号或归属"></label>
          <label>状态<select name="status">${[['','全部状态'],...['ACTIVE','CLOSED'].map(code=>[code,financialStateLabel('account',code)])].map(([value,text])=>`<option value="${value}" ${scope.status === value ? 'selected' : ''}>${esc(text)}</option>`).join('')}</select></label>
          <label>每页<select name="page_size">${[20,50,100].map(value=>`<option value="${value}" ${scope.pageSize === value ? 'selected' : ''}>${value}项</option>`).join('')}</select></label>
          <button type="submit">查找</button><button type="button" data-account-clear-search>清空搜索</button></form>
        <div class="account-ref-table" data-account-list>${cardList(result, !!scope.word)}</div>
        <p class="account-metadata-note">归属按卡的当前真实集合筛选；数量和余额另见<a href="#workbench/position">资产负债对象</a>。来源日期为历史证据入库时间，不是余额更新。</p>
      </section>
    </div></div>`;
}

function dialog(title, body) {
  const node = document.createElement("dialog");
  node.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button type="button" data-account-close>关闭</button></div><div class="dialog-body">${body}</div>`;
  node.querySelector("[data-account-close]").onclick = () => node.close();
  node.addEventListener("close", () => node.remove());
  const signal = readController?.signal;
  const abort = () => { if (node.open) node.close(); };
  signal?.addEventListener('abort', abort, {once:true});
  node.addEventListener('close', () => signal?.removeEventListener('abort', abort), {once:true});
  document.body.append(node);
  node.showModal();
  return node;
}

function writeError(node, error) {
  const uncertain = isUnknownWrite(error);
  node.dataset.writeOutcome = uncertain ? "UNKNOWN" : "NOT_COMMITTED";
  node.querySelector("[role=status]").textContent = uncertain
    ? "提交结果未知。先关闭窗口并查询当前对象 / 列表，不要重发创建或变更。"
    : error.code === "WRITE_BUSY" ? "本次未提交，输入已保留。稍后重新读取或预览，再由你提交；不会自动重发。"
    : `${financialIssueMessage(error)}；本次未提交。`;
  node.querySelectorAll("[type=submit]").forEach((button) => { button.disabled = uncertain; });
  node.querySelectorAll('[data-choice-pick], [data-choice-clear]').forEach(button => {button.disabled = uncertain;});
}

async function openAccountCommand(host, reload, kind, rawId, moving = false) {
    const route = location.hash;
    try {
      const id = rawId ? resourceId(rawId) : null;
      const row = id ? await request(`${base}/${endpoint[kind]}/${id}`, { signal: readController?.signal }) : {};
      if (!host.isConnected || route !== location.hash) return;
      if (moving) {
        const node = dialog("归属变更预览", `<form class="stack">${namedChoice('account_id', '目标管理集合', {pick: '查找集合', clear: '明确移至未分组'})}
          <p>这里只移动卡的元数据归属，不修改 Review 或 Ledger。</p><p role="status"></p><div data-impact></div>
          <button type="submit">预览影响</button><button type="button" data-confirm disabled>确认当前预览</button></form>`);
        let plan, change, target, generation = 0;
        const pickerController = new AbortController();
        node.addEventListener('close', () => pickerController.abort(), {once: true});
        readController?.signal.addEventListener('abort', () => pickerController.abort(), {once: true});
        const invalidate = () => { ++generation; plan = null; node.querySelector('[data-confirm]').disabled = true; };
        node.querySelector('form').addEventListener('change', invalidate);
        bindNamedChoice(node, 'account_id', {url: `${base}/account`, title: '选择目标管理集合',
          signal: pickerController.signal, allowZero: true, zeroLabel: '未分组', pickerAttribute: 'data-account-picker',
          initialize: false, changed: row => {target = row;} });
        node.querySelector("form").onsubmit = async (event) => {
          event.preventDefault();
          if (node.dataset.writeOutcome === 'UNKNOWN') return;
          const submit = node.querySelector("[type=submit]");
          submit.disabled = true;
          const issued = ++generation;
          plan = null; node.querySelector('[data-confirm]').disabled = true;
          try {
            change = { account_id: resourceId(node.querySelector('[name="account_id"]').value, { allowZero: true }), expected_updated_time: row.updated_time };
            const nextPlan = await jsonRequest(`${base}/account-ref/${id}/move-preview`, "POST", change);
            if (!node.isConnected || issued !== generation) return;
            plan = nextPlan;
            node.querySelector("[data-impact]").textContent = `${row.party_name || '未分组'} / ${row.account_name || '未分组'} → ${target ? `${target.party_name} / ${target.name}` : '未分组'}；影响 ${plan.affected_ledger_count} 笔历史流水的当前筛选归属。${plan.cross_party ? "注意：跨个人变更，请核对。" : ""}`;
            node.querySelector("[data-confirm]").disabled = false;
          } catch (error) {
            // A preview POST is read-only: a lost preview can be requested
            // again; it must not be mistaken for an unknown move command.
            if (node.isConnected && issued === generation) node.querySelector('[role=status]').textContent = `${financialIssueMessage(error,'归属变更预览未取得可靠结果，请重新读取当前归属核对')}；未执行归属变更。`;
          }
          finally { submit.disabled = node.dataset.writeOutcome === 'UNKNOWN'; }
        };
        node.querySelector("[data-confirm]").onclick = async () => {
          if (!plan || node.dataset.writeOutcome === "UNKNOWN") return;
          node.querySelector("[data-confirm]").disabled = true;
          node.querySelector("[type=submit]").disabled = true;
          try {
            await jsonRequest(`${base}/account-ref/${id}/move-command`, "POST", { ...change, preview_digest: plan.preview_digest });
            node.close(); await reload();
          } catch (error) { plan = null; writeError(node, error); }
        };
        return;
      }
      const owner = !id && kind === 'account' ? namedChoice('party_id', '所属个人', {
        value:host.dataset.party === '0' ? '' : host.dataset.party,
        text:host.dataset.party === '0' ? '请选择所属个人' : host.dataset.partyLabel, pick:'选择个人'})
        : !id && kind === 'ref' ? namedChoice('account_id', '所属管理集合', {
          value:host.dataset.account !== '0' ? host.dataset.account : host.dataset.party !== '0' ? '' : 0,
          text:host.dataset.account !== '0' ? host.dataset.accountLabel : host.dataset.party !== '0' ? '请选择集合，或明确设为未分组' : '未分组',
          pick:'选择集合', clear:'明确未分组'}) : '';
      const fields = owner + input("name", "名称", row.name || "", `maxlength="120" ${kind === "ref" ? "" : "required"}`)
        + (kind === "ref" ? input("institution", "机构", row.institution || "", 'maxlength="120"') + input("reference", "用户登记本方账号（与来源身份不同）", row.reference || "", 'maxlength="200" autocomplete="off"') : "")
        + (kind === "account" ? input("statement_interval_months", "账单提醒月数（0 为手动）", row.statement_interval_months || 0, 'type="number" min="0" max="120" required')
          + input("snapshot_interval_months", "余额快照提醒月数（0 为手动）", row.snapshot_interval_months || 0, 'type="number" min="0" max="120" required') : "")
        + (id ? `<label>状态<select name="status">${['ACTIVE','CLOSED'].map(code=>`<option value="${code}" ${row.status === code ? 'selected' : ''}>${esc(financialStateLabel('account',code))}</option>`).join('')}</select></label>` : "");
      const node = dialog(`${id ? "维护" : "新建"}${labels[kind]}`, `<form class="stack">${fields}<p role="status"></p><button type="submit">保存</button></form>`);
      const controller = new AbortController();
      node.addEventListener('close', () => controller.abort(), {once:true});
      if (!id && kind === 'account') bindNamedChoice(node, 'party_id', {url:`${base}/account-party`,
        title:'选择所属个人', signal:controller.signal, initialize:false, filter:{key:'status', op:'=', val:'ACTIVE'}});
      if (!id && kind === 'ref') bindNamedChoice(node, 'account_id', {url:`${base}/account`,
        title:'选择所属集合', signal:controller.signal, initialize:false, allowZero:true, zeroLabel:'未分组',
        filter:resourceId(host.dataset.party, {allowZero:true}) ? {op:'AND', expression:[
          {key:'party_id', op:'=', val:resourceId(host.dataset.party)}, {key:'status', op:'=', val:'ACTIVE'}]} : {key:'status', op:'=', val:'ACTIVE'}});
      node.querySelector("form").onsubmit = async (event) => {
        event.preventDefault();
        if (node.dataset.writeOutcome === "UNKNOWN") return;
        node.querySelector("[type=submit]").disabled = true;
        try {
          const values = Object.fromEntries(new FormData(event.target));
          if (kind === "account") {
            values.statement_interval_months = Number(values.statement_interval_months);
            values.snapshot_interval_months = Number(values.snapshot_interval_months);
            if (!id) values.party_id = resourceId(values.party_id);
          }
          if (kind === "ref") values.account_id = resourceId(id ? row.account_id : values.account_id, { allowZero: true });
          if (id) values.expected_updated_time = row.updated_time;
          await jsonRequest(`${base}/${endpoint[kind]}${id ? `/${id}/metadata` : ""}`, id ? "PUT" : "POST", values);
          node.close(); await reload();
        } catch (error) { writeError(node, error); }
      };
    } catch (error) {
      if (error.name !== "AbortError" && host.isConnected && route === location.hash)
        dialog("读取失败", `<p role="status">${esc(error.message)}</p>`);
    }
}

export function bindAccountManagement(root, reload) {
  const host = root.querySelector('[data-account-management]');
  if (!host) return;
  if (mountedScan !== refScan) mountedScan?.reset();
  mountedHost = host;
  mountedScan = refScan;
  mountedRoute = location.hash;
  const params = new URLSearchParams(host.dataset.accountParams);
  const party = resourceId(host.dataset.party, {allowZero:true});
  const navigate = changes => {
    const route = accountNavigation(params, changes);
    if (route === location.hash) reload({restoreValues:false});
    else location.hash = route;
  };
  bindNamedChoice(host, 'party_id', {url:`${base}/account-party`, title:'选择个人范围',
    signal:readController?.signal, initialize:false, allowZero:true, zeroLabel:'全部个人',
    changed:row => navigate({party:row?.id || 0, account:0, unassigned:0, ref_page:1})});
  bindNamedChoice(host, 'account_id', {url:`${base}/account`, title:'选择集合范围',
    signal:readController?.signal, initialize:false, allowZero:true, zeroLabel:'全部集合',
    filter:party ? {key:'party_id', op:'=', val:party} : undefined,
    changed:row => navigate({party:row?.party_id || party, account:row?.id || 0, unassigned:0, ref_page:1})});
  const form = host.querySelector('[data-account-filter]');
  form.onsubmit = event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    navigate({...values, ref_page:1});
  };
  form.querySelectorAll('select').forEach(select => select.onchange = () => form.requestSubmit());
  host.querySelector('[data-account-clear-search]').onclick = () => navigate({word:'', ref_page:1});
  const renderList = result => {
    const list = host.querySelector('[data-account-list]');
    list.innerHTML = cardList(result, true);
    refScan?.bind(list, renderList);
  };
  refScan?.bind(host.querySelector('[data-account-list]'), renderList);
  host.onclick = async event => {
    const page = event.target.closest('[data-account-page]');
    if (page) { if (!page.disabled) navigate({ref_page:resourceId(page.dataset.accountPage)}); return; }
    const manage = event.target.closest('[data-account-manage]');
    if (manage) {
      const kind = manage.dataset.accountManage;
      const node = dialog(`管理${labels[kind]}`, `<p>${kind === 'account' && party ? esc(host.dataset.partyLabel) + '的集合' : '全部' + labels[kind]}；未拥有来源卡的对象也可维护。</p>
        <label>选择后的操作<select data-directory-action><option value="filter">筛选来源卡</option><option value="edit">维护资料</option></select></label><div data-account-directory></div>`);
      const controller = new AbortController();
      node.addEventListener('close', () => controller.abort(), {once:true});
      mountPicker(node.querySelector('[data-account-directory]'), {url:`${base}/${endpoint[kind]}`,
        searchKeys:['display_label'], describe:metadataLabel, signal:controller.signal,
        filter:kind === 'account' && party ? {key:'party_id', op:'=', val:party} : undefined,
        choose:row => {
          const edit = node.querySelector('[data-directory-action]').value === 'edit';
          node.close();
          if (edit) openAccountCommand(host, reload, kind, row.id);
          else navigate(kind === 'party' ? {party:row.id, account:0, unassigned:0, ref_page:1}
            : {party:row.party_id, account:row.id, unassigned:0, ref_page:1});
        }});
      return;
    }
    const button = event.target.closest('[data-account-create], [data-account-edit], [data-account-move]');
    if (!button || button.disabled) return;
    button.disabled = true;
    try {
      const moving = button.dataset.accountMove;
      await openAccountCommand(host, reload, moving ? 'ref' : button.dataset.accountCreate || button.dataset.accountEdit,
        moving || button.dataset.id, !!moving);
    } finally {if (button.isConnected) button.disabled = false;}
  };
}

import { request } from '../api/client.js';
import { esc, resourceId } from '../util/core.js';
import { dateTimeRangeControl } from './date-time-range.js';
import { namedChoice, bindNamedChoice, setNamedChoice, metadataLabel } from './workbench.js';

const scopes = [
  {name: 'party_id', label: '个人', url: '/paam/ledger/v1/account-party'},
  {name: 'account_id', label: '账户集合', url: '/paam/ledger/v1/account', zero: '已识别未分组'},
  {name: 'account_ref_id', label: '具体来源卡', url: '/paam/ledger/v1/account-ref', zero: '来源未识别'},
  {name: 'tag_id', label: '标签', url: '/paam/tag/v1/tag', flowOnly: true},
];
let readController;
let pendingNames = 0;

export function stopTransactionFilterRead() { readController?.abort(); }
export function transactionFilterReadBusy() { return !!pendingNames && !readController?.signal.aborted; }

// Validate URL IDs before either the financial list or name hydration request.
// Explicit zero has business meaning; it is never interchangeable with clear.
export function transactionScopeFilters(params, flow = false) {
  return scopes.filter(scope => flow || !scope.flowOnly).filter(scope => params.has(scope.name))
    .map(scope => ({key: scope.name, op: '=', val: resourceId(params.get(scope.name), {allowZero: !!scope.zero})}));
}

export function transactionFilter({params, flow = false, currency, sort, advanced = ''}) {
  const word = params.get('word')?.trim() || '';
  const direction = ({'1': 'IN', '2': 'OUT'})[params.get('cash_direction')] || params.get('cash_direction') || '';
  const searchField = params.get('search_field') || 'summary';
  const choices = scopes.filter(scope => flow || !scope.flowOnly);
  const identifiers = transactionScopeFilters(params, flow);
  const formName = flow ? 'economic-filter' : 'fact-filter';
  return `<form class="detail-filter transaction-filter" data-transaction-filter data-form="${formName}">
    <div class="transaction-filter-main">
      <div class="transaction-filter-search"><span>字面搜索</span><div class="transaction-search-inputs">
        <select name="search_field" aria-label="搜索字段"><option value="summary" ${searchField === 'summary' ? 'selected' : ''}>脱敏摘要</option><option value="${flow ? 'counterparty' : 'counterparty_name'}" ${searchField === (flow ? 'counterparty' : 'counterparty_name') ? 'selected' : ''}>脱敏交易对手</option></select>
        <input name="word" maxlength="128" value="${esc(word)}" autocomplete="off" aria-label="搜索文字" placeholder="搜索摘要或交易对手（字面匹配）">
      </div></div>
      ${dateTimeRangeControl(params.get('date_from') || '', params.get('date_to') || '')}
      <label>收支方向<select name="cash_direction"><option value="">全部方向</option>${[['IN','收入 / 流入'],['OUT','支出 / 流出']].map(([code, text]) => `<option value="${code}" ${direction === code ? 'selected' : ''}>${text}</option>`).join('')}</select></label>
      <div class="transaction-filter-actions"><button type="submit">查找</button><button type="button" class="quiet" data-action="detail-clear" data-page-id="${flow ? 'economy' : 'ledger'}">清空</button></div>
    </div>
    <div class="transaction-filter-secondary"><label>币种${currency}</label><label>排序${sort}</label><button type="button" data-filter-more-toggle aria-expanded="false" aria-controls="transaction-filter-more">高级条件</button></div>
    <details id="transaction-filter-more" data-filter-more><summary>账户、标签及其他条件</summary><div class="transaction-filter-advanced">${advanced}
      ${choices.map(scope => {
        const value = identifiers.find(item => item.key === scope.name)?.val ?? '';
        const text = value === 0 ? scope.zero : value ? `#${value}（正在读取名称）` : '不限';
        return namedChoice(scope.name, scope.label, {value, text, clear: '不限'})
          .replace('</div></div>', `${scope.zero ? `<button type="button" data-filter-zero="${scope.name}">${scope.zero}</button>` : ''}</div></div>`);
      }).join('')}
    </div></details><div class="transaction-filter-chips" data-filter-chips aria-label="已应用条件" aria-live="polite"></div>
  </form>`;
}

// Background refresh must not erase an unsubmitted query or fold the advanced
// panel while it is being used. This compares transport values, not labels.
export function transactionFilterDirty(form, params) {
  if (!form) return false;
  const data = new FormData(form);
  return [...data].some(([name, value]) => {
    const expected = name === 'sort' ? `${params.get('sort_field') || 'occurred_time'}.${params.get('sort_order') || 'desc'}`
      : name === 'search_field' ? params.get(name) || 'summary' : params.get(name) || '';
    const normalize = item => name === 'word' ? String(item).trim()
      : name.endsWith('currency_code') ? String(item).trim().toUpperCase()
      : name === 'cash_direction' ? ({'1': 'IN', '2': 'OUT'})[item] || item
      : /^date_(from|to)$/.test(name) && /^\d{4}-\d{2}-\d{2}$/.test(item)
        ? `${item}T${name === 'date_from' ? '00:00' : '23:59'}` : String(item);
    return normalize(value) !== normalize(expected);
  });
}

export function bindTransactionFilter(root) {
  const form = root.querySelector('[data-transaction-filter]');
  if (!form) return;
  stopTransactionFilterRead();
  const controller = readController = new AbortController();
  pendingNames = 0;
  const {signal} = controller;
  const live = () => form.isConnected && !signal.aborted;
  const more = form.querySelector('[data-filter-more]');
  const toggle = form.querySelector('[data-filter-more-toggle]');
  toggle.onclick = () => {more.open = !more.open;};
  more.addEventListener('toggle', () => toggle.setAttribute('aria-expanded', String(more.open)));
  const value = name => form.elements.namedItem(name)?.value || '';
  const selectedText = name => {
    const field = form.elements.namedItem(name);
    return field?.selectedOptions?.[0]?.textContent || value(name);
  };
  function paint() {
    if (!live()) return;
    const chips = [];
    const add = (name, label, text) => {if (value(name)) chips.push({name, text: `${label}：${text}`});};
    add('word', selectedText('search_field'), value('word'));
    add('cash_direction', '方向', selectedText('cash_direction'));
    const currencyName = form.elements.namedItem('cash_currency_code') ? 'cash_currency_code' : 'currency_code';
    add(currencyName, '币种', value(currencyName));
    if (value('date_from') || value('date_to')) chips.push({name: 'date_range', text: `时间：${value('date_from') || '不限'} → ${value('date_to') || '不限'}`});
    if (value('sort') !== 'occurred_time.desc') add('sort', '排序', selectedText('sort'));
    add('economic_type', '账本类型', selectedText('economic_type'));
    add('active', '有效状态', selectedText('active'));
    for (const scope of scopes) {
      const label = form.querySelector(`[data-named-choice="${scope.name}"] [data-choice-label]`);
      if (label) add(scope.name, scope.label, label.textContent);
    }
    const host = form.querySelector('[data-filter-chips]');
    host.hidden = !chips.length;
    host.innerHTML = chips.map(chip => `<button type="button" class="quiet" data-filter-chip="${chip.name}" aria-label="${esc(`移除条件 ${chip.text}`)}">${esc(chip.text)} <span aria-hidden="true">×</span></button>`).join('');
    host.querySelectorAll('[data-filter-chip]').forEach(button => button.onclick = () => {
      const name = button.dataset.filterChip;
      if (name === 'date_range') {
        form.elements.namedItem('date_from').value = '';
        form.elements.namedItem('date_to').value = '';
      } else if (form.querySelector(`[data-named-choice="${name}"]`)) {
        setNamedChoice(form, name, '', '不限', false);
      } else form.elements.namedItem(name).value = name === 'sort' ? 'occurred_time.desc' : '';
      form.requestSubmit();
    });
  }
  for (const scope of scopes) {
    if (!form.querySelector(`[data-named-choice="${scope.name}"]`)) continue;
    bindNamedChoice(form, scope.name, {url: scope.url, title: `选择${scope.label}条件`, signal,
      zeroLabel: '不限', initialize: false,
      describe: row => metadataLabel(row) + (scope.flowOnly ? ` · 维度${row.view_status}` : ''),
      changed: () => {paint(); form.requestSubmit();}});
    form.querySelector(`[data-filter-zero="${scope.name}"]`)?.addEventListener('click', () => {
      if (!live()) return;
      setNamedChoice(form, scope.name, 0, scope.zero, false);
      form.requestSubmit();
    });
    const initial = value(scope.name);
    if (initial && initial !== '0') {
      ++pendingNames;
      request(`${scope.url}/${resourceId(initial)}`, {signal}).then(row => {
        resourceId(row.id);
        if (!live() || value(scope.name) !== initial) return;
        if (String(row.id) !== initial) throw new Error('对象名称响应与所选ID不一致');
        setNamedChoice(form, scope.name, initial,
          metadataLabel(row) + (scope.flowOnly ? ` · 维度${row.view_status}` : ''), false);
        paint();
      }).catch(error => {
        if (!live() || error.name === 'AbortError' || value(scope.name) !== initial) return;
        setNamedChoice(form, scope.name, initial, `#${initial} 名称读取失败：${error.message}；可重新选择`, false);
        paint();
      }).finally(() => {
        if (readController === controller) --pendingNames;
      });
    }
  }
  paint();
}

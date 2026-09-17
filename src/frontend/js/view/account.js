import { esc, money, typeNames } from "../util/core.js";

export function monthBounds(cursor) {
  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  const from = `${year}-${String(month + 1).padStart(2, "0")}-01`;
  const last = new Date(year, month + 1, 0).getDate();
  const to = `${year}-${String(month + 1).padStart(2, "0")}-${String(last).padStart(2, "0")}`;
  return { year, month, from, to, last };
}

export function cursorFromParam(value, fallback) {
  if (!/^\d{4}-\d{2}$/.test(value || "")) return fallback;
  const [year, month] = value.split("-").map(Number);
  if (month < 1 || month > 12) return fallback;
  return new Date(year, month - 1, 1);
}

const amount = (value, scale, currency) => money({
  amount: value,
  amount_scale: scale,
  currency_code: currency,
});

function dayMap(summary, currency) {
  return new Map(summary.trend
    .filter((item) => item.currency_code === currency)
    .map((item) => [item.day, item]));
}

function chartMarkup(summary, currency, range) {
  const days = dayMap(summary, currency);
  const values = [...days.values()].flatMap((item) => [item.income_value, item.expense_value]);
  const maximum = Math.max(1, ...values);
  return Array.from({ length: range.last }, (_, index) => {
    const day = index + 1;
    const key = `${range.year}-${String(range.month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const item = days.get(key);
    const income = item?.income_value || 0;
    const expense = item?.expense_value || 0;
    const incomeHeight = income ? Math.max(4, Math.round(income / maximum * 100)) : 0;
    const expenseHeight = expense ? Math.max(4, Math.round(expense / maximum * 100)) : 0;
    return `<button type="button" class="day-bar" data-action="account-day" data-value="${key}" title="${key}：收入 ${income}，支出 ${expense}">
      <span class="day-bar-track"><i style="height:${incomeHeight}%"></i><i class="expense" style="height:${expenseHeight}%"></i></span>
      <small>${day}</small>
    </button>`;
  }).join("");
}

function calendarMarkup(summary, currency, range) {
  const days = dayMap(summary, currency);
  const leading = (new Date(range.year, range.month, 1).getDay() + 6) % 7;
  const blanks = Array.from({ length: leading }, () => '<span class="calendar-day outside"></span>');
  const cells = Array.from({ length: range.last }, (_, index) => {
    const day = index + 1;
    const key = `${range.year}-${String(range.month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const item = days.get(key);
    return `<button type="button" class="calendar-day${item ? "" : " empty"}" data-action="account-day" data-value="${key}">
      <span>${day}</span>
      ${item ? `<strong>＋${amount(item.income_value, item.amount_scale, currency)}</strong><small>−${amount(item.expense_value, item.amount_scale, currency)}</small><em>${item.net_value >= 0 ? "+" : ""}${amount(item.net_value, item.amount_scale, currency)}</em>` : ""}
    </button>`;
  });
  return [...blanks, ...cells].join("");
}

export function accountsMarkup({ summary, accounts, accountCode, currency, cursor }) {
  const range = monthBounds(cursor);
  const currencies = summary.totals.map((item) => item.currency_code);
  const selectedCurrency = currencies.includes(currency) ? currency : currencies[0] || "CNY";
  const total = summary.totals.find((item) => item.currency_code === selectedCurrency) || {
    amount_scale: 2,
    income_value: 0,
    expense_value: 0,
    refund_offset_value: 0,
    net_value: 0,
  };
  const accountOptions = accounts.map((item) => {
    const identity = item.identity || item.account_code || "UNKNOWN";
    const label = item.display_name || identity;
    return `<option value="${esc(identity)}" ${identity === accountCode ? "selected" : ""}>${esc(label)}</option>`;
  }).join("");
  const currencyOptions = [...new Set([selectedCurrency, ...currencies])]
    .map((item) => `<option value="${esc(item)}" ${item === selectedCurrency ? "selected" : ""}>${esc(item)}</option>`)
    .join("");
  const actualActivities = summary.activities
    .filter((item) => item.currency_code === selectedCurrency);
  const visibleActivities = [...actualActivities];
  const visibleTypes = new Set(visibleActivities.map((item) => item.entry_type_code));
  for (const entryTypeCode of ["INCOME_AND_EXPENSE", "INTERNAL_TRANSFER", "ASSET_AND_LIABILITY"]) {
    if (visibleActivities.length >= 3) break;
    if (visibleTypes.has(entryTypeCode)) continue;
    visibleActivities.push({
      entry_type_code: entryTypeCode,
      currency_code: selectedCurrency,
      amount_scale: total.amount_scale,
      in_amount_value: 0,
      out_amount_value: 0,
      nettable: true,
    });
  }
  const activityCards = visibleActivities
    .map((item) => `<button type="button" class="activity-card" data-action="account-type" data-value="${esc(item.entry_type_code)}">
      <header><span>${esc(typeNames[item.entry_type_code] || item.entry_type_code)}</span><small>单方向、单币种经济结果</small></header>
      <div><p><span>流入</span><strong>${amount(item.in_amount_value, item.amount_scale, selectedCurrency)}</strong></p><p><span>流出</span><strong>${amount(item.out_amount_value, item.amount_scale, selectedCurrency)}</strong></p></div>
      <footer>查看相关流水 →</footer>
    </button>`).join("");
  const activeDays = summary.trend.filter((item) => item.currency_code === selectedCurrency).length;

  return `<section class="account-dashboard">
    <form class="account-toolbar" data-form="account-filter">
      <label><span>账户</span><select name="account_code"><option value="">全部账户</option>${accountOptions}</select></label>
      <label><span>币种</span><select name="currency_code">${currencyOptions}</select></label>
      <div class="month-switcher" aria-label="月份选择">
        <button type="button" data-action="account-month" data-value="-1" aria-label="上个月">←</button>
        <strong>${range.year} 年 ${range.month + 1} 月</strong>
        <button type="button" data-action="account-month" data-value="1" aria-label="下个月">→</button>
        <button type="button" data-action="account-current">本月</button>
      </div>
      <span class="account-scope">${accountCode ? "单一账户" : "全部账户"} · ${esc(selectedCurrency)}</span>
    </form>
    <div class="month-metrics">
      <button type="button" data-action="account-metric" data-value="INCOME"><span>本月收入</span><strong class="income">${amount(total.income_value, total.amount_scale, selectedCurrency)}</strong><small>点击查看收入流水</small></button>
      <button type="button" data-action="account-metric" data-value="EXPENSE"><span>本月支出</span><strong class="expense">${amount(total.expense_value, total.amount_scale, selectedCurrency)}</strong><small>点击查看支出流水</small></button>
      <article><span>本月净收支</span><strong>${amount(total.net_value, total.amount_scale, selectedCurrency)}</strong><small>收入减支出并计入退款抵扣</small></article>
      <article><span>活跃天数</span><strong>${activeDays} 天</strong><small>${summary.entry_count} 条流水 · ${summary.provisional_count} 条待完善</small></article>
    </div>
    <section class="account-activities economic-activity-cards" aria-label="经济流水分类汇总"><div>${activityCards}</div></section>
    <div class="account-chart-grid">
      <article class="month-chart-card">
        <header><div><span class="eyebrow">DAILY CASH FLOW</span><h2>每日收支走势</h2></div><div class="chart-legend"><span class="income-dot">收入</span><span class="expense-dot">支出</span></div></header>
        <div class="month-bars">${chartMarkup(summary, selectedCurrency, range)}</div>
      </article>
      <aside class="account-note"><span class="eyebrow">当前口径</span><h3>已生效的经济流水</h3><p>收入与支出只统计事实交易；账户流转和债权关系分别展示，不做跨币种折算。</p><button type="button" data-action="account-drilldown">查看本月经济流水 →</button></aside>
    </div>
    <article class="calendar-card">
      <header><div><span class="eyebrow">CALENDAR</span><h2>每日收入 / 支出</h2></div><small>点击日期查看当天的经济流水。</small></header>
      <div class="calendar-weekdays" aria-hidden="true"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>
      <div class="calendar-grid">${calendarMarkup(summary, selectedCurrency, range)}</div>
    </article>
  </section>`;
}

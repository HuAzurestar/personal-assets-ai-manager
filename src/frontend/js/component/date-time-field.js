import { esc, localDateTime, selectedTimeZone, zonedISOString } from '../util/core.js';

// Store the chosen wall clock and its explicit zone independently of later
// changes to the page's display zone. Reuse the existing DST-safe conversion.
export function dateTimeField(name, label, instant = new Date(), zone = selectedTimeZone()) {
  const zones = [['Asia/Hong_Kong', '香港'], ['Asia/Shanghai', '上海'], ['Asia/Tokyo', '东京'],
    ['Europe/London', '伦敦'], ['America/New_York', '纽约'], ['UTC', 'UTC']];
  if (!zones.some(([value]) => value === zone)) zones.push([zone, zone]);
  return `<div class="actions"><label>${esc(label)}<input type="datetime-local" step="1" name="${name}" value="${esc(localDateTime(instant, zone))}" required></label><label>发生时区<select name="${name}_timezone">${zones.map(([value, title]) => `<option value="${esc(value)}" ${value === zone ? 'selected' : ''}>${esc(title)} · ${esc(value)}</option>`).join('')}</select></label></div><small>按所选时区解释；夏令时不存在或重叠的时间须明确改用 UTC 等唯一时间。</small>`;
}

export function dateTimeValue(node, name) {
  return zonedISOString(node.querySelector(`[name="${name}"]`).value, false,
    node.querySelector(`[name="${name}_timezone"]`).value);
}

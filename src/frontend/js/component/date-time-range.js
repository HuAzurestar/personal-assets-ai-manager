import { $, $$, esc } from "../util/core.js";

let openedControl = null;
let outsideHandler = null;

const pad = (value) => String(value).padStart(2, "0");
const today = () => {
  const value = new Date();
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
};

function normalize(value, endpoint) {
  const text = String(value || "").slice(0, 16);
  if (!text) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return `${text}T${endpoint === "start" ? "00:00" : "23:59"}`;
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text) ? text : "";
}

function parts(value, endpoint) {
  const normalized = normalize(value, endpoint);
  if (!normalized) return { date: "", hour: endpoint === "start" ? "00" : "23", minute: endpoint === "start" ? "00" : "59" };
  return { date: normalized.slice(0, 10), hour: normalized.slice(11, 13), minute: normalized.slice(14, 16) };
}

function formatValue(value, endpoint) {
  const selected = parts(value, endpoint);
  return selected.date ? `${selected.date} ${selected.hour}:${selected.minute}` : `选择${endpoint === "start" ? "开始" : "结束"}时间`;
}

function buttonText(start, end) {
  if (!start && !end) return "选择开始时间 → 结束时间";
  return `${formatValue(start, "start")} → ${formatValue(end, "end")}`;
}

export function dateTimeRangeControl(startValue = "", endValue = "") {
  const start = normalize(startValue, "start");
  const end = normalize(endValue, "end");
  return `<div class="detail-filter-field date-time-range-filter"><span>时间范围 <i data-range-hint>选择完整时间段</i></span><div class="date-time-range" data-date-time-range data-active="start" data-date-phase="start"><button type="button" class="date-time-range-trigger" data-action="range-open" aria-haspopup="dialog" aria-expanded="false">${esc(buttonText(start, end))}</button><input type="hidden" name="date_from" value="${esc(start)}"><input type="hidden" name="date_to" value="${esc(end)}"><div class="date-time-range-popover" data-range-popover role="dialog" aria-label="选择日期时间范围" hidden></div></div></div>`;
}

function monthDate(control) {
  const value = control.dataset.month || parts($("[name=date_from]", control).value, "start").date || today();
  const [year, month] = value.split("-").map(Number);
  return new Date(year, month - 1, 1);
}

function setMonth(control, date) {
  control.dataset.month = `${date.getFullYear()}-${pad(date.getMonth() + 1)}`;
}

function calendarDays(month) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const start = new Date(first);
  start.setDate(first.getDate() - first.getDay());
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(start);
    date.setDate(start.getDate() + index);
    return date;
  });
}

function options(count, selected) {
  return Array.from({ length: count }, (_, value) => {
    const text = pad(value);
    return `<option value="${text}" ${text === selected ? "selected" : ""}>${text}</option>`;
  }).join("");
}

function render(control, message = "") {
  const startInput = $("[name=date_from]", control);
  const endInput = $("[name=date_to]", control);
  const start = parts(startInput.value, "start");
  const end = parts(endInput.value, "end");
  const active = control.dataset.active || "start";
  const current = active === "start" ? start : end;
  const month = monthDate(control);
  const startDate = start.date;
  const endDate = end.date;
  const days = calendarDays(month).map((date) => {
    const value = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    const classes = [date.getMonth() === month.getMonth() ? "" : "outside"];
    if (value === startDate) classes.push("range-start");
    if (value === endDate) classes.push("range-end");
    if (startDate && endDate && value > startDate && value < endDate) classes.push("in-range");
    if (value === today()) classes.push("today");
    return `<button type="button" class="${classes.filter(Boolean).join(" ")}" data-range-date="${value}" aria-label="${value}">${date.getDate()}</button>`;
  }).join("");
  const popover = $("[data-range-popover]", control);
  popover.innerHTML = `<div class="range-calendar-pane"><header><button type="button" data-range-month="-1" aria-label="上个月">←</button><strong>${month.getFullYear()} 年 ${month.getMonth() + 1} 月</strong><button type="button" data-range-month="1" aria-label="下个月">→</button></header><div class="range-weekdays"><span>日</span><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span></div><div class="range-calendar-grid">${days}</div><div class="range-endpoints"><button type="button" data-range-endpoint="start" class="${active === "start" ? "active" : ""}"><span>开始</span><strong>${start.date || "未选择"}</strong><small>${start.date ? `${start.hour}:${start.minute}` : "--:--"}</small></button><button type="button" data-range-endpoint="end" class="${active === "end" ? "active" : ""}"><span>结束</span><strong>${end.date || "未选择"}</strong><small>${end.date ? `${end.hour}:${end.minute}` : "--:--"}</small></button></div><p class="range-message ${message ? "error" : ""}" data-range-message>${message || (active === "start" ? "正在设置开始时间" : "正在设置结束时间")}</p></div><aside class="range-time-pane"><div><span>小时</span><select size="7" data-range-time="hour" aria-label="${active === "start" ? "开始" : "结束"}小时">${options(24, current.hour)}</select></div><b>:</b><div><span>分钟</span><select size="7" data-range-time="minute" aria-label="${active === "start" ? "开始" : "结束"}分钟">${options(60, current.minute)}</select></div></aside><footer><button type="button" class="quiet" data-range-clear>清空</button><button type="button" class="quiet" data-range-today>今天</button><button type="button" class="primary" data-range-apply>应用时间范围</button></footer>`;
  $("[data-action=range-open]", control).textContent = buttonText(startInput.value, endInput.value);
  $$('[data-range-time] option:checked', popover).forEach((option) => option.scrollIntoView({ block: "center" }));
}

function position(control) {
  const popover = $("[data-range-popover]", control);
  const trigger = $("[data-action=range-open]", control);
  const box = trigger.getBoundingClientRect();
  const width = Math.min(680, window.innerWidth - 20);
  const left = Math.max(10, Math.min(box.left, window.innerWidth - width - 10));
  popover.style.width = `${width}px`;
  popover.style.left = `${left}px`;
  popover.style.top = `${Math.min(box.bottom + 6, window.innerHeight - popover.offsetHeight - 10)}px`;
}

function close(control) {
  if (!control) return;
  $("[data-range-popover]", control).hidden = true;
  $("[data-action=range-open]", control).setAttribute("aria-expanded", "false");
  if (openedControl === control) openedControl = null;
  if (outsideHandler) document.removeEventListener("pointerdown", outsideHandler, true);
  outsideHandler = null;
}

function setEndpointValue(control, endpoint, nextParts) {
  const input = $(`[name=date_${endpoint === "start" ? "from" : "to"}]`, control);
  input.value = nextParts.date ? `${nextParts.date}T${nextParts.hour}:${nextParts.minute}` : "";
}

function validate(control) {
  const start = $("[name=date_from]", control).value;
  const end = $("[name=date_to]", control).value;
  if (!start || !end) return "请选择完整的开始和结束时间";
  if (start > end) return "开始时间不能晚于结束时间";
  return "";
}

export function bindDateTimeRanges(root, onApply) {
  $$('[data-date-time-range]', root).forEach((control) => {
    const popover = $("[data-range-popover]", control);
    const trigger = $("[data-action=range-open]", control);
    render(control);
    trigger.onclick = () => {
      if (openedControl && openedControl !== control) close(openedControl);
      popover.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      openedControl = control;
      render(control);
      requestAnimationFrame(() => position(control));
      outsideHandler = (event) => {
        if (!control.contains(event.target)) close(control);
      };
      document.addEventListener("pointerdown", outsideHandler, true);
    };
    popover.onclick = (event) => {
      const monthButton = event.target.closest('[data-range-month]');
      if (monthButton) {
        const month = monthDate(control);
        month.setMonth(month.getMonth() + Number(monthButton.dataset.rangeMonth));
        setMonth(control, month);
        render(control);
        position(control);
        return;
      }
      const endpointButton = event.target.closest('[data-range-endpoint]');
      if (endpointButton) {
        control.dataset.active = endpointButton.dataset.rangeEndpoint;
        control.dataset.datePhase = endpointButton.dataset.rangeEndpoint;
        render(control);
        return;
      }
      const day = event.target.closest('[data-range-date]');
      if (day) {
        const endpoint = control.dataset.datePhase || "start";
        const current = parts($(`[name=date_${endpoint === "start" ? "from" : "to"}]`, control).value, endpoint);
        setEndpointValue(control, endpoint, { ...current, date: day.dataset.rangeDate });
        if (endpoint === "start") $("[name=date_to]", control).value = "";
        const message = validate(control);
        control.dataset.active = endpoint;
        control.dataset.datePhase = endpoint === "start" || message ? "end" : "start";
        render(control, message && $("[name=date_from]", control).value && $("[name=date_to]", control).value ? message : "");
        return;
      }
      if (event.target.closest('[data-range-clear]')) {
        $("[name=date_from]", control).value = "";
        $("[name=date_to]", control).value = "";
        control.dataset.active = "start";
        control.dataset.datePhase = "start";
        close(control);
        onApply?.(control.closest("form"));
        return;
      }
      if (event.target.closest('[data-range-today]')) {
        const endpoint = control.dataset.active || "start";
        const current = parts($(`[name=date_${endpoint === "start" ? "from" : "to"}]`, control).value, endpoint);
        setEndpointValue(control, endpoint, { ...current, date: today() });
        setMonth(control, new Date());
        render(control, validate(control));
        return;
      }
      if (event.target.closest('[data-range-apply]')) {
        const message = validate(control);
        if (message) {
          render(control, message);
          return;
        }
        close(control);
        onApply?.(control.closest("form"));
      }
    };
    popover.onchange = (event) => {
      const select = event.target.closest('[data-range-time]');
      if (!select) return;
      const endpoint = control.dataset.active || "start";
      const input = $(`[name=date_${endpoint === "start" ? "from" : "to"}]`, control);
      const current = parts(input.value, endpoint);
      if (!current.date) current.date = today();
      current[select.dataset.rangeTime] = select.value;
      setEndpointValue(control, endpoint, current);
      render(control, validate(control));
    };
  });
}

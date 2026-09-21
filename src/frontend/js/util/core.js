export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

export const key = () => crypto.randomUUID();

const DEFAULT_TIME_ZONE = "Asia/Hong_Kong";
const TIME_ZONE_STORAGE_KEY = "paam.timezone";

function validTimeZone(value) {
  try {
    new Intl.DateTimeFormat("en", { timeZone: value }).format();
    return true;
  } catch {
    return false;
  }
}

export function selectedTimeZone() {
  const stored = globalThis.localStorage?.getItem(TIME_ZONE_STORAGE_KEY);
  return stored && validTimeZone(stored) ? stored : DEFAULT_TIME_ZONE;
}

export function setSelectedTimeZone(value) {
  if (!validTimeZone(value)) throw new Error(`不支持的时区：${value}`);
  globalThis.localStorage?.setItem(TIME_ZONE_STORAGE_KEY, value);
}

function wallClockParts(timestamp, timeZone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(timestamp));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return ["year", "month", "day", "hour", "minute", "second"].map((key) => Number(values[key]));
}

export function selectedCalendarDate(value = new Date()) {
  const [year, month, day] = wallClockParts(value, selectedTimeZone());
  const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  return { year, month, day, iso };
}

export function zonedISOString(value, exclusiveEnd = false) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?$/);
  if (!match) throw new Error(`无效的本地日期时间：${value}`);
  const dateOnly = match[4] === undefined;
  let wallTimestamp = Date.UTC(
    Number(match[1]), Number(match[2]) - 1, Number(match[3]),
    Number(match[4] || 0), Number(match[5] || 0), Number(match[6] || 0),
  );
  if (exclusiveEnd) wallTimestamp += dateOnly ? 86_400_000 : 60_000;
  const target = new Date(wallTimestamp);
  const desired = Date.UTC(
    target.getUTCFullYear(), target.getUTCMonth(), target.getUTCDate(),
    target.getUTCHours(), target.getUTCMinutes(), target.getUTCSeconds(),
  );
  const timeZone = selectedTimeZone();
  const wallClockValue = (instant) => {
    const parts = wallClockParts(instant, timeZone);
    return Date.UTC(parts[0], parts[1] - 1, parts[2], parts[3], parts[4], parts[5]);
  };
  const offsets = new Set();
  for (let hours = -36; hours <= 36; hours += 6) {
    const sample = desired + hours * 3_600_000;
    offsets.add(wallClockValue(sample) - sample);
  }
  const matches = [...new Set(
    [...offsets]
      .map((offset) => desired - offset)
      .filter((instant) => wallClockValue(instant) === desired),
  )];
  if (!matches.length) throw new Error(`本地时间在 ${timeZone} 不存在：${value}`);
  if (matches.length > 1) throw new Error(`本地时间在 ${timeZone} 不唯一：${value}`);
  return new Date(matches[0]).toISOString();
}

export function date(value) {
  if (!value) return "未提供";
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(text)) return "时间缺少时区";
  const parsed = new Date(text);
  if (!Number.isFinite(parsed.getTime())) return text;
  return new Intl.DateTimeFormat("sv-SE", { timeZone: selectedTimeZone(), year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(parsed);
}

export const typeNames = {
  0: "收入与支出", 1: "内部转账", 2: "资产与负债",
  TRANSACTION: "收入与支出", ACCOUNT_TRANSFER: "内部转账", CLAIM: "资产与负债",
  INCOME_AND_EXPENSE: "收入与支出", INTERNAL_TRANSFER: "内部转账", ASSET_AND_LIABILITY: "资产与负债",
};

export const reviewTypeNames = {
  TRANSACTION: "事实交易",
  BORROW_AND_REPAY: "借款与还款",
  ACCOUNT: "账户修正",
  FACT_CONFLICT: "事实冲突",
};

export const roleNames = {};

export const statusNames = {
  0: "已确认", 1: "已撤销",
  PENDING: "待确认", CONFIRMED: "已确认", REVOKED: "已撤销",
  REJECTED: "已忽略", DEFAULT: "默认", COMPLETE: "完整",
  PARTIAL: "部分", CONFLICT: "冲突", ACTIVE: "启用中", ARCHIVED: "已归档",
};

const currencyPrecisions = {
  CNY: 2, EUR: 2, GBP: 2, HKD: 2, JPY: 0, USD: 2,
};

export function currencyPrecision(value) {
  const code = String(value || "").trim().toUpperCase();
  const match = code.match(/^([A-Z][A-Z0-9]{1,11}?)(?:_([0-8]))?$/);
  if (!match || !(match[1] in currencyPrecisions)) throw new Error(`不支持的币种单位：${value}`);
  return match[2] === undefined ? currencyPrecisions[match[1]] : Number(match[2]);
}

export function decimalAmount(value, currencyCode) {
  const text = String(value || "").trim();
  if (!text) return null;
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error(`金额格式不正确：${text}`);
  const precision = currencyPrecision(currencyCode);
  const [whole, decimal = ""] = text.split(".");
  if (decimal.length > precision) throw new Error(`${currencyCode} 金额最多允许 ${precision} 位小数`);
  const result = Number(`${whole}${decimal.padEnd(precision, "0")}`);
  if (!Number.isSafeInteger(result) || result <= 0) throw new Error("金额超出可处理范围");
  return result;
}

export function money(item) {
  if (!item) return "—";
  const code = String(item.currency_code || "").toUpperCase();
  const precision = currencyPrecision(code);
  const value = Number(item.amount) / (10 ** precision);
  const baseCurrency = code.split("_", 1)[0];
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: baseCurrency,
      minimumFractionDigits: precision,
      maximumFractionDigits: precision,
    }).format(value);
  } catch {
    return `${value.toFixed(precision)} ${code}`;
  }
}

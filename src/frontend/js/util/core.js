export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

export const key = () => crypto.randomUUID();
export const date = (value) => String(value || "").replace("T", " ").slice(0, 16);

export const typeNames = {
  0: "收入与支出", 1: "内部转账", 2: "资产与负债",
  TRANSACTION: "事实交易", ACCOUNT_TRANSFER: "账户流转", CLAIM: "债权关系",
  INCOME_AND_EXPENSE: "收入与支出", INTERNAL_TRANSFER: "内部转账", ASSET_AND_LIABILITY: "资产与负债",
  INCOME: "收入", EXPENSE: "支出", AA: "AA", LOAN_BORROW: "借入",
  LOAN_LEND: "借出", REFUND: "退款", TRANSFER: "转账",
  FX_EXCHANGE: "换汇", UNRESOLVED: "待核验",
};

export const reviewTypeNames = {
  CLASSIFICATION: "确认普通收支",
  AA: "AA 分摊",
  LOAN_BORROW: "借入与还款",
  LOAN_LEND: "借出与收回",
  REFUND: "退款关联",
  TRANSFER: "本人账户转账",
  FX_EXCHANGE: "换汇",
  DUPLICATE: "重复交易",
  TAG: "标签修改",
  ACCOUNT: "账户修正",
  FACT_CONFLICT: "事实冲突",
};

export const roleNames = {
  CLASSIFIED_INCOME: "确认为普通收入",
  CLASSIFIED_EXPENSE: "确认为普通支出",
  AA_PAID: "我先支付", AA_RECEIVED: "收到分摊",
  LOAN_RECEIVED: "收到借款", LOAN_REPAID: "偿还借款",
  LOAN_LENT: "借出款项", LOAN_RECOVERED: "收回借款",
  REFUND_RECEIVED: "收到退款", REFUND_EXPENSE: "原支出",
  TRANSFER_OUT: "转出", TRANSFER_IN: "转入", TRANSFER_FEE: "手续费",
  FX_OUT: "换出", FX_IN: "换入", FX_FEE: "换汇费用",
  DUPLICATE_RETAINED: "保留", DUPLICATE_EXCLUDED: "排除",
};

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

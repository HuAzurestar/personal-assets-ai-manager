export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

export const key = () => crypto.randomUUID();
export const date = (value) => String(value || "").replace("T", " ").slice(0, 16);

export const typeNames = {
  INCOME_AND_EXPENSE: "收入与支出", INTERNAL_TRANSFER: "内部转账", ASSET_AND_LIABILITY: "资产与负债",
  INCOME: "收入", EXPENSE: "支出", AA: "AA", LOAN_BORROW: "借入",
  LOAN_LEND: "借出", REFUND: "退款", TRANSFER: "转账",
  FX_EXCHANGE: "换汇", UNRESOLVED: "待核验",
};

export const statusNames = {
  0: "已确认", 1: "已撤销",
  PENDING: "待确认", CONFIRMED: "已确认", REVOKED: "已撤销",
  REJECTED: "已忽略", DEFAULT: "默认", COMPLETE: "完整",
  PARTIAL: "部分", CONFLICT: "冲突", ACTIVE: "启用中", ARCHIVED: "已归档",
};

export function money(item) {
  if (!item) return "—";
  const value = Number(item.amount_value) / (10 ** Number(item.amount_scale));
  try {
    return new Intl.NumberFormat("zh-CN", {
      style: "currency",
      currency: item.currency_code,
      maximumFractionDigits: item.amount_scale,
    }).format(value);
  } catch {
    return `${value.toFixed(item.amount_scale)} ${item.currency_code}`;
  }
}

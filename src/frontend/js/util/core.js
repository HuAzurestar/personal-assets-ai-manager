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

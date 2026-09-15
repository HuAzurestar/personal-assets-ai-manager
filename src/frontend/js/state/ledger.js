export const now = new Date();
export const state = {
  page: "ledger",
  params: new URLSearchParams(),
  importPlan: null,
  renderVersion: 0,
  historyFilterTimer: null,
  historyRequestController: null,
  historyRequestVersion: 0,
  historyAccountNames: new Map(),
  detailFacts: new Map(),
  detailEconomics: new Map(),
  detailEconomicReviews: new Map(),
  detailSummaries: new Map(),
  detailTagViews: new Map(),
  accountMonth: new Date(now.getFullYear(), now.getMonth(), 1),
};

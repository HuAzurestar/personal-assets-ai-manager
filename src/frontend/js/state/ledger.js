export const state = {
  page: "ledger",
  params: new URLSearchParams(),
  importPlan: null,
  renderVersion: 0,
  historyFilterTimer: null,
  historyRequestController: null,
  historyRequestVersion: 0,
  detailEconomics: new Map(),
  detailEconomicReviews: new Map(),
  detailSummaries: new Map(),
  accountMonth: null,
};

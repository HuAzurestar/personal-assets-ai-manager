import { request, jsonRequest, isUnknownWrite } from "../api/client.js";
import { esc, date, resourceId } from "../util/core.js";
import { table } from "../component/table.js";

const base = "/paam/ledger/v1";
const endpoint = { party: "account-party", account: "account", ref: "account-ref" };
const labels = { party: "个人", account: "管理集合", ref: "具体来源卡" };
let readController;

export function stopAccountRead() { readController?.abort(); }

const input = (name, label, value = "", extra = "") => `<label>${label}<input name="${name}" value="${esc(value)}" ${extra}></label>`;

function navigation(params, changes) {
  const next = new URLSearchParams(params);
  Object.entries(changes).forEach(([key, value]) => next.set(key, value));
  return `#workbench/account?${next}`;
}

function pageControls(kind, result, params) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  return `<div class="actions"><a href="${esc(navigation(params, { [`${kind}_page`]: Math.max(1, result.page_index - 1) }))}">上一页</a>
    <span>${result.page_index} / ${pages}，共 ${result.total} 项</span>
    <a href="${esc(navigation(params, { [`${kind}_page`]: Math.min(pages, result.page_index + 1) }))}">下一页</a></div>`;
}

export async function accountManagementPage(params) {
  stopAccountRead();
  readController = new AbortController();
  const partyId = resourceId(params.get("party") || 0, { allowZero: true }), accountId = resourceId(params.get("account") || 0, { allowZero: true });
  const pages = await Promise.all(Object.keys(endpoint).map(async (kind) => {
    const query = new URLSearchParams({ page_size: "20", page_index: params.get(`${kind}_page`) || "1" });
    if (kind === "account" && partyId) query.set("filter", JSON.stringify({ key: "party_id", op: "=", val: partyId }));
    if (kind === "ref" && params.get("unassigned") === "1") query.set("filter", JSON.stringify({ key: "account_id", op: "=", val: 0 }));
    else if (kind === "ref" && accountId) query.set("filter", JSON.stringify({ key: "account_id", op: "=", val: accountId }));
    return [kind, await request(`${base}/${endpoint[kind]}/list?${query}`, { signal: readController.signal })];
  }));
  const sections = pages.map(([kind, result]) => {
    const rows = result.items.map((row) => {
      const select = kind === "party" ? `<a href="${esc(navigation(params, { party: row.id, account: 0, account_page: 1, ref_page: 1, unassigned: 0 }))}">查看集合</a>`
        : kind === "account" ? `<a href="${esc(navigation(params, { account: row.id, ref_page: 1, unassigned: 0 }))}">查看具体卡</a>` : "";
      const detail = kind === "ref" ? `${esc(row.institution)} ${esc(row.reference)} · ${esc(row.identity_strength)}<br>
        来源：${esc(row.source_namespace || "未知")} ${esc(row.source_identity)}<br>最近来源入库：${row.latest_source_time ? esc(date(row.latest_source_time)) : "未知"}`
        : kind === "account" ? `个人 #${row.party_id} · 账单 / 快照提醒：${row.statement_interval_months || "手动"} / ${row.snapshot_interval_months || "手动"}` : "";
      return `<tr><td>#${row.id} ${esc(row.name || "未命名来源")}</td><td>${esc(row.status)}</td><td>${detail}</td>
        <td>${select} <button data-account-edit="${kind}" data-id="${row.id}">维护</button>
        ${kind === "ref" ? `<button data-account-move="${row.id}">预览归属变更</button>` : ""}</td></tr>`;
    });
    return `<section class="panel"><h2>${labels[kind]}</h2><button data-account-create="${kind}" ${kind === "account" && !partyId ? "disabled" : ""}>新建${labels[kind]}</button>
      ${table(["身份", "状态", "资料 / 来源", "操作"], rows)}${pageControls(kind, result, params)}</section>`;
  }).join("");
  return `<div data-account-management data-party="${partyId}" data-account="${accountId}"><h1>个人与账户来源</h1>
    <p>个人 #${partyId || "未筛选"} → 集合 #${accountId || "未筛选"} → 具体来源卡。余额与资产负债对象分开管理；关闭卡不删除历史现金。</p>
    <p><a href="#workbench/account">全部</a> · <a href="#workbench/account?unassigned=1">来源已识别但未分组</a> · <a href="#workbench/position">资产负债对象</a></p>
    <p>来源身份、用户登记账号和交易订单号不是同一个字段。只显示遮罩账号；维护提醒默认手动。</p>${sections}</div>`;
}

function dialog(title, body) {
  const node = document.createElement("dialog");
  node.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button type="button" data-account-close>关闭</button></div><div class="dialog-body">${body}</div>`;
  node.querySelector("[data-account-close]").onclick = () => node.close();
  node.addEventListener("close", () => node.remove());
  document.body.append(node);
  node.showModal();
  return node;
}

function writeError(node, error) {
  const uncertain = isUnknownWrite(error);
  node.dataset.writeOutcome = uncertain ? "UNKNOWN" : "NOT_COMMITTED";
  node.querySelector("[role=status]").textContent = uncertain
    ? "提交结果未知。先关闭窗口并查询当前对象 / 列表，不要重发创建或变更。"
    : error.code === "WRITE_BUSY" ? "本次未提交，输入已保留。稍后重新读取或预览，再由你提交；不会自动重发。"
    : `${error.code || "操作失败"}：${error.message}`;
  node.querySelectorAll("[type=submit]").forEach((button) => { button.disabled = uncertain; });
}

export function bindAccountManagement(root, reload) {
  const host = root.querySelector("[data-account-management]");
  if (!host) return;
  host.onclick = async (event) => {
    const button = event.target.closest("[data-account-create], [data-account-edit], [data-account-move]");
    if (!button || button.disabled) return;
    button.disabled = true;
    const route = location.hash;
    try {
      const moving = button.dataset.accountMove;
      const kind = moving ? "ref" : button.dataset.accountCreate || button.dataset.accountEdit;
      const rawId = moving || button.dataset.id;
      const id = rawId ? resourceId(rawId) : null;
      const row = id ? await request(`${base}/${endpoint[kind]}/${id}`, { signal: readController?.signal }) : {};
      if (!host.isConnected || route !== location.hash) return;
      if (moving) {
        const node = dialog("归属变更预览", `<form class="stack">${input("account_id", "目标管理集合 ID（0 为未分组）", host.dataset.account, 'type="number" min="0" required')}
          <p>可以从账户页查找集合 ID。这里只移动卡的元数据归属，不修改 Review 或 Ledger。</p><p role="status"></p><div data-impact></div>
          <button type="submit">预览影响</button><button type="button" data-confirm disabled>确认当前预览</button></form>`);
        let plan, change, generation = 0;
        node.querySelector("input").oninput = () => { ++generation; plan = null; node.querySelector("[data-confirm]").disabled = true; };
        node.querySelector("form").onsubmit = async (event) => {
          event.preventDefault();
          const submit = node.querySelector("[type=submit]");
          submit.disabled = true;
          const issued = ++generation;
          try {
            change = { account_id: resourceId(node.querySelector("input").value, { allowZero: true }), expected_updated_time: row.updated_time };
            const nextPlan = await jsonRequest(`${base}/account-ref/${id}/move-preview`, "POST", change);
            if (!node.isConnected || issued !== generation) return;
            plan = nextPlan;
            node.querySelector("[data-impact]").textContent = `个人 #${plan.from_party_id} → #${plan.to_party_id}；集合 #${plan.from_account_id} → #${plan.to_account_id}；影响 ${plan.affected_ledger_count} 笔历史流水的当前筛选归属。${plan.cross_party ? "注意：跨个人变更，请核对。" : ""}`;
            node.querySelector("[data-confirm]").disabled = false;
          } catch (error) { writeError(node, error); }
          finally { submit.disabled = false; }
        };
        node.querySelector("[data-confirm]").onclick = async () => {
          if (!plan || node.dataset.writeOutcome === "UNKNOWN") return;
          node.querySelector("[data-confirm]").disabled = true;
          node.querySelector("[type=submit]").disabled = true;
          try {
            await jsonRequest(`${base}/account-ref/${id}/move-command`, "POST", { ...change, preview_digest: plan.preview_digest });
            node.close(); await reload();
          } catch (error) { plan = null; writeError(node, error); }
        };
        return;
      }
      const fields = input("name", "名称", row.name || "", `maxlength="120" ${kind === "ref" ? "" : "required"}`)
        + (kind === "ref" ? input("institution", "机构", row.institution || "", 'maxlength="120"') + input("reference", "用户登记本方账号（与来源身份不同）", row.reference || "", 'maxlength="200" autocomplete="off"') : "")
        + (kind === "account" ? input("statement_interval_months", "账单提醒月数（0 为手动）", row.statement_interval_months || 0, 'type="number" min="0" max="120" required')
          + input("snapshot_interval_months", "余额快照提醒月数（0 为手动）", row.snapshot_interval_months || 0, 'type="number" min="0" max="120" required') : "")
        + (id ? `<label>状态<select name="status"><option value="ACTIVE" ${row.status === "ACTIVE" ? "selected" : ""}>ACTIVE</option><option value="CLOSED" ${row.status === "CLOSED" ? "selected" : ""}>CLOSED（历史现金仍计）</option></select></label>` : "");
      const node = dialog(`${id ? "维护" : "新建"}${labels[kind]}`, `<form class="stack">${fields}<p role="status"></p><button type="submit">保存</button></form>`);
      node.querySelector("form").onsubmit = async (event) => {
        event.preventDefault();
        if (node.dataset.writeOutcome === "UNKNOWN") return;
        node.querySelector("[type=submit]").disabled = true;
        try {
          const values = Object.fromEntries(new FormData(event.target));
          if (kind === "account") {
            values.statement_interval_months = Number(values.statement_interval_months);
            values.snapshot_interval_months = Number(values.snapshot_interval_months);
            if (!id) values.party_id = resourceId(host.dataset.party);
          }
          if (kind === "ref") values.account_id = resourceId(id ? row.account_id : host.dataset.account, { allowZero: true });
          if (id) values.expected_updated_time = row.updated_time;
          await jsonRequest(`${base}/${endpoint[kind]}${id ? `/${id}/metadata` : ""}`, id ? "PUT" : "POST", values);
          node.close(); await reload();
        } catch (error) { writeError(node, error); }
      };
    } catch (error) {
      if (error.name !== "AbortError" && host.isConnected && route === location.hash)
        dialog("读取失败", `<p role="status">${esc(error.message)}</p>`);
    }
    finally { if (button.isConnected) button.disabled = false; }
  };
}

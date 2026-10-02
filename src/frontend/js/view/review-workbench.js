import { request, jsonRequest } from "../api/client.js";
import { esc, date, money, quantityDecimal, quantityAmount, typeNames, reviewTypeNames } from "../util/core.js";
import { input, select, workbenchDialog, writeFailure, mountPicker } from "../component/workbench.js";
import { positionFields, bindPartyPicker } from "./position.js";

const base = "/paam/ledger/v1/review";
const cases = [["NORMAL", "普通收支"], ["REFUND", "退款"], ["SHARED_PAYMENT", "共同费用 / AA"], ["INTERNAL_TRANSFER", "真实内部转账"], ["BORROW_REPAY", "借出、借入、收回、偿还"], ["DUPLICATE", "同一交易的重复证据"], ["POS_OPENING", "对象期初数量"], ["POS_POSITION_OPEN", "对象增加（可无现金）"], ["POS_POSITION_SETTLE", "对象减少（显式来源）"], ["POS_CREDIT_PURCHASE", "信用消费"], ["POS_CREDIT_REPAY", "信用还本"]];
let controller;
export function stopReviewRead() { controller?.abort(); }
const ids = values => values.length ? values.map(id => `#${id}`).join("、") : "无";

function previewMarkup(plan, facts = new Map(), positions = new Map()) {
  return `<h3>服务端预览（尚未发布）</h3><p>整体停用冲突 Review：${ids(plan.impact.conflicting_review_ids)}；恢复原始系统默认：${ids(plan.impact.restored_default_review_ids)}</p>
    <p>账户来源：${ids(plan.impact.affected_account_ref_ids)}；对象：${ids(plan.impact.affected_position_ids)}；可能失效的后续腿：${ids(plan.impact.dependent_position_leg_ids)}</p>
    <p>标签影响 Ledger：${ids(plan.impact.tag_ledger_ids)}；涉及 ${(plan.tag_effect.affected_views || []).length} 个视图、${(plan.tag_effect.affected_rule_ids || []).length} 条规则。旧标签保留，异步请求随账务状态失效。</p>
    <p>将失效的建议：${plan.tag_effect.invalidated_request_count ?? 0}；扫描状态：${esc(plan.tag_effect.scan_state || "NOT_NEEDED")}。只有完整含义相同且新旧各唯一的输出延续标签。</p>
    ${(plan.tag_effect.mappings || []).length ? `<section class="panel" data-tag-mappings><h4>新旧标签对照</h4>
      ${(plan.tag_effect.mappings || []).slice(0, 100).map(mapping => `<p>旧 Ledger ${mapping.old_ledger_id ? `#${mapping.old_ledger_id}` : "无唯一来源"} → ${mapping.new_output ? `新解释 ${mapping.new_output.review_index + 1} / 现金行 ${mapping.new_output.allocation_index + 1}` : "保留停用原项"} · View #${mapping.view_id} / Tag #${mapping.tag_id} · ${esc({ KEEP: "延续已生效值", REVIEW_REQUIRED: "默认值，待人工核对", RETAIN_INACTIVE: "原项标签不改" }[mapping.disposition] || mapping.disposition)}</p>`).join("")}
      ${(plan.tag_effect.mappings || []).length > 100 ? `<p>显示前100项，共${plan.tag_effect.mappings.length}项；服务端确认覆盖整个原子组。</p>` : ""}</section>` : ""}
    ${plan.blocking_issues.map(issue => `<p class="error">${esc(issue.code)}：${esc(issue.message)}</p>`).join("")}
    ${plan.new_reviews.map(row => `<section class="panel"><h4>${esc(row.case_code)} → ${esc(reviewTypeNames[row.type] || row.type)} · ${esc(row.title)}</h4>
      ${row.allocations.map(allocation => {
        const fact = facts.get(allocation.transaction_id);
        return `<p>Fact #${allocation.transaction_id} → ${esc(typeNames[allocation.economic_type])} · ${fact ? esc(money({ cash_amount: allocation.cash_amount, cash_currency_code: fact.cash_currency_code })) : `${allocation.cash_amount} 最小单位`} · 来源卡 #${allocation.account_ref_id}${fact ? ` · ${esc(fact.cash_direction)} · ${esc(date(fact.occurred_time))}` : ""}</p>`;
      }).join("")}
      ${row.new_positions.map((position, index) => `<p>新对象 ${index + 1}：${esc(position.title)} · ${esc(position.type)} · ${esc(position.usage_scenario)} · 个人 #${position.party_id} · ${esc(position.counterparty)} · ${esc(position.unit_code)}</p>`).join("")}
      ${row.legs.map((leg, index) => {
        const position = leg.existing_position_id ? positions.get(leg.existing_position_id) : row.new_positions[leg.new_position_index];
        return `<p>数量腿 ${index + 1} → ${leg.existing_position_id ? `对象 #${leg.existing_position_id}` : `新对象 ${leg.new_position_index + 1}`}：${esc(leg.type)} · ${esc(leg.leg_direction)} ${position ? `${quantityDecimal(leg.leg_amount, position.unit_code)} ${esc(position.unit_code)}` : `${leg.leg_amount} 最小量`} · 来源腿 #${leg.source} · ${esc(date(leg.occurred_time))} · ${esc(leg.basis)}</p>`;
      }).join("")}
      ${row.position_allocations.map(link => `<p>现金行 ${link.allocation_index + 1} → 数量腿 ${link.leg_index + 1}：${esc(money(link))}（款项归因，不是额外现金）</p>`).join("")}</section>`).join("")}
    ${plan.position_changes.map(change => `<p>对象 ${change.position_id ? `#${change.position_id}` : `新对象 ${change.new_position_index + 1}`}：${esc(change.before.quantity_state)} ${change.before.quantity ?? "—"} → ${esc(change.after.quantity_state)} ${change.after.quantity ?? "—"}（单位最小量；来源失效不能当作有据数量）</p>`).join("")}
    ${plan.coverage.map(row => `<p>Fact #${row.transaction_id}：事实 ${row.cash_amount}／生效覆盖 ${row.effective_cash_amount}（同币种最小单位）</p>`).join("")}`;
}

// Preview and command share exactly the same frozen intent. Editing cancels
// the approval; command retries are never automatic, even after a lost reply.
function bindPublication(form, build, facts, completed, positions = new Map()) {
  let generation = 0, plan, frozen, writing = false, uncertain = false, previewing = false;
  const previewButton = form.querySelector("[data-review-preview]");
  const submit = form.querySelector("[data-review-command]");
  const status = form.querySelector("[data-review-status]");
  const invalidate = () => {
    ++generation; plan = null; frozen = null; submit.disabled = true;
    status.textContent = uncertain ? "提交结果未知，请查询当前对象；不得重发。" : "输入已变更，需要重新预览。";
  };
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  const setEditing = disabled => form.querySelectorAll("input, textarea, select, button").forEach(node => { node.disabled = disabled; });
  previewButton.onclick = async () => {
    if (writing || uncertain || previewing) return;
    const issued = ++generation; plan = null; submit.disabled = true; previewing = true; previewButton.disabled = true;
    try {
      const intent = build();
      const next = await jsonRequest(`${base}/preview`, "POST", intent);
      if (!form.isConnected || issued !== generation) return;
      plan = next; frozen = intent;
      form.querySelector("[data-review-impact]").innerHTML = previewMarkup(next, facts, positions);
      status.textContent = next.blocking_issues.length ? "有阻塞问题，不能提交。" : "请核对现金、数量、整体冲突和默认恢复后确认。";
      submit.disabled = !!next.blocking_issues.length;
    } catch (error) { if (form.isConnected && issued === generation) status.textContent = `${error.code || "预览失败"}：${error.message}`; }
    finally { previewing = false; if (form.isConnected) previewButton.disabled = uncertain; }
  };
  form.onsubmit = async event => {
    event.preventDefault();
    if (!plan || !frozen || writing || uncertain || submit.disabled) return;
    writing = true; setEditing(true);
    try {
      const result = await jsonRequest(`${base}/command`, "POST", { ...frozen, expected_reviews: plan.expected_reviews, preview_digest: plan.preview_digest });
      plan = null; frozen = null;
      status.textContent = "发布成功。正在读取当前对象…";
      await completed(result);
    } catch (error) {
      plan = null; frozen = null;
      uncertain = writeFailure({ querySelector: () => status }, error);
      if (!uncertain) { setEditing(false); submit.disabled = true; }
    } finally { writing = false; }
  };
  return invalidate;
}

export async function transitionReview(id, activate, reload) {
  const row = await request(`${base}/${id}`);
  const node = workbenchDialog(`${activate ? "激活" : "停用"}原 Review #${id}`, `<form class="stack"><p>仅改变原组状态；原现金、数量腿和关系 ID 保留。服务端将核对全部直接冲突、原始默认恢复和来源依赖。</p><p role="status" data-review-status></p><div data-review-impact></div><button type="button" data-review-preview>预览整体影响</button><button type="submit" data-review-command disabled>确认当前预览</button></form>`);
  bindPublication(node.querySelector("form"), () => ({
    [activate ? "activate_review_ids" : "deactivate_review_ids"]: [Number(id)],
  }), new Map(), async () => { node.close(); await reload(); }, new Map(row.positions.map(position => [position.id, position])));
}

export async function mountReviewWorkbench(root, params, completed) {
  stopReviewRead(); controller = new AbortController();
  const signal = controller.signal, route = location.hash;
  const host = root.querySelector("[data-review-workflow]");
  if (!host) return;
  const facts = new Map(), selected = new Set(), positions = new Map();
  host.innerHTML = `<section class="panel"><h1>不可变账务审查</h1><p>Fact 不可改；每个所选 Fact 必须完整解释。现金与对象数量分区，停用保留原始内容，不造剩余默认项、不按同名猜债。</p>
    <a href="#workbench/position">查询对象与显式来源腿</a> · <a href="#workbench/account">查询来源卡</a>
    <form class="stack" data-immutable-review>${select("case_code", "业务场景", cases, params.get("case_code") || "NORMAL")}${input("title", "审查说明", "", 'maxlength="160"')}
      ${select("phase", "共同费用阶段（只用于 AA）", [["ADVANCE_OUT", "我垫付：资产增加 / 现金流出"], ["COLLECT_IN", "我收回：资产减少 / 现金流入"], ["RECEIVE_IN", "他人垫付：负债增加 / 现金流入"], ["PAY_OUT", "我偿还：负债减少 / 现金流出"]], "ADVANCE_OUT")}
      <h2>1. 现金事实与完整分配</h2><div data-fact-picker></div><p data-selected-facts>未选择现金 Fact（纯数量场景允许为空）。</p><div data-cash-rows></div><button type="button" data-add-cash>增加现金拆分</button>
      <h2>2. 独立对象与数量</h2><p>可选已有对象，或在本次发布中创建新对象。OUT 必须指定该对象原始 IN 来源腿；不会自动 FIFO。</p>
      <div class="actions">${input("position_id", "已有对象 ID", params.get("position") || "", 'type="number" min="1"')}<button type="button" data-load-position>读取对象</button><button type="button" data-find-position>分页查找对象</button><button type="button" data-new-position>本次发布新建对象</button></div>
      <div data-position-choices></div><div data-position-drafts></div><div data-leg-rows></div><button type="button" data-add-leg>增加数量腿</button>
      <h2>3. 现金 → 数量腿的款项归因</h2><p>必须显式选择现金拆分行和数量腿。金额来自现金币种；第二段不新增现金。</p><div data-link-rows></div><button type="button" data-add-link>增加款项归因</button>
      <h2>4. 重复证据（仅显式 A / B）</h2><p>B 不再计现金，原始证据保留；A 与 B 必须已核对不同来源卡、同金额、币种、方向和时间。</p><div data-duplicate-rows></div><button type="button" data-add-duplicate>增加重复证据 B → 保留 A</button>
      <p role="status" data-review-status></p><div data-review-impact></div><div class="actions"><button type="button" data-review-preview>服务端预览完整变更</button><button type="submit" data-review-command disabled>确认当前预览并发布</button></div></form></section>`;
  const form = host.querySelector("form"), cashRoot = form.querySelector("[data-cash-rows]"), legRoot = form.querySelector("[data-leg-rows]"), draftRoot = form.querySelector("[data-position-drafts]"), linkRoot = form.querySelector("[data-link-rows]"), duplicateRoot = form.querySelector("[data-duplicate-rows]");
  const value = (node, name) => node.querySelector(`[name="${name}"]`).value;
  const nodes = (node, selector) => [...node.querySelectorAll(selector)];
  const fail = error => { form.querySelector("[data-review-status]").textContent = `${error.code || "输入错误"}：${error.message}`; };
  const article = (parent, kind, content) => {
    const node = document.createElement("article"); node.className = "panel stack"; node.dataset[kind] = "";
    node.innerHTML = `${content}<button type="button" data-remove>移除此行</button>`;
    node.querySelector("[data-remove]").onclick = () => {
      if (kind === "positionDraft" && nodes(legRoot, '[name="target"]').some(target => target.value.startsWith("new:")))
        return fail(new Error("先移除引用新对象的数量腿，再移除新对象；不能让行号变化静默改绑对象"));
      if (["cashRow", "legRow"].includes(kind)) {
        linkRoot.replaceChildren();
        form.querySelector("[data-review-status]").textContent = "现金／数量行结构改变，已清除款项归因，请按当前行号重新明确关联。";
      }
      node.remove();
      nodes(cashRoot, "[data-cash-row]").forEach((row, index) => { row.querySelector("h3").textContent = `现金拆分行 ${index + 1} · Fact #${value(row, "transaction_id")}`; });
      nodes(legRoot, "[data-leg-row]").forEach((row, index) => { row.querySelector("h3").textContent = `数量腿 ${index + 1}`; });
      invalidate();
    };
    parent.append(node); invalidate(); return node;
  };
  const refreshPositionChoices = () => {
    form.querySelector("[data-position-choices]").textContent = [...positions.values()].map(row => `#${row.id} ${row.title} / ${row.type} / ${row.unit_code} / ${row.status}`).join("；") || "尚未载入已有对象";
  };
  const addCash = (fact, amount = fact.cash_amount) => {
    const index = nodes(cashRoot, "[data-cash-row]").length + 1;
    const defaultType = ["BORROW_REPAY", "SHARED_PAYMENT", "POS_CREDIT_REPAY"].includes(form.elements.case_code.value) ? "ASSET_LIABILITY" : form.elements.case_code.value === "INTERNAL_TRANSFER" ? "ACCOUNT_TRANSFER" : form.elements.case_code.value === "DUPLICATE" ? "DUPLICATE" : "TRANSACTION";
const row = article(cashRoot, "cashRow", `<h3>现金拆分行 ${index} · Fact #${fact.transaction_id}</h3><p>${esc(fact.summary)} · ${esc(fact.cash_direction)} · ${esc(date(fact.occurred_time))} · ${esc(fact.cash_currency_code)}（方向／币种／时间不可改）</p>
      <input type="hidden" name="transaction_id" value="${fact.transaction_id}">${select("economic_type", "经济分类", Object.keys(typeNames).filter(key => ["TRANSACTION", "ACCOUNT_TRANSFER", "ASSET_LIABILITY", "DUPLICATE"].includes(key)).map(key => [key, typeNames[key]]), defaultType)}
      ${input("cash_amount", "分配金额", quantityDecimal(amount, fact.cash_currency_code), 'inputmode="decimal" required')}${input("account_ref_id", "具体本方来源卡 ID（0 表示未知）", fact.account_ref_id, 'type="number" min="0" required')}<button type="button" data-pick-ref>分页选择来源卡</button>`);
    row.querySelector("[data-pick-ref]").onclick = () => {
      const node = workbenchDialog("选择具体来源卡", '<div data-ref-picker></div>');
      mountPicker(node.querySelector("[data-ref-picker]"), { url: "/paam/ledger/v1/account-ref", signal,
        describe: item => `#${item.id} ${item.name} ${item.reference} / ${item.source_identity} / ${item.status}`,
        choose: item => { row.querySelector('[name="account_ref_id"]').value = item.id; invalidate(); node.close(); },
      });
    };
  };
  const readPosition = async id => {
    const row = await request(`/paam/financial/v1/position/${id}`, { signal });
    if (!host.isConnected || route !== location.hash) return;
    positions.set(row.id, row); refreshPositionChoices(); invalidate(); return row;
  };
  const build = () => {
    const allocations = nodes(cashRoot, "[data-cash-row]").map(row => {
      const transaction_id = Number(value(row, "transaction_id")), fact = facts.get(transaction_id);
      return { transaction_id, economic_type: value(row, "economic_type"), cash_amount: quantityAmount(value(row, "cash_amount"), fact.cash_currency_code), account_ref_id: Number(value(row, "account_ref_id")) };
    });
    for (const id of selected) {
      const sum = allocations.filter(row => row.transaction_id === id).reduce((sum, row) => sum + row.cash_amount, 0);
      if (!Number.isSafeInteger(sum) || sum !== facts.get(id).cash_amount) throw new Error(`Fact #${id} 需要完整解释：${sum} / ${facts.get(id).cash_amount} 最小单位`);
    }
    const new_positions = nodes(draftRoot, "[data-position-draft]").map(row => Object.fromEntries(["title", "description", "type", "usage_scenario", "counterparty", "unit_code"].map(key => [key, key === "unit_code" ? value(row, key).trim().toUpperCase() : value(row, key)]).concat([["party_id", Number(value(row, "party_id"))]])));
    const legs = nodes(legRoot, "[data-leg-row]").map(row => {
      const target = value(row, "target"), newTarget = target.startsWith("new:"), targetId = Number(target.split(":")[1]);
      const position = newTarget ? new_positions[targetId] : positions.get(targetId);
      if (!position) throw new Error("数量腿需先载入明确对象；新对象行号改变后请重新选择");
      const occurred = value(row, "occurred_time");
      if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(occurred) || !Number.isFinite(Date.parse(occurred))) throw new Error("数量发生时间需填写带时区 ISO 时间");
      return { [newTarget ? "new_position_index" : "existing_position_id"]: targetId, type: value(row, "type"), leg_amount: quantityAmount(value(row, "leg_amount"), position.unit_code), leg_direction: value(row, "leg_direction"), occurred_time: occurred, source: Number(value(row, "source")), basis: value(row, "basis") };
    });
    const position_allocations = nodes(linkRoot, "[data-link-row]").map(row => {
      const allocation_index = Number(value(row, "allocation_index")) - 1, leg_index = Number(value(row, "leg_index")) - 1;
      if (!allocations[allocation_index] || !legs[leg_index]) throw new Error("款项归因引用不存在的现金行或数量腿，请核对当前行号");
      const cash_currency_code = facts.get(allocations[allocation_index].transaction_id).cash_currency_code;
      return { allocation_index, leg_index, cash_currency_code, cash_amount: quantityAmount(value(row, "cash_amount"), cash_currency_code) };
    });
    const duplicate_transactions = nodes(duplicateRoot, "[data-duplicate-row]").map(row => ({ transaction_id: Number(value(row, "transaction_id")), kept_transaction_id: Number(value(row, "kept_transaction_id")) }));
    const account_bindings = nodes(duplicateRoot, "[data-duplicate-row]").map(row => ({ transaction_id: Number(value(row, "transaction_id")), account_ref_id: Number(value(row, "account_ref_id")) }));
    const case_code = form.elements.case_code.value;
    const core = { allocations, new_positions, legs, position_allocations };
    const review = { case_code, title: form.elements.title.value, account_bindings, duplicate_transactions,
      ...(case_code.startsWith("POS_") ? core : { parameters: { ...core, ...(case_code === "SHARED_PAYMENT" ? { phase: form.elements.phase.value } : {}) } }) };
    return { new_reviews: [review] };
  };
  const invalidate = bindPublication(form, build, facts, completed, positions);
  form.querySelector("[data-add-cash]").onclick = () => {
    const fact = facts.get([...selected][0]); if (!fact) return fail(new Error("先选择现金 Fact；纯数量场景无需现金行"));
    if (selected.size === 1) return addCash(fact);
    const node = workbenchDialog("选择要增加拆分的现金 Fact", `${select("fact", "明确 Fact", [...selected].map(id => [id, `#${id} ${facts.get(id).summary}`]), fact.transaction_id)}<button type="button" data-add-selected>增加拆分</button>`);
    node.querySelector("[data-add-selected]").onclick = () => { addCash(facts.get(Number(value(node, "fact")))); node.close(); };
  };
  form.querySelector("[data-load-position]").onclick = () => readPosition(Number(form.elements.position_id.value)).catch(fail);
  form.querySelector("[data-find-position]").onclick = () => {
    const node = workbenchDialog("分页选择对象", '<div data-position-picker></div>');
    mountPicker(node.querySelector("[data-position-picker]"), { url: "/paam/financial/v1/position", searchKeys: ["title"], signal,
      describe: row => `#${row.id} ${row.title} / ${row.type} / ${row.unit_code} / ${row.status}`,
      choose: row => { readPosition(row.id).then(() => node.close()).catch(fail); },
    });
  };
  form.querySelector("[data-new-position]").onclick = () => {
    const row = article(draftRoot, "positionDraft", `<h3>本次新对象 ${nodes(draftRoot, "[data-position-draft]").length + 1}</h3>${positionFields()}`);
    bindPartyPicker(row, signal);
  };
  form.querySelector("[data-add-leg]").onclick = () => {
    const targets = [...positions.values()].map(row => [`existing:${row.id}`, `#${row.id} ${row.title} (${row.unit_code})`]).concat(nodes(draftRoot, "[data-position-draft]").map((row, index) => [`new:${index}`, `新对象 ${index + 1} ${value(row, "title")} (${value(row, "unit_code")})`]));
    if (!targets.length) return fail(new Error("先读取已有对象或填写本次新对象"));
    const opening = form.elements.case_code.value === "POS_OPENING";
    const row = article(legRoot, "legRow", `<h3>数量腿 ${nodes(legRoot, "[data-leg-row]").length + 1}</h3>${select("target", "明确对象", targets, targets[0][0])}${select("type", "数量证据类型", ["MOVEMENT", "OPENING"], opening ? "OPENING" : "MOVEMENT")}${select("leg_direction", "数量方向", ["IN", "OUT"], form.elements.case_code.value.includes("SETTLE") || form.elements.case_code.value === "POS_CREDIT_REPAY" ? "OUT" : "IN")}
      ${input("leg_amount", "精确数量（按对象单位）", "", 'inputmode="decimal" required')}${input("occurred_time", "带时区发生时间", new Date().toISOString(), 'required')}${input("source", "原始 IN 来源腿 ID（IN 为 0；OUT 必填正数）", 0, 'type="number" min="0" required')}<button type="button" data-find-source>分页查找原始来源腿</button>${input("basis", "数量依据 / 第三人代还说明", "", 'maxlength="2000"')}`);
    row.querySelector('[name="target"]').onchange = () => { row.querySelector('[name="source"]').value = 0; invalidate(); };
    row.querySelector("[data-find-source]").onclick = () => {
      const target = value(row, "target");
      if (!target.startsWith("existing:")) return fail(new Error("新对象没有已发布来源腿；OUT 需选择已有对象"));
      const id = Number(target.split(":")[1]);
      const node = workbenchDialog(`对象 #${id} 的原始来源腿`, '<div data-source-picker></div>');
      mountPicker(node.querySelector("[data-source-picker]"), { url: `/paam/financial/v1/position/${id}/leg`, signal,
        describe: leg => `Leg #${leg.id} / ${leg.leg_direction} ${quantityDecimal(leg.leg_amount, leg.unit_code)} ${leg.unit_code} / Review #${leg.review_id} ${leg.review.status} / ${date(leg.occurred_time)}`,
        choose: leg => {
          if (leg.leg_direction !== "IN" || leg.review.status !== "CONFIRMED") return fail(new Error("只能选择该对象当前有效的原始 IN 来源腿"));
          row.querySelector('[name="source"]').value = leg.id; invalidate(); node.close();
        },
      });
    };
  };
  form.querySelector("[data-add-link]").onclick = () => article(linkRoot, "linkRow", `${input("allocation_index", "当前现金拆分行号（从 1 起）", 1, 'type="number" min="1" required')}${input("leg_index", "当前数量腿行号（从 1 起）", 1, 'type="number" min="1" required')}${input("cash_amount", "归因现金金额（币种取自现金行）", "", 'inputmode="decimal" required')}`);
  form.querySelector("[data-add-duplicate]").onclick = () => article(duplicateRoot, "duplicateRow", `${input("transaction_id", "重复证据 B 的 Fact ID", "", 'type="number" min="1" required')}${input("kept_transaction_id", "保留计现金 A 的 Fact ID", "", 'type="number" min="1" required')}${input("account_ref_id", "明确 B 来源卡 ID", "", 'type="number" min="1" required')}`);
  const preselected = new Set((params.get("facts") || "").split(",").filter(Boolean).map(Number));
  const choose = fact => {
    const id = fact.transaction_id;
    if (selected.has(id)) {
      selected.delete(id); nodes(cashRoot, "[data-cash-row]").filter(row => Number(value(row, "transaction_id")) === id).forEach(row => row.remove());
      linkRoot.replaceChildren();
      nodes(cashRoot, "[data-cash-row]").forEach((row, index) => { row.querySelector("h3").textContent = `现金拆分行 ${index + 1} · Fact #${value(row, "transaction_id")}`; });
    } else { facts.set(id, fact); selected.add(id); addCash(fact); }
    form.querySelector("[data-selected-facts]").textContent = `选择 ${selected.size} 个完整 Fact：${ids([...selected])}`; invalidate();
  };
  await mountPicker(form.querySelector("[data-fact-picker]"), { url: "/paam/ledger/v1/candidate", searchKeys: ["summary"], signal,
    describe: fact => `Fact #${fact.transaction_id} ${fact.summary} / ${money(fact)} ${fact.cash_direction} / 当前覆盖 ${fact.coverage.state} / 默认身份 ${fact.coverage.default_identity_state}`,
    selected: fact => selected.has(fact.transaction_id), choose,
  });
  for (const id of preselected) {
    const page = await request(`/paam/ledger/v1/candidate/list?${new URLSearchParams({ page_size: "20", filter: JSON.stringify({ key: "id", op: "=", val: id }) })}`, { signal });
    if (!host.isConnected || route !== location.hash) return;
    if (page.items[0]) choose(page.items[0]);
  }
  if (params.get("position")) await readPosition(Number(params.get("position")));
}

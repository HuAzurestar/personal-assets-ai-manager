import { request, jsonRequest } from "../api/client.js";
import { esc, date } from "../util/core.js";

const base = "/paam/system/v1/ai";
let snapshot = null;
const boundRoots = new WeakSet();
const states = { DRAFT: "草稿", PRODUCTION: "生产", RETIRED: "历史", STARTED: "调用中", SUCCEEDED: "成功", REJECTED: "校验拒绝", ERROR: "失败" };
const count = (value) => value < 0 ? "未知" : String(value);

function pageNumber(params, name) {
  const value = Number(params.get(name) || 1);
  return Number.isSafeInteger(value) && value > 0 ? value : 1;
}

function pager(result, name, params) {
  const pages = Math.max(1, Math.ceil(result.total / result.page_size));
  const link = (page, text) => {
    const query = new URLSearchParams(params);
    query.set(name, String(page));
    return `<a href="#settings/ai?${esc(query.toString())}">${text}</a>`;
  };
  return `<div class="automation-actions">${result.page_index > 1 ? link(result.page_index - 1, "上一页") : ""}
    <span>第 ${result.page_index} / ${pages} 页 · 共 ${result.total} 条</span>
    ${result.page_index < pages ? link(result.page_index + 1, "下一页") : ""}</div>`;
}

export async function aiPage(params = new URLSearchParams()) {
  const [tasks, prompts, runs, usage, schedule] = await Promise.all([
    request(`${base}/task/list?page_index=${pageNumber(params, "task_page")}`),
    request(`${base}/prompt/list?page_index=${pageNumber(params, "prompt_page")}`),
    request(`${base}/invocation/list?page_index=${pageNumber(params, "run_page")}`),
    request(`${base}/usage`),
    request("/paam/system/v1/schedule/status"),
  ]);
  snapshot = { tasks, prompts, runs, usage, schedule };
  return `<div class="automation-page" data-ai-page>
    <section class="automation-section">
      <div class="automation-section-head"><div><h2>AI 管理</h2><p>管理任务与 Prompt 版本，查看调用结果和用量。</p></div><button data-ai-action="refresh">刷新</button></div>
      <div class="automation-grid">
        <article class="automation-card"><h3>累计调用</h3><p>${usage.calls} 次 · ${usage.succeeded} 次成功</p></article>
        <article class="automation-card"><h3>已报告 token</h3><p>${usage.reported_total_tokens} · ${usage.calls_with_usage}/${usage.calls} 次调用有用量</p></article>
        <article class="automation-card"><h3>已知估算费用（USD）</h3><p>${usage.calls_with_cost ? esc(usage.estimated_cost_usd) : "未知"} · ${usage.calls_with_cost}/${usage.calls} 次调用有估算</p></article>
      </div><p>统计涵盖中间件启用后的全部记录；缺失用量不会按零计，估算费用不代表供应商账单。</p>
    </section>
    <section class="automation-section">
      <div class="automation-section-head"><h3>任务</h3><a href="#settings/automation">模型连接与披露设置</a></div>
      <div class="automation-grid">${tasks.items.map(task => `<article class="automation-card"><h3>${esc(task.title)}</h3><p>${esc(task.key)} · 任务 v${task.version}</p><p>生产 Prompt v${task.production_version}</p><button data-ai-action="new" data-key="${esc(task.key)}">创建 Prompt 版本</button></article>`).join("") || "<p>尚未注册任务。</p>"}</div>
      ${pager(tasks, "task_page", params)}
    </section>
    <section class="automation-section">
      <div class="automation-section-head"><h3>Prompt 版本</h3><p>保存为草稿后预览；发布时切换生产版本并作废相关旧建议。</p></div>
      <div class="automation-grid">${prompts.items.map(prompt => `<article class="automation-card"><h3>${esc(prompt.prompt_key)} · v${prompt.version}</h3><p>${states[prompt.state]} · ${esc(prompt.note)}</p><pre>${esc(prompt.instruction)}</pre><div class="automation-actions"><button data-ai-action="edit" data-id="${prompt.id}">复制为新版本</button><button data-ai-action="preview" data-id="${prompt.id}">虚构样例预览</button>${prompt.state !== "PRODUCTION" ? `<button data-ai-action="publish" data-id="${prompt.id}">${prompt.state === "RETIRED" ? "恢复此版本" : "发布"}</button>` : ""}</div></article>`).join("") || "<p>尚未保存版本。</p>"}</div>
      ${pager(prompts, "prompt_page", params)}
    </section>
    <section class="automation-section">
      <div class="automation-section-head"><h3>调用记录</h3><p>只展示调用元数据；正文保留在本地审计中。</p></div>
      <div class="table-wrap"><table><thead><tr><th>时间 / 调用</th><th>任务 / Prompt</th><th>模型</th><th>结果</th><th>输入 → 输出 token</th><th>耗时</th><th>估算 USD</th></tr></thead><tbody>
        ${runs.items.map(run => `<tr><td>${esc(date(run.created_time))}<br>#${run.id} · ${esc(run.run_id || "独立调用")}</td><td>${esc(run.task_key)} v${run.task_version}<br>Prompt v${run.prompt_version} · 尝试 ${run.attempt}</td><td>${esc(run.model_name)}</td><td>${states[run.status]}<br>${esc(run.error_code || run.result_code)}</td><td>${count(run.input_tokens)} → ${count(run.output_tokens)}</td><td>${run.status === "STARTED" ? "处理中" : `${run.latency_ms} ms`}</td><td>${esc(run.cost_usd || "未知")}</td></tr>`).join("") || '<tr><td colspan="7">暂无调用记录。</td></tr>'}
      </tbody></table></div>${pager(runs, "run_page", params)}
    </section>
    <section class="automation-section">
      <div class="automation-section-head"><h3>自动化与后台任务</h3><div class="automation-actions"><a href="#details/auto-rule">管理自动规则</a><a href="#settings/automation">开关、披露与调度诊断</a></div></div>
      <p>调度器：${schedule.scheduler_state === "RUNNING" ? "运行中" : "已停止"} · worker：${esc(schedule.worker_state)}</p>
      ${schedule.tasks.map(job => `<p>${esc(job.display_name || job.task_key)} · ${esc(job.queue_state)} · 最近结果 ${esc(job.last_result || "暂无")}</p>`).join("") || "<p>暂无注册任务。</p>"}
    </section>
  </div>`;
}

function dialog(title, markup) {
  const element = document.createElement("dialog");
  element.className = "wide";
  element.dataset.aiDialog = "";
  element.innerHTML = `<div class="dialog-head"><h2>${esc(title)}</h2><button data-ai-close>关闭</button></div><div class="dialog-body">${markup}<p data-ai-error role="alert"></p></div>`;
  element.querySelector("[data-ai-close]").addEventListener("click", () => element.close());
  element.addEventListener("close", () => element.remove());
  document.body.append(element);
  element.showModal();
  return element;
}

function editor(taskKey, prompt, rerender) {
  const element = dialog("创建 Prompt 新版本", `<form><p>${esc(taskKey)} · 隐私约束和输出合同由任务强制执行。</p><label>任务指引<textarea name="instruction" rows="8" maxlength="16000" required>${esc(prompt?.instruction || "")}</textarea></label><label>版本说明<input name="note" maxlength="1000"></label><button type="submit" class="primary">保存草稿</button></form>`);
  element.querySelector("form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await jsonRequest(`${base}/prompt`, "POST", { prompt_key: taskKey, instruction: form.elements.instruction.value, note: form.elements.note.value });
      element.close();
      await rerender();
    } catch (error) { element.querySelector("[data-ai-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
}

function preview(prompt) {
  const element = dialog(`Prompt v${prompt.version} · 虚构预览`, `<label>固定样例<select><option value="MEAL_SMALL">日常餐饮</option><option value="MEAL_LARGE">高额聚餐</option><option value="NON_MEAL_SMALL">文具</option><option value="NO_CONTEXT">无业务语义</option></select></label><p>只渲染已保存的 Prompt 与披露配置，不调用模型。</p><button data-preview>渲染样例</button><div data-messages></div>`);
  element.querySelector("[data-preview]").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const value = await jsonRequest(`${base}/prompt/${prompt.id}/preview`, "POST", { sample: element.querySelector("select").value });
      element.querySelector("[data-messages]").innerHTML = value.messages.map(message => `<h3>${esc(message.role)}</h3><pre>${esc(message.content)}</pre>`).join("") || "<p>清洗后没有业务语义，不会发起调用。</p>";
      element.querySelector("[data-ai-error]").textContent = "";
    } catch (error) { element.querySelector("[data-ai-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
  element.querySelector("[data-preview]").click();
}

async function publish(prompt, rerender) {
  // Read current production independently of task-list pagination.
  let task;
  let page = 1;
  let current;
  do {
    current = await request(`${base}/task/list?page_size=100&page_index=${page++}`);
    task = current.items.find(item => item.prompt_key === prompt.prompt_key);
  } while (!task && current.items.length && page <= Math.ceil(current.total / current.page_size));
  if (!task) throw new Error("未找到任务，请刷新后重试。");
  const element = dialog("切换生产 Prompt", `<p>${esc(prompt.prompt_key)}：v${task.production_version} → v${prompt.version}</p><pre>${esc(prompt.instruction)}</pre><p>相关规则会重新扫描；待审查旧建议会作废，执行中的旧结果将无法提交。</p><button class="primary" data-publish>发布 v${prompt.version}</button>`);
  element.querySelector("[data-publish]").addEventListener("click", async event => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await jsonRequest(`${base}/prompt/${prompt.id}/publish`, "POST", { expected_updated_time: prompt.updated_time, expected_production_version: task.production_version });
      element.close();
      await rerender();
    } catch (error) { element.querySelector("[data-ai-error]").textContent = error.message; }
    finally { button.disabled = false; }
  });
}

export function bindAi(root, rerender, notify) {
  if (boundRoots.has(root)) return;
  boundRoots.add(root);
  root.addEventListener("click", async event => {
    const button = event.target.closest("[data-ai-action]");
    if (!button || !root.contains(button) || !snapshot) return;
    const prompt = snapshot.prompts.items.find(item => item.id === Number(button.dataset.id));
    try {
      switch (button.dataset.aiAction) {
        case "refresh": await rerender(); break;
        case "new": editor(button.dataset.key, null, rerender); break;
        case "edit": editor(prompt.prompt_key, prompt, rerender); break;
        case "preview": preview(prompt); break;
        case "publish": await publish(prompt, rerender); break;
      }
    } catch (error) { notify(error.message); }
  });
}

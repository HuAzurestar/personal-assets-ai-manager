"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const context = { AbortController, URLSearchParams, console,
  esc: (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])),
  money: (value) => `${value.currency_code} ${value.amount}`,
};
vm.createContext(context);
for (const file of ["util/visible_poll.js", "view/disclosure.js", "view/automation_feedback.js"]) {
  vm.runInContext(fs.readFileSync(path.resolve(__dirname, `../frontend/js/${file}`), "utf8")
    .replace(/^import .*;\r?\n/gm, "").replace(/^export /gm, ""), context);
}
const parse = context.parseDisclosure;
assert.equal(parse('{"CNY":[0,3000]}', "DAY").amount_bands.CNY.boundaries[0], 0);
assert.equal(parse('{"CNY_4":[0,9000000000000]}', "NONE").date_granularity, "NONE");
for (const invalid of ['null', '[]', '{"XYZ":[0]}', '{"CNY_9":[0]}', '{"CNY":[false,1]}', '{"CNY":[0,"1"]}', '{"CNY":[0,1.1]}', '{"CNY":[0,1,1]}', '{"CNY":[1]}', '{"CNY":[0,9000000000001]}']) assert.throws(() => parse(invalid, "DAY"));
assert.deepEqual(Object.keys(parse('{}', "DAY").amount_bands), []);
const counters = { analyzed_count: "9223372036854775807", failed_count: "0", suggested_count: "24", accepted_count: "6", rejected_count: "2",
  execution_success_count: "9223372036854775807", execution_success_rate: 1, decision_count: "8", acceptance_rate: .75 };
const stats = context.ruleStatistics(counters);
assert.match(stats, /9223372036854775807/);
assert.doesNotMatch(stats, /9223372036854776000/);
assert.match(stats, /75.0%（6 \/ 8 次人工决定）/);
assert.match(stats, /不是模型准确率/);
assert.match(context.ruleStatistics({ ...counters, execution_success_rate: null, acceptance_rate: null }), /暂无样本/);
assert.equal(context.selectionConflict([{ ledger_id: 1, view_id: 1 }, { ledger_id: 1, view_id: 1 }]), true);
assert.equal(context.selectionConflict([{ ledger_id: 1, view_id: 1 }, { ledger_id: 1, view_id: 2 }]), false);
const results = context.batchResults({ items: [{ request_id: 1, result: "APPROVED" }, { request_id: 2, result: "RULE_STALE" }, { request_id: 3, result: "ALREADY_APPROVED" }] }, [1, 2, 3, 4], "approve");
assert.equal(results.filter((item) => item.category === "success").length, 1);
assert.equal(results[3].result, "UNKNOWN");
const resultMarkup = context.batchResultMarkup(results);
assert.match(resultMarkup, /成功 1 · 已处理 1 · 失败\/未知 2/);
assert.match(resultMarkup, /Request #2：规则版本已变化/);
assert.equal(context.batchResults({ items: [{ id: 7, status: 3 }] }, [7], "reject")[0].result, "REJECTED");
for (const [code, status] of [["TAG_REQUEST_NOT_FOUND", 404], ["TAG_REQUEST_NOT_PENDING", 409], ["TAG_REQUEST_STALE", 409], ["TAG_REQUEST_SCOPE_CONFLICT", 409]]) {
  const failure = context.batchFailureMarkup({ code, status, message: "<script>private details</script>" }, [1, 2, 3]);
  assert.match(failure, /整批未提交/);
  assert.match(failure, /本次选择：#1、#2、#3/);
  assert.match(failure, /未获得逐项结果/);
  assert.doesNotMatch(failure, /Request #|规则版本已变化|private|<script>|成功 \d|失败\/未知/);
}
for (const error of [new Error("network private details"), { status: 500, code: "TAG_REQUEST_NOT_FOUND" }, { status: 409, code: "UNKNOWN" }, { code: "__proto__" }]) {
  const failure = context.batchFailureMarkup(error, [1]);
  assert.match(failure, /提交结果未知/);
  assert.match(failure, /可能已经执行/);
  assert.doesNotMatch(failure, /整批未提交|Request #|private/);
}
assert.match(context.batchFailureMarkup(null, ["<script>"]), /&lt;script&gt;/);
for (const scenario of ["PARTIAL", "MANUAL", "QUEUE", "EMPTY", "ZERO", "LARGE", "OFFLINE"]) {
  assert.match(context.interactionMarkup(scenario), /虚构交互演示/);
  assert.match(context.interactionMarkup(scenario), /M2-CORE/);
}
const preview = context.previewDisclosureMarkup({ input_eligible: true,
  sample: { label: "fictional", amount: 2900, currency_code: "CNY", merchant: "<script>", summary: "safe" }, warnings: ["<img>"],
  messages: [{ role: "system", content: "<script>secret</script>" }, { role: "user", content: '{"amount_band":"[0,3000)"}' }] });
assert.doesNotMatch(preview, /<script>|<img>/);
assert.match(preview, /没有调用模型/);
assert.match(context.runtimeMarkup(null), /状态未知/);
const runtime = context.runtimeMarkup({ scheduler_state: "RUNNING", worker_state: "HEALTHY", tasks: [
  { task_key: "a", queue_state: "RUNNING" }, { task_key: "b", queue_state: "QUEUED", queue_position: 1 },
] });
assert.match(runtime, /等待 1 · 运行 1/);
const persistedDiagnostics = context.diagnosticsMarkup({ page_index: 2, page_size: 10, total: 12, items: [{
  time: "2026-09-27T00:00:00Z", run_id: "a".repeat(32), task_key: "tag-scan:3", phase: "CALL",
  code: "OUTPUT_SEMANTIC_INVALID", detail_code: "ITEM_MISMATCH", ledger_id: 12, attempt: 1,
  safe_message: "临时代号不匹配 <script>",
}] });
assert.match(persistedDiagnostics, /第 2\/2 页/);
assert.match(persistedDiagnostics, /ITEM_MISMATCH/);
assert.match(persistedDiagnostics, /data-diagnostic-page=/);
assert.doesNotMatch(persistedDiagnostics, /<button[^>]*data-page=|<script>/);
assert.match(context.diagnosticsMarkup(null), /读取失败，状态未知/);

function harness() {
  let counter = 0;
  let now = 0;
  const queue = new Map();
  const events = new Map();
  const documentRef = { hidden: false, addEventListener: (key, callback) => events.set(key, callback), removeEventListener: (key) => events.delete(key) };
  const timers = { setTimeout: (callback, delay) => { const id = ++counter; queue.set(id, { at: now + delay, callback }); return id; }, clearTimeout: (id) => queue.delete(id) };
  return { documentRef, timers, events, queue,
    async next() { const [id, entry] = [...queue].sort((a, b) => a[1].at - b[1].at)[0]; queue.delete(id); now = entry.at; entry.callback(); await new Promise(setImmediate); },
    async hide(value) { documentRef.hidden = value; events.get("visibilitychange")?.(); await new Promise(setImmediate); },
  };
}
(async () => {
  const h = harness();
  const states = [];
  const applied = [];
  let calls = 0;
  let resolve;
  let reject;
  let signal;
  let canPoll = true;
  const stop = context.startVisiblePoll({ ...h, canPoll: () => canPoll,
    load: (value) => { calls++; signal = value; return new Promise((a, b) => { resolve = a; reject = b; }); },
    apply: (value) => applied.push(value), onState: (value) => states.push(value),
  });
  assert.equal(calls, 0);
  canPoll = false;
  await h.next(); assert.equal(calls, 0); // Active form or confirmation suppresses reads.
  canPoll = true;
  await h.next(); assert.equal(calls, 1);
  await h.hide(true); assert.equal(signal.aborted, true);
  resolve("stale"); await new Promise(setImmediate);
  assert.equal(applied.length, 0); assert.equal(h.queue.size, 0);
  await h.hide(false); assert.equal(calls, 2);
  resolve("fresh"); await new Promise(setImmediate);
  assert.deepEqual(applied, ["fresh"]);
  assert.equal(states.at(-1).state, "CURRENT");
  await h.next(); reject(new Error("private server details")); await new Promise(setImmediate);
  assert.equal(states.at(-1).state, "UNKNOWN");
  assert.ok(states.at(-1).lastSuccess);
  assert.doesNotMatch(JSON.stringify(states), /private/);
  await h.next();
  h.events.get('paam:mutation')({ detail: { phase: 'start' } });
  assert.equal(signal.aborted, true);
  resolve('before command'); await new Promise(setImmediate);
  assert.deepEqual(applied, ['fresh']);
  const before = calls;
  await h.next(); assert.equal(calls, before);
  h.events.get('paam:mutation')({ detail: { phase: 'end' } });
  await h.next(); resolve('after command'); await new Promise(setImmediate);
  assert.deepEqual(applied, ['fresh', 'after command']);
  await h.next(); stop(); resolve("late"); await new Promise(setImmediate);
  assert.deepEqual(applied, ["fresh", "after command"]);
  assert.equal(h.events.size, 0); assert.equal(h.queue.size, 0);
  console.log("RESULT=PASS DISCLOSURE=1 PRECISE_STATS=1 BATCH_FEEDBACK=1 SAFE_PREVIEW=1 VISIBLE_POLL=1 OFFLINE=1");
})().catch((error) => { console.error(error); process.exitCode = 1; });

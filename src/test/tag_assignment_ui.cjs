"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const code = fs.readFileSync(path.resolve(__dirname, "../frontend/js/view/ledger.js"), "utf8");
const start = code.indexOf("function manualTagViewNames(form) {");
const end = code.indexOf("async function submitLedgerAccount", start);
assert.ok(start > 0 && end > start);
let sent;
const context = {
  FormData: class { *[Symbol.iterator]() { yield ["category", "food"]; yield ["mood", "happy"]; } },
  beginSubmit: () => true,
  jsonRequest: async (url, method, payload) => { sent = { url, method, payload }; },
  closeDialogs: () => {}, toast: () => {}, render: async () => {},
  endSubmit: () => {}, showFormError: (_form, error) => { throw error; },
};
vm.createContext(context);
vm.runInContext(`${code.slice(start, end)}\nglobalThis.scopes = manualTagViewNames; globalThis.submit = submitTags;`, context);
function form(category = "food", claimed = []) {
  return {
    dataset: { ledger: "12", updatedTime: "2026-09-26T10:00:00Z" },
    querySelectorAll: (selector) => selector === "select[name]" ? [
      { name: "category", value: category, dataset: { original: "food" } },
      { name: "mood", value: "happy", dataset: { original: "happy" } },
    ] : claimed.map((view) => ({ dataset: { manualView: view } })),
  };
}
(async () => {
  assert.deepEqual([...context.scopes(form())], []);
  assert.deepEqual([...context.scopes(form("travel"))], ["category"]);
  assert.deepEqual([...context.scopes(form("food", ["category"]))], ["category"]);
  assert.deepEqual([...context.scopes(form("travel", ["category"]))], ["category"]);
  await context.submit({ preventDefault() {}, currentTarget: form("food", ["category"]) });
  assert.equal(sent.method, "PUT");
  assert.equal(sent.url, "/paam/tag/v1/assignment/12");
  assert.deepEqual([...sent.payload.view_names], ["category"]);
  assert.equal(sent.payload.tag_state.mood, "happy");
  assert.match(code, /data-manual-view=/);
  assert.match(code, /data-original=/);
  console.log("PASS CHANGED_VIEW_ONLY=1 SAME_VALUE_TAKEOVER=1 UNCHANGED_NOOP=1 SCOPED_SUBMIT=1");
})().catch((error) => { console.error(error); process.exitCode = 1; });

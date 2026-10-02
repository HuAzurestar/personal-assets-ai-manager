"""Execute frontend exact-unit parsing rather than snapshotting JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script", [
    "automation_ui.cjs", "automation_m2_ui.cjs",
    "automation_setting_form.cjs", "tag_assignment_ui.cjs",
    "import_batch_unknown.cjs",
    "import_batch_issue.cjs",
    "write_failure.cjs",
    "tag_impact.cjs",
    "resource_id.cjs",
    "candidate_scan.cjs",
    "draft_reference.cjs",
])
def test_standalone_frontend_contract(script):
    """Keep the standalone CI contracts in the local full-test matrix too."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend contract execution")
    result = subprocess.run(
        [node, str(Path(__file__).parent / script)],
        text=True, capture_output=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_exact_frontend_units_and_canonical_money():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend unit execution")
    core = (Path(__file__).parents[1] / "frontend/js/util/core.js").resolve().as_uri()
    result = subprocess.run([node, "--input-type=module"], input=f'''
import {{ quantityAmount, quantityDecimal, decimalAmount, money, typeNames }} from {core!r};
import assert from 'node:assert/strict';
assert.equal(quantityAmount('0.001', 'KG_3'), 1);
assert.equal(quantityAmount('400.00', 'CNY'), 40000);
assert.equal(quantityDecimal(10000, 'CNY'), '100.00');
assert.equal(quantityAmount('400', 'KRW'), 400);
assert.equal(quantityAmount('1', 'PCS'), 1);
assert.equal(quantityDecimal(-1, 'KG_3'), '−0.001');
for (const [text, code] of [['1.01','KRW'], ['0.0001','KG_3'], ['1.1','PCS'],
  ['1e3','CNY'], ['NaN','CNY'], ['Infinity','CNY'], ['0','CNY'], ['-1','CNY'],
  ['9000000000001','PCS']]) assert.throws(() => quantityAmount(text, code));
assert.equal(decimalAmount('400', 'KRW'), 400);
assert.equal(money({{cash_amount:10000,cash_currency_code:'CNY'}}), money({{amount:10000,currency_code:'CNY'}}));
assert.equal(money({{cash_amount:null,cash_currency_code:'CNY'}}), '—');
assert.ok(typeNames.DUPLICATE);
assert.equal(typeNames.CLAIM, undefined);
''', text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_flow_candidate_scan_continuation_and_stale_generation():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for frontend unit execution")
    module = (Path(__file__).parents[1] / "frontend/js/view/flow-search.js").resolve().as_uri()
    result = subprocess.run([node, "--input-type=module"], input=f'''
import assert from 'node:assert/strict';
globalThis.document = {{ querySelector: () => null }};
globalThis.location = {{ hash: '#details/economy?word=Mock' }};
let calls = [], pending;
const replies = [
  {{items:[], total:null, scanned_count:2, has_more:true, next_cursor:'one'}},
  {{items:[], total:null, scanned_count:2, has_more:true, next_cursor:'two'}},
  {{items:[{{id:3}},{{id:4}}], total:null, scanned_count:2, has_more:false, next_cursor:null}},
];
globalThis.fetch = async (url) => {{
  calls.push(url);
  const body = replies.shift() || await new Promise(resolve => pending = resolve);
  return {{ok:true, status:200, json:async () => ({{status:200, message:'ok', body}})}};
}};
const {{readFlowSearch,bindFlowSearch,stopFlowRead,flowReadBusy}} = await import({module!r});
const query = new URLSearchParams({{page_size:'2',query:'[{{"key":"summary","word":"Mock"}}]'}});
const first = await readFlowSearch(query, location.hash);
assert.equal(first.total, null); assert.equal(first.items.length, 0);
const button = {{ disabled:false, isConnected:true }};
const root = {{isConnected:true, querySelector:key => key === '[data-flow-continue]' ? button : null}};
let painted = [];
bindFlowSearch(root, result => painted.push(result));
for (let i=0; i<100 && painted.at(-1)?.scan_state !== 'complete'; i++) await new Promise(resolve => setTimeout(resolve, 0));
assert.deepEqual(painted.at(-1).items.map(row => row.id), [3,4]);
assert.equal(painted.at(-1).scanned_count, 6); assert.equal(painted.at(-1).has_more, false);
assert.ok(calls[1].includes('cursor=one') && calls[2].includes('cursor=two'));
await button.onclick(); assert.equal(calls.length, 3);
// Start another scan, then cancel a pending continuation. Its late result must
// not repaint the new route, and simultaneous clicks cannot duplicate scans.
replies.push(first);
location.hash = '#details/economy?word=Other';
const other = new URLSearchParams({{page_size:'2',query:'[{{"key":"summary","word":"Other"}}]'}});
await readFlowSearch(other, location.hash);
bindFlowSearch(root, result => painted.push(result));
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(flowReadBusy(), true);
const before = calls.length; await button.onclick(); assert.equal(calls.length,before);
const paintCount = painted.length;
stopFlowRead(); location.hash = '#workbench/position';
pending({{items:[{{id:999}}],total:null,scanned_count:2,has_more:false,next_cursor:null}});
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(painted.length,paintCount); assert.equal(flowReadBusy(),false);
''', text=True, capture_output=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr

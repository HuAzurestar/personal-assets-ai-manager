"""Execute frontend exact-unit parsing rather than snapshotting JavaScript."""
from pathlib import Path
import shutil
import subprocess

import pytest


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

const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
(async () => {
  const {reviewScene, incompatibleSceneInputs} = await import(pathToFileURL(path.resolve(__dirname, '../frontend/js/util/review-scene.js')).href);
  for (const [code, type] of [['NORMAL','TRANSACTION'], ['REFUND','TRANSACTION'], ['INTERNAL_TRANSFER','ACCOUNT_TRANSFER'], ['DUPLICATE','DUPLICATE']]) {
    const policy = reviewScene(code);
    assert.equal(policy.cash, true);
    assert.equal(policy.quantity, false);
    assert.equal(policy.link, false);
    assert.equal(policy.phase, false);
    assert.equal(policy.duplicate, code === 'DUPLICATE');
    assert.deepEqual(policy.economicTypes, [type]);
    assert.ok(Object.isFrozen(policy) && Object.isFrozen(policy.economicTypes));
  }
  const opening = reviewScene('POS_OPENING');
  assert.equal(opening.cash, false); assert.equal(opening.quantity, true);
  assert.equal(opening.link, false); assert.equal(opening.duplicate, false);
  for (const code of ['BORROW_REPAY','SHARED_PAYMENT','POS_POSITION_OPEN','POS_POSITION_SETTLE','POS_CREDIT_PURCHASE','POS_CREDIT_REPAY']) {
    const policy = reviewScene(code);
    assert.ok(policy.cash && policy.quantity && policy.link && policy.duplicate);
    assert.equal(policy.phase, code === 'SHARED_PAYMENT');
    assert.equal(policy.economicTypes.length, 4);
  }
  assert.deepEqual(incompatibleSceneInputs('NORMAL', {cash:2,facts:1,drafts:1,legs:2,links:3,duplicates:1}), {drafts:1,legs:2,links:3,duplicates:1});
  assert.deepEqual(incompatibleSceneInputs('POS_OPENING', {cash:2,facts:1,drafts:1,legs:2,links:3,duplicates:1}), {facts:1,cash:2,links:3,duplicates:1});
  assert.deepEqual(incompatibleSceneInputs('BORROW_REPAY', {cash:2,facts:1,drafts:1,legs:2,links:3,duplicates:1}), {});
  for (const code of ['constructor','toString','UNKNOWN','']) assert.throws(() => reviewScene(code), /不支持/);
  for (const count of [-1,1.5,NaN,9007199254740992]) assert.throws(() => incompatibleSceneInputs('NORMAL', {legs:count}));
  console.log('PASS 11 scenes: applicable input only, strict presets, explicit discarded draft counts');
})().catch(error => {console.error(error); process.exitCode = 1;});

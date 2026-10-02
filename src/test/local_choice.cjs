const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
(async () => {
  const url = pathToFileURL(path.resolve(__dirname, '../frontend/js/component/local-choice.js')).href;
  const { localChoiceMap, localChoicePage } = await import(url);
  const choices = localChoiceMap(Array.from({ length: 1000 }, (_, i) => [`cash-${i}`, `Mock cash ${i} · CNY`]));
  let result = localChoicePage(choices, '', 1);
  assert.equal(result.total, 1000); assert.equal(result.items.length, 20); assert.equal(result.pages, 50);
  result = localChoicePage(choices, '', 50);
  assert.equal(result.items.length, 20); assert.equal(result.items[19][0], 'cash-999');
  result = localChoicePage(choices, 'mock CASH 999', 1);
  assert.equal(result.total, 1); assert.equal(result.items[0][0], 'cash-999');
  assert.equal(localChoicePage(choices, 'not a match', 1).total, 0);
  const literals = localChoiceMap([[1, 'Café %_'], [2, 'Other']]);
  assert.equal(localChoicePage(literals, 'cafe\u0301 %_', 1).items[0][0], '1');
  assert.equal(localChoicePage(literals, '%_', 1).total, 1);
  assert.equal(localChoicePage(literals, '?', 1).total, 0);
  assert.throws(() => localChoiceMap([['same', 'A'], ['same', 'B']]), /不唯一/);
  // Changes to the live draft clamp pages, not to a stale copied option list.
  choices.delete('cash-999');
  assert.equal(localChoicePage(choices, 'mock cash 999', 50).total, 0);
  assert.equal(localChoicePage(literals, '', 999).page, 1);
  console.log('PASS bounded local choices: 1000 named candidates, 20-row pages, literal NFC search, live deletion and exact scope counts');
})().catch(error => {console.error(error); process.exitCode = 1;});

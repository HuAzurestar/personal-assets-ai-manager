const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

(async () => {
  const {captureNavigationView, restoreNavigationView, navigationContext} = await import(
    pathToFileURL(path.resolve(__dirname, '../frontend/js/util/navigation-context.js')).href);
  const callbacks = {}, frames = [];
  let rows = [], scroll, busy = false;
  global.window = {scrollX:2, scrollY:600, innerHeight:800,
    scrollTo(value) {scroll = value; this.scrollX = value.left; this.scrollY = value.top;},
    addEventListener(name, fn) {callbacks[name] = fn;}};
  global.document = {addEventListener(name, fn) {callbacks[name] = fn;}};
  global.requestAnimationFrame = fn => {frames.push(fn); return frames.length;};
  global.location = {hash:'#details/ledger?page=2', href:'http://fictional.test/#details/ledger?page=2'};
  global.history = {state:{unrelated:'keep'}, replaceState(value) {this.state = value;}};
  const root = {scrollLeft:3, scrollTop:5,
    querySelectorAll() {return rows;},
    querySelector(selector) {return rows.find(row => selector === `[data-economic-row="${row.id}"]`);},
    getAttribute() {return busy ? 'true' : null;}};
  const row = (id, top) => ({id, hasAttribute:name => name === 'data-economic-row',
    getAttribute:() => id, getClientRects:() => [1],
    getBoundingClientRect:() => ({top, bottom:top + 50})});
  rows = [row('1', -300), row('2', 80), row('3', 130)];
  const view = captureNavigationView(root, location.hash);
  assert.equal(view.anchors.length, 2);
  assert.equal(view.anchors[0].value, '2');
  assert.equal(JSON.stringify(view).includes('summary'), false);
  rows = [row('2', 400), row('3', 450)];
  assert.equal(restoreNavigationView(root, view, location.hash), true);
  assert.equal(scroll.top, 920); // Restore the record, not merely the old pixel.
  rows = [row('3', 450)];
  assert.equal(restoreNavigationView(root, view, location.hash), true);
  assert.equal(scroll.top, 1240); // Missing first anchor uses the next surviving row.
  rows = [];
  restoreNavigationView(root, view, location.hash);
  assert.equal(scroll.top, 600);
  assert.equal(restoreNavigationView(root, view, '#other'), false);
  assert.equal(restoreNavigationView(root, {...view, y:NaN}, location.hash), false);
  assert.equal(restoreNavigationView(root, {...view, anchors:Array(9).fill({})}, location.hash), false);
  assert.equal(captureNavigationView(root, '#'+ 'a'.repeat(4096)), null);
  const navigation = navigationContext(root);
  navigation.begin(location.hash);
  navigation.mounted(location.hash);
  frames.shift()();
  assert.equal(history.state.unrelated, 'keep');
  assert.equal(history.state.paamView.route, location.hash);
  const saved = history.state;
  busy = true;
  navigation.remember();
  assert.equal(history.state, saved);
  busy = false;
  location.hash = '#workbench/review';
  navigation.remember();
  assert.equal(history.state, saved); // A pending destination cannot overwrite the origin.
  navigation.begin(location.hash);
  navigation.mounted(location.hash, {restore:true});
  assert.equal(scroll.top, 0);
  console.log('PASS per-entry viewport, stable/fallback anchors, finite bounded state and no stale route overwrite');
})().catch(error => {console.error(error); process.exitCode = 1;});

const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
(async () => {
  global.document = {querySelector: () => null};
  global.location = {hash:'#workbench/account'};
  const module = await import(pathToFileURL(path.resolve(__dirname, '../frontend/js/view/account-management.js')).href);
  const {accountScope, accountRefFilter, accountNavigation, accountManagementPage} = module;
  const params = value => new URLSearchParams(value);
  assert.equal(accountRefFilter(accountScope(params({party:'1'}))).key, 'party_id');
  assert.deepEqual(accountRefFilter(accountScope(params({party:'1',account:'2',status:'CLOSED'}))), {op:'AND',expression:[
    {key:'party_id',op:'=',val:1},{key:'account_id',op:'=',val:2},{key:'status',op:'=',val:'CLOSED'}]});
  assert.deepEqual(accountRefFilter(accountScope(params({unassigned:'1'}))), {key:'account_id',op:'=',val:0});
  assert.equal(accountRefFilter(accountScope(params({}))), undefined);
  for (const value of [{party:'9007199254740992'}, {account:'-1'}, {ref_page:'1.2'}, {page_size:'1000'},
    {status:'HIDDEN'}, {party:'1',unassigned:'1'}, {word:'a'.repeat(129)}]) assert.throws(() => accountScope(params(value)));
  assert.ok(!accountNavigation(params({party_page:'9',account_page:'8',party:'1',word:'Mock'}), {party:2,account:0,ref_page:1}).includes('party_page'));
  const calls = [];
  const party = {id:1,name:'Mock Alice',status:'ACTIVE',display_label:'Mock Alice'};
  const account = {id:2,party_id:1,name:'Mock group',status:'ACTIVE',display_label:'Mock group · Mock Alice'};
  const row = {id:3,name:'Mock <img src=x>',institution:'Mock bank',source_identity:'****1234',reference:'',party_name:'Mock Alice',
    account_name:'Mock group',identity_strength:'UNKNOWN',status:'ACTIVE',latest_source_time:null};
  global.fetch = async url => {
    calls.push(url);
    const body = url.endsWith('/account/2') ? account : url.endsWith('/account-party/1') ? party
      : {items:[row],total:1,page_index:1,page_size:20};
    return {ok:true,status:200,json:async()=>({status:200,message:'ok',body})};
  };
  const markup = await accountManagementPage(params({}));
  assert.equal(calls.length,1); assert.ok(calls[0].includes('/account-ref/list?'));
  assert.equal((markup.match(/<table>/g)||[]).length,1);
  assert.ok(markup.includes('Mock &lt;img src=x&gt;') && !markup.includes('<img src=x>'));
  calls.length = 0;
  const derived = await accountManagementPage(params({account:'2'}));
  assert.ok(derived.includes('data-party="1"'));
  const query = new URL(calls.at(-1),'http://example.test').searchParams;
  assert.deepEqual(JSON.parse(query.get('filter')), {op:'AND',expression:[
    {key:'party_id',op:'=',val:1},{key:'account_id',op:'=',val:2}]});
  calls.length = 0;
  await assert.rejects(() => accountManagementPage(params({party:'3',account:'2'})), /不属于当前个人/);
  assert.deepEqual(calls, ['/paam/ledger/v1/account/2']);
  calls.length = 0;
  await assert.rejects(() => accountManagementPage(params({party:'9007199254740992'})));
  assert.equal(calls.length,0);
  console.log('PASS card-first bounded reads, exact ownership scope, independent unassigned, invalid deep links and escaping');
})().catch(error => {console.error(error);process.exitCode=1;});

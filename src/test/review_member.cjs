const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
(async () => {
  const {currentReviewLabel,validateMemberPage,completeReviewMembers} = await import(pathToFileURL(path.resolve(__dirname,'../frontend/js/util/review-member.js')).href);
  const review = {id:7,title:'Mock grouped evidence',type:'OTHER_MANUAL',status:'CONFIRMED',updated_time:'2024-01-01T00:00:00Z',member_count:105};
  const fact = id => ({transaction_id:id,current_reviews:[review]});
  const header = () => ({items:[{...review}],total:1});
  const page = (index,total=105) => ({items:Array.from({length:Math.max(0,Math.min(100,total-(index-1)*100))},(_,i)=>fact((index-1)*100+i+1)),total,page_index:index,page_size:100});
  const calls = [];
  const read = async url => {calls.push(url);return url.includes('/review/list?') ? header() : page(Number(new URL(url,'http://local.test').searchParams.get('page_index')));};
  assert.match(currentReviewLabel(review),/Mock grouped evidence.*105/);
  const members = await completeReviewMembers(review,{read});
  assert.equal(members.length,105);assert.equal(members.at(-1).transaction_id,105);
  assert.equal(calls.length,4);assert.ok(calls[0].includes('/review/list?') && calls[3].includes('/review/list?'));
  assert.ok(calls[1].includes('page_index=1&page_size=100') && calls[2].includes('page_index=2&page_size=100'));
  assert.deepEqual(review,{id:7,title:'Mock grouped evidence',type:'OTHER_MANUAL',status:'CONFIRMED',updated_time:'2024-01-01T00:00:00Z',member_count:105});
  const rejected = async (transform,pattern, options={}) => {
    let count=0;
    await assert.rejects(completeReviewMembers(review,{read:async url => transform(await read(url),url,++count),...options}),pattern);
  };
  await rejected((value,url) => {if(url.includes('page_index=2')) throw new Error('Mock second-page failure');return value;},/second-page failure/);
  await rejected((value,url,count) => count===4 ? {items:[{...review,status:'REVOKED'}],total:1} : value,/状态已变化/);
  await rejected((value,url,count) => count===4 ? {items:[{...review,updated_time:'2024-01-02'}],total:1} : value,/状态已变化/);
  await rejected((value,url) => url.includes('/review/list?') ? {items:[],total:0} : value,/状态已变化/);
  await rejected((value,url) => {if(url.includes('page_index=2')) value.total=104;return value;},/不完整|数量已变化/);
  await rejected((value,url) => {if(url.includes('page_index=2')) value.items[0]=fact(1);return value;},/成员重复/);
  await rejected((value,url) => {if(url.includes('page_index=2')) value.items[0].current_reviews=[];return value;},/当前解释/);
  await rejected((value,url) => {if(url.includes('page_index=2')) value.items[0].current_reviews=[{...review,updated_time:'2024-01-02'}];return value;},/当前解释/);
  await rejected(value=>value,/选择上限/,{maxMembers:100});
  await rejected(value=>value,/读取容量/,{maxBytes:1});
  await rejected(value=>value,/已取消/,{valid:()=>false});
  const abort = new AbortController();
  await rejected((value,url) => {if(url.includes('page_index=2')) abort.abort();return value;},/已取消/,{signal:abort.signal});
  let pending, began;
  const deferred = new Promise(resolve=>began=resolve);
  const cancel = new AbortController();
  const loading = completeReviewMembers(review,{signal:cancel.signal,read:async url => {
    if(url.includes('page_index=2')) {began();return new Promise(resolve=>pending=resolve);}
    return read(url);
  }});
  await deferred;cancel.abort();pending(page(2));
  await assert.rejects(loading,/已取消/);
  for(const value of [{...page(1),total:NaN},{...page(1),page_index:2},{...page(1),page_size:20},{...page(1),items:[]},
    {...page(1),items:[fact(2**63),...page(1).items.slice(1)]}]) assert.throws(()=>validateMemberPage(value,1,100));
  console.log('PASS whole-member read: complete pages, fresh state, limits, failure/cancel without partial selection');
})().catch(error=>{console.error(error);process.exitCode=1;});

import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
const {planSourceRefresh}=await import('../app/auction-refresh.tsx');
const project=(id,source_key,extra={})=>({id,source_key,name:`Project ${id}`,can_search:true,reason:null,active_status:null,scan_run_id:null,...extra});
const options={sources:['copart','iaa','encar'].map(source_key=>({source_key,can_search:true,reason:null})),projects:[
  project(1,'copart'),project(2,'copart',{active_status:'RUNNING',scan_run_id:7}),
  project(3,'copart',{can_search:false,reason:'INVALID_SOURCE_URL'}),project(4,'iaa'),project(5,'encar'),
]};
test('bulk plan includes only ready projects of the exact current source; active and invalid projects stay separate',()=>{
  const plan=planSourceRefresh(options,'copart');
  assert.deepEqual(plan.ready.map(p=>p.id),[1]);
  assert.deepEqual(plan.active.map(p=>p.id),[2]);
  assert.deepEqual(plan.unavailable.map(p=>[p.id,p.reason]),[[3,'INVALID_SOURCE_URL']]);
  assert.deepEqual(planSourceRefresh(options,'iaa').ready.map(p=>p.id),[4]);
  assert.deepEqual(planSourceRefresh({...options,projects:[]},'copart').projects,[]);
});
test('site license and missing module fail closed, even if stale project flags permit search',()=>{
  const restricted={...options,sources:options.sources.map(s=>s.source_key==='copart'?{...s,can_search:false,reason:'LICENSE_SOURCE_NOT_ALLOWED'}:s)};
  const plan=planSourceRefresh(restricted,'copart');
  assert.deepEqual(plan.ready,[]);
  assert.deepEqual(plan.active.map(p=>p.id),[2]);
  assert.deepEqual(plan.unavailable.map(p=>p.reason),['LICENSE_SOURCE_NOT_ALLOWED','LICENSE_SOURCE_NOT_ALLOWED']);
  const missing=planSourceRefresh({...options,sources:[]},'copart');
  assert.equal(missing.ready.length,0);
  assert.ok(missing.unavailable.every(p=>p.reason==='SOURCE_UNAVAILABLE'));
  assert.equal(planSourceRefresh(options,'copart',[]).ready.length,0);
});

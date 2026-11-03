import assert from 'node:assert/strict';
import test from 'node:test';
import {watchTestDiagnostics} from '../app/test-diagnostics.ts';

test('preview diagnostics record safe UI state and timer gaps and stop completely', async()=>{
  const win=new EventTarget(),doc=new EventTarget(),sent=[];
  let tick,now=100000,cleared=false;
  win.__searchcarServiceHealth={checked_at:99000};
  win.setInterval=fn=>{tick=fn;return 42;};win.clearInterval=()=>{cleared=true;};
  doc.hasFocus=()=>true;doc.visibilityState='visible';
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  const stop=watchTestDiagnostics(true,async data=>{sent.push(data);},()=>({pending_queries:2,error_queries:1}),win,doc,()=>now);
  await flush();
  assert.equal(sent[0].native_age_ms,1000);
  win.dispatchEvent(new Event('searchcar:menu-resume'));await flush();assert.equal(sent.at(-1).event,'menu-resume');
  now+=60000;tick();await flush();assert.equal(sent.at(-1).event,'timer-gap');assert.equal(sent.at(-1).lag_ms,45000);
  doc.visibilityState='hidden';doc.dispatchEvent(new Event('visibilitychange'));await flush();assert.equal(sent.at(-1).hidden,true);
  assert.deepEqual(Object.keys(sent[0]).sort(),['event','focused','hidden','lag_ms','native_age_ms','pending_queries','error_queries'].sort());
  const count=sent.length;stop();win.dispatchEvent(new Event('focus'));win.dispatchEvent(new Event('searchcar:menu-resume'));tick();await flush();assert.equal(sent.length,count);assert.equal(cleared,true);
});

test('ordinary build attaches no timers, listeners or requests',()=>{
  const forbidden=new Proxy({}, {get(){throw Error('must remain disabled');}});
  watchTestDiagnostics(false,()=>{throw Error('sent');},()=>{throw Error('queried');},forbidden,forbidden)();
});

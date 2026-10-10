const {test}=require('node:test');
const assert=require('node:assert/strict');
const {planLaunch,enqueueDemo,advanceDemo,cancelDemo}=require('./auction-launch-ui.js');
const catalog=[
  {source:'Encar',state:'ready',projects:[{id:'p1',name:'Avante',scope:'all'},{id:'p2',name:'Sonata'}]},
  {source:'Copart',state:'ready',projects:[{id:'p1',name:'Audi'},{id:'p2',name:'Camry'}]},
  {source:'IAA',state:'ready',projects:[{id:'p1',name:'GLE'}]}
];
test('site selection stays isolated and equal project IDs from different sites are distinct',()=>{
  const q=[{source:'Encar',project:'p1',state:'running'}];
  const plan=planLaunch(catalog,['Encar','Copart'],q);
  assert.deepEqual(plan.ready.map(r=>r.source+':'+r.project),['Encar:p2','Copart:p1','Copart:p2']);
  assert.equal(plan.skipped[0].reason,'busy');
  assert.ok(plan.ready.every(r=>r.source!=='IAA'));
});
test('mixed batch rotates sites at project boundaries and never queues duplicates',()=>{
  const plan=planLaunch(catalog,['Encar','Copart','IAA','Copart'],[]);
  assert.deepEqual(plan.ready.map(r=>r.source),['Encar','Copart','IAA','Encar','Copart']);
  const queue=enqueueDemo([],plan,'b1');
  assert.equal(queue.filter(r=>r.state==='running').length,1);
  assert.equal(queue.filter(r=>r.state==='waiting').length,4);
  assert.equal(enqueueDemo(queue,plan,'b2').length,5);
});
test('disabled and empty sites explain the skip; planning freezes project data',()=>{
  const input=[...catalog,{source:'Empty',state:'ready',projects:[]}].map(s=>s.source==='IAA'?{...s,state:'license'}:s);
  const plan=planLaunch(input,['IAA','Empty','Copart'],[]);
  assert.deepEqual(plan.skipped.map(r=>r.reason),['license','empty']);
  assert.equal(plan.ready[0].name,'Audi');
  assert.notEqual(plan.ready[0].config,catalog[1].projects[0]);
  assert.equal(planLaunch(catalog,[],[]).ready.length,0);
});
test('canceling one site/project leaves other sites intact and releases the slot',()=>{
  const queue=enqueueDemo([],planLaunch(catalog,['Encar','Copart'],[]),'b1');
  const canceled=cancelDemo(queue,{batch:'b1',source:'Encar'});
  assert.equal(canceled.filter(r=>r.source==='Encar'&&r.state==='canceled').length,2);
  assert.equal(canceled.find(r=>r.source==='Copart').state,'running');
  assert.equal(queue[0].state,'running');
  const one=cancelDemo(queue,{batch:'b1',source:'Copart',project:'p1'});
  assert.equal(one.filter(r=>r.state==='canceled').length,1);
  assert.equal(one[0].state,'running');
});
test('source block affects only its remaining tasks; completed rows are never canceled',()=>{
  const queue=enqueueDemo([],planLaunch(catalog,['Encar','Copart','IAA'],[]),'b1');
  const blocked=advanceDemo(queue,true);
  assert.equal(blocked[0].state,'failed');
  assert.equal(blocked.find(r=>r.source==='Encar'&&r.project==='p2').state,'skipped');
  assert.equal(blocked.find(r=>r.source==='Copart').state,'running');
  const success=advanceDemo(queue);
  assert.equal(success[0].state,'succeeded');
  assert.equal(cancelDemo(success,{batch:'b1'})[0].state,'succeeded');
});

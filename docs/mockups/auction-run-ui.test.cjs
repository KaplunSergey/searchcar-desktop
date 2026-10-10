const {test}=require('node:test');
const assert=require('node:assert/strict');
const {searchView,deliveryView,stopDelivery,buildDemoRuns,runHistoryHTML}=require('./auction-run-ui.js');
const complete={status:'completed',scope:'all',pages:8,target:8,delivery:[{id:'a',state:'sent'},{id:'b',state:'error'}]};
test('history keeps the lot URL instead of replacing it with the search URL',()=>{
  const projects=[{id:'p1',name:'Audi'},{id:'p2',name:'Toyota'}];
  for(const [source,url] of [['Copart','https://www.copart.com/lot/65507986'],['IAA','https://www.iaai.com/VehicleDetail/46628754~US']]) {
    const lots=[{id:'a',project:'p1',name:'Audi',spec:['',''],url,event:{}}];
    const html=runHistoryHTML({runs:buildDemoRuns(source,projects,lots),projects,lots,source,lang:'ru',now:0,view:{screen:'list',expanded:new Set(['success'])}});
    assert.ok(html.includes('href="'+url+'"'));
    assert.doesNotMatch(html,/href="https:\/\/www\.(copart\.com\/lotSearchResults|iaai\.com\/Search)/);
  }
});
test('delivery failure does not turn a complete search into failed search',()=>{
  assert.equal(searchView(complete).tone,'success');
  assert.equal(deliveryView(complete).attention,true);
  assert.equal(deliveryView(complete).sent,1);
  assert.equal(deliveryView(complete).total,2);
});
test('first-page search succeeds against selected scope, not total website pages',()=>{
  const view=searchView({...complete,scope:'first',pages:1,target:1,availablePages:9,delivery:[]});
  assert.equal(view.attention,false);
  assert.match(view.coverage,/1 из 1/);
  assert.match(view.coverage,/выбранный охват/);
  assert.doesNotMatch(view.coverage,/1 из 9|неполный/i);
});
test('partial and canceled searches never imply complete coverage',()=>{
  for(const status of ['partial','canceled','failed']) {
    const view=searchView({...complete,status,pages:3});
    assert.notEqual(view.tone,'success');
    assert.equal(view.percent,null);
  }
  assert.equal(searchView({...complete,status:'partial'}).attention,true);
});
test('progress is based on the selected target; unknown total has no percentage',()=>{
  assert.equal(searchView({...complete,status:'running',pages:2,target:8}).percent,25);
  assert.equal(searchView({...complete,status:'running',pages:2,target:null}).percent,null);
});
test('uncertain delivery requires explicit confirmation, never an automatic retry',()=>{
  const view=deliveryView({...complete,delivery:[{id:'a',state:'sent'},{id:'b',state:'unknown'},{id:'c',state:'error'}]});
  assert.equal(view.attention,true);
  assert.deepEqual(view.retry,[{id:'b',confirm:true},{id:'c',confirm:false}]);
});
test('RU and UK labels cover canceled, partial, running and unknown states',()=>{
  assert.equal(searchView({...complete,status:'canceled'},'uk').label,'Скасовано');
  assert.equal(searchView({...complete,status:'partial'},'ru').label,'Неполный результат');
  assert.equal(searchView({...complete,status:'running'},'uk').label,'Оновлення');
});
test('stopping delivery preserves sent posts, cancels waiting and retains uncertainty of in-flight message',()=>{
  const rows=[{id:'a',state:'sent'},{id:'b',state:'sending'},{id:'c',state:'waiting'}];
  assert.deepEqual(stopDelivery(rows).map(m=>m.state),['sent','unknown','canceled']);
  assert.equal(rows[1].state,'sending');
});
test('history renders every search/delivery state in RU/UK, escapes source text and isolates sites',()=>{
  const projects=[{id:'p1',name:'<img src=x onerror=alert(1)>'},{id:'p2',name:'Second'}];
  const lots=[{id:'a',project:'p1',name:'Audi',spec:['2,0 л','2,0 л'],event:{state:'scheduled',kind:'start',at:'2026-10-08T16:00:00+02:00'}},{id:'b',project:'p1',name:'Future',spec:['3,0 л','3,0 л'],event:{state:'scheduled',kind:'start',at:'2026-10-09T16:00:00+02:00'}}];
  for(const source of ['IAA','Copart'])for(const lang of ['ru','uk']) {
    const own=buildDemoRuns(source,projects,lots),other=buildDemoRuns(source==='IAA'?'Copart':'IAA',projects,lots);
    const html=runHistoryHTML({runs:[...own,...other],projects,lots,source,lang,now:Date.parse('2026-10-08T12:37:00+02:00'),view:{screen:'list',expanded:new Set(own.map(r=>r.id))}});
    assert.equal((html.match(/data-history-run=/g)||[]).length,11);
    assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(html,/<img src=x|NaN|Invalid Date|ACV|<footer/);
    assert.match(html,lang==='ru'?/Изменений: 2/:/Змін: 2/);
  }
});
test('history filters preserve independent search and delivery outcomes',()=>{
  const projects=[{id:'p1',name:'Audi'},{id:'p2',name:'Toyota'}];
  const runs=buildDemoRuns('Copart',projects,[]);
  const render=view=>runHistoryHTML({runs,projects,lots:[],source:'Copart',lang:'ru',now:0,view:{screen:'list',...view}});
  const count=html=>(html.match(/data-history-run=/g)||[]).length;
  assert.equal(count(render({filter:'attention'})),4);
  assert.equal(count(render({filter:'completed'})),7);
  assert.equal(count(render({project:'p2'})),1);
  assert.match(render({project:'p2'}),/1 из 1 · выбранный охват/);
  assert.match(render({filter:'completed'}),/data-history-run="delivery-error"/);
});
test('empty filters, no history, loading and screen failure remain distinct',()=>{
  const projects=[{id:'p1',name:'Audi'},{id:'p2',name:'Toyota'}],runs=buildDemoRuns('IAA',projects,[]);
  const render=view=>runHistoryHTML({runs,projects,lots:[],source:'IAA',lang:'ru',now:0,view:{screen:'list',...view}});
  assert.match(render({project:'missing'}),/По этим фильтрам запусков нет/);
  assert.match(render({screen:'empty'}),/Запусков пока нет/);
  assert.doesNotMatch(render({screen:'empty'}),/id="history-reset"/);
  assert.match(render({screen:'loading'}),/aria-busy="true"/);
  assert.match(render({screen:'error'}),/ошибка экрана, а не новый неудачный поиск/);
});

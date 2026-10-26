import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { AuctionRuns, auctionRunSummary, filterAuctionRuns, auctionReportChanges } = await import('../app/auction-runs.tsx');
const projects=[{id:9,source_key:'copart',name:'Current Audi',cars:2},{id:11,source_key:'iaa',name:'Foreign IAA',cars:99}];
const run=(overrides={})=>({id:8,kind:'PROJECTS',status:'RUNNING',progress:87,created_at:'2026-10-19T10:00:00Z',payload:{trigger:'MANUAL',
  project_ids:[9,10,11],project_plans:[{project_id:9,source_key:'copart',name:'Audi'},{project_id:10,source_key:'copart',name:'Deleted BMW'},{project_id:11,source_key:'iaa',name:'Foreign IAA'}],
  project_statuses:{'9':'SUCCEEDED','10':'QUEUED','11':'FAILED'},current_project_id:9,
  pagination:{'9':{mode:'FIRST_PAGE',pages_visited:1,total_pages:9,ads_seen:20,complete:true}},
  report:[{car_id:1,project_id:9,source_key:'copart',change:'NEW',title:'New Audi'},
    {car_id:2,project_id:10,source_key:'copart',change:'UPDATED',title:'BMW',changes:[{field:'schedule',old:'Today',new:'Tomorrow'}]},
    {car_id:3,project_id:11,source_key:'iaa',change:'NEW',title:'Foreign car'},
    {car_id:4,project_id:9,source_key:'iaa',change:'NEW',title:'Mismatched source'}]},...overrides});
const summary=(value,source='copart',filter='all')=>auctionRunSummary(value,source,projects,filter);
const render=(props={})=>{
  const client=new QueryClient();
  try{return renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionRuns,{
    source:'copart',locale:'ru',runs:[run()],projects,runsLoading:false,runsError:false,retryRuns(){},sourceTestMode:true,
    navigate(){},write:async()=>({}),renderNotice:()=>null,...props
  })));}finally{client.clear();}
};
test('history projects frozen/deleted projects and never borrows another site status, totals or report',()=>{
  const own=summary(run());
  assert.deepEqual(own.projects.map(p=>p.name),['Audi','Deleted BMW']);
  assert.equal(own.status,'QUEUED'); // other site's failure is not our failure
  assert.deepEqual(own.report.map(r=>r.car_id),[1,2]);
  assert.equal(own.target,null);assert.equal(own.ads,null);assert.equal(own.percent,null);
  const first=summary(run(),'copart','9');
  assert.equal(first.status,'SUCCEEDED');assert.equal(first.target,1);assert.equal(first.visited,1);assert.equal(first.ads,20);assert.equal(first.firstPage,true);
  assert.deepEqual(first.report.map(r=>r.car_id),[1]);
  assert.equal(summary(run(),'iaa').status,'FAILED');
});
test('history progress uses measured selected coverage, unknown totals are indeterminate, waiting is not failed',()=>{
  const value=run();value.payload.project_statuses['9']='RUNNING';
  value.payload.pagination['9']={mode:'ALL_PAGES',pages_visited:2,expected_pages:8,total_pages:10,ads_seen:40};
  assert.equal(summary(value,'copart','9').percent,25);
  delete value.payload.pagination['9'].expected_pages;delete value.payload.pagination['9'].total_pages;
  assert.equal(summary(value,'copart','9').percent,null);
  value.payload.pagination['9'].interaction={state:'WAITING_FOR_USER'};
  assert.equal(summary(value,'copart','9').status,'WAITING_FOR_USER');
  value.payload.pagination['9'].pages_visited=-1;value.payload.pagination['9'].ads_seen=NaN;
  assert.equal(summary(value,'copart','9').visited,null);assert.equal(summary(value,'copart','9').ads,null);
});
test('history filtering derives the selected project outcome and keeps chronological order',()=>{
  const mixed=run();mixed.status='PARTIAL';mixed.payload.project_statuses={'9':'SUCCEEDED','10':'FAILED','11':'FAILED'};
  const queued=run({id:9});
  assert.deepEqual(filterAuctionRuns([mixed,queued],'copart',projects,{project:'all',state:'active'}).map(r=>r.id),[9]);
  assert.deepEqual(filterAuctionRuns([queued,mixed],'copart',projects,{project:'all',state:'attention'}).map(r=>r.id),[8]);
  assert.deepEqual(filterAuctionRuns([mixed],'copart',projects,{project:'9',state:'completed'}).map(r=>r.id),[8]);
  assert.deepEqual(filterAuctionRuns([mixed],'copart',projects,{project:'11',state:'all'}),[]);
  assert.deepEqual(filterAuctionRuns([mixed,queued],'copart',projects,{project:'all',state:'all'}).map(r=>r.id),[9,8]);
});
test('a source-projected legacy car refresh remains in history without inventing project coverage',()=>{
  const legacy=run({id:99,kind:'CARS',source_key:'copart',status:'SUCCEEDED',payload:{car_ids:[41]}});
  assert.deepEqual(filterAuctionRuns([legacy],'copart',projects,{project:'all',state:'completed'}).map(r=>r.id),[99]);
  assert.equal(summary(legacy).target,null);
  assert.equal(filterAuctionRuns([legacy],'iaa',projects,{project:'all',state:'all'}).length,0);
  assert.equal(filterAuctionRuns([legacy],'copart',projects,{project:'9',state:'all'}).length,0);
});
test('change filters only use recorded events and do not invent absence or sold changes',()=>{
  const report=summary(run()).report;
  assert.deepEqual(auctionReportChanges(report,'new').map(r=>r.car_id),[1]);
  assert.deepEqual(auctionReportChanges(report,'updated').map(r=>r.car_id),[2]);
  assert.deepEqual(auctionReportChanges(report,'schedule').map(r=>r.car_id),[2]);
  assert.deepEqual(auctionReportChanges(report,'all'),report);
});
test('a deduplicated vehicle report remains visible in every recorded project context',()=>{
  const value=run();
  value.payload.report=[{car_id:7,project_id:9,project_ids:[9,10],source_key:'copart',change:'UPDATED',title:'Shared car'}];
  const filtered=summary(value,'copart','10').report;
  assert.equal(filtered.length,1);
  assert.equal(filtered[0].project_id,10);
  assert.deepEqual(filtered[0].project_ids,[10]);
  assert.equal(summary(value,'iaa').report.length,0);
});
test('history distinguishes loading/read-error/empty, escapes labels and does not render foreign or technical data',()=>{
  for(const [locale,empty,loading,error,filter] of [['ru','Сохранённых запусков нет','Загрузка истории','Не удалось обновить историю','Состояние поиска'],
    ['uk','Збережених запусків немає','Завантаження історії','Не вдалося оновити історію','Стан пошуку']]) {
    assert.ok(render({locale,runs:[]}).includes(empty));
    assert.ok(render({locale,runsLoading:true}).includes(loading));
    const failed=render({locale,runsError:true});assert.ok(failed.includes(error));assert.doesNotMatch(failed,/data-run-id=/);
    const value=run();value.error='<private exception>';
    value.payload.project_plans[0].name='<script>Audi</script>';
    const html=render({locale,runs:[value]});
    assert.ok(html.includes(`aria-label="${filter}"`));assert.match(html,/&lt;script&gt;Audi&lt;\/script&gt;/);
    assert.doesNotMatch(html,/<script>|Foreign IAA|Foreign car|Mismatched source|private exception|87%|Повторить отправку|Повторити надсилання/);
  }
});
test('unknown trigger/date stay unknown and active unknown coverage has no fake percent',()=>{
  const value=run({created_at:'invalid'});value.payload.trigger=undefined;value.payload.project_statuses['10']='RUNNING';
  const html=render({runs:[value]});
  assert.match(html,/Дата неизвестна/);assert.match(html,/Тип запуска не указан/);
  assert.match(html,/<progress aria-label="Прогресс поиска" max="100"><\/progress>/);
  assert.doesNotMatch(html,/87%|value="0"|Ручной поиск/);
});
test('history restores in-memory UI for only its own source across workspace remounts',()=>{
  const client=new QueryClient();
  const ui={filters:{project:'9',state:'completed'},expanded:{'copart-8':true},changes:{'copart-8':'schedule'},tabs:{}};
  client.setQueryData(['auction-history-ui','copart'],ui);
  const value=run({status:'PARTIAL'});value.payload.project_statuses={'9':'SUCCEEDED','10':'FAILED','11':'FAILED'};
  const draw=source=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionRuns,{
    source,locale:'ru',runs:[value],projects,runsLoading:false,runsError:false,retryRuns(){},navigate(){},renderNotice:()=>null
  })));
  try {
    assert.match(draw('copart'),/value="9" selected=""/);
    assert.match(draw('copart'),/aria-expanded="true"/);
    assert.match(draw('copart'),/value="schedule" selected=""/);
    const other=draw('iaa');
    assert.match(other,/value="all" selected=""/);assert.match(other,/aria-expanded="false"/);
    assert.deepEqual(client.getQueryData(['auction-history-ui','copart']),ui);
  }finally{client.clear();}
});

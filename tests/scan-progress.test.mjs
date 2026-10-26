import './app-loader.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
const {LaunchProgress}=await import('../app/launch-dialog.tsx');
const {SourceNavigation}=await import('../app/source-navigation.tsx');
const progress=await import('../app/scan-progress.tsx');
const projects=[{id:1,name:'Finished',source_key:'copart'},{id:2,name:'Current',source_key:'copart'},{id:3,name:'Next',source_key:'iaa'}];
const run={id:70,kind:'PROJECTS',status:'RUNNING',progress:33,payload:{project_ids:[1,2,3],current_project_id:2,
  project_statuses:{1:'SUCCEEDED',2:'RUNNING',3:'QUEUED'},project_plans:projects.map(p=>({project_id:p.id,...p}))}};
test('project progress uses frozen project statuses, waits for verification and preserves terminal failures',()=>{
  assert.equal(progress.latestProjectUpdate([run],1,projects).status,'SUCCEEDED');
  assert.equal(progress.latestProjectUpdate([run],2,projects).status,'RUNNING');
  const waiting={...run,payload:{...run.payload,pagination:{2:{interaction:{state:'WAITING_FOR_USER'}}}}};
  assert.equal(progress.latestProjectUpdate([waiting],2,projects).status,'WAITING_FOR_USER');
  const failed={...run,id:71,status:'FAILED',payload:{project_ids:[1]}};
  assert.equal(progress.latestProjectUpdate([run,failed],1,projects).status,'FAILED');
  assert.equal(progress.latestProjectUpdate([failed,run],1,projects).status,'FAILED');
  assert.equal(progress.latestProjectUpdate([run],99,projects),null);
});
test('sidebar idle is green, missing status is not green, and active work stays visible after a license restriction',()=>{
  const options={sources:['copart','iaa'].map(source_key=>({source_key,family:'AUCTION',can_search:true,reason:null})),projects};
  const nav=extra=>renderToStaticMarkup(React.createElement(SourceNavigation,{source:'copart',view:'projects',locale:'ru',navigate(){},selectSource(){},options,...extra}));
  assert.match(nav({}),/source-ready[^>]*aria-label="Copart: Нет активных обновлений"/);
  assert.doesNotMatch(nav({activityFailed:true}),/source-ready/);
  assert.match(nav({activityFailed:true}),/Состояние обновления неизвестно/);
  const blocked={...options,sources:options.sources.map(s=>({...s,can_search:false,reason:'LICENSE_SOURCE_NOT_ALLOWED'}))};
  assert.doesNotMatch(nav({options:blocked}),/source-ready/);
  assert.match(nav({options:blocked,runs:[run]}),/source-running[^>]*aria-label="Copart: Обновляется"/);
  assert.match(nav({options:blocked,runs:[run]}),/source-waiting[^>]*aria-label="IAA: В очереди"/);
});
test('launch result follows each returned scan id, retaining completion and failure rather than enqueue acknowledgement',()=>{
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  try {
    client.setQueryData(['scans','launch',70],run);
    client.setQueryData(['scans','launch',71],{id:71,kind:'PROJECTS',status:'FAILED',progress:100,payload:{project_ids:[3]}});
    const result={id:70,projects:[{project_id:1,status:'QUEUED',scan_run_id:70},{project_id:2,status:'QUEUED',scan_run_id:70},{project_id:3,status:'ALREADY_ACTIVE',scan_run_id:71}]};
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(LaunchProgress,{result,projects,locale:'ru',read:()=>{throw Error('SSR must not fetch');}})));
    assert.match(html,/Завершено: 1/);assert.match(html,/Обновляется: 1/);assert.match(html,/С ошибкой: 1/);
    for(const status of ['SUCCEEDED','RUNNING','FAILED']) assert.ok(html.includes(`data-status="${status}"`));
    client.getQueryCache().find({queryKey:['scans','launch',70],exact:true}).setState({status:'error',error:new Error('offline')});
    const stale=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(LaunchProgress,{result,projects,locale:'uk',read:async()=>run})));
    assert.match(stale,/Не вдалося оновити стан/);
  } finally {client.clear();}
});

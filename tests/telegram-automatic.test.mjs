import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { TelegramAutomatic, checkedAutomaticStatus } = await import('../app/telegram-automatic.tsx');
const base=()=>({project_id:9,source_key:'copart',enabled:false,recipient:{project_id:9,title:'Team <script>',chat_id:555,binding_id:'b'.repeat(32)},batch:null,issue:null,max_result_age_minutes:30});

test('automatic settings reject another source/project/recipient/batch rather than offering opt-in',()=>{
  assert.equal(checkedAutomaticStatus(base(),'copart',9).enabled,false);
  for(const change of [{project_id:10},{source_key:'iaa'},{enabled:'yes'},
    {recipient:{...base().recipient,project_id:10}},{batch:{project_id:10,source_key:'copart',jobs:[]}}]) {
    assert.throws(()=>checkedAutomaticStatus({...base(),...change},'copart',9),/context/);
  }
});

for(const locale of ['ru','uk']) test(`${locale}: default-off checkbox, escaped recipient, preview and next-search explanation`,()=>{
  const client=new QueryClient();client.setQueryData(['telegram-automatic','copart',9],base());
  const calls=[];
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramAutomatic,
      {source:'copart',projectId:9,locale,read:async()=>calls.push('read'),write:async()=>calls.push('write')})));
    assert.match(html,/type="checkbox"/);assert.doesNotMatch(html,/checked=""/);
    assert.match(html,/Team &lt;script&gt;/);assert.match(html,/555/);
    assert.match(html,locale==='ru'?/следующий полный поиск/:/наступний повний пошук/);
    assert.match(html,locale==='ru'?/Посмотреть сообщения/:/Переглянути повідомлення/);
    assert.deepEqual(calls,[]);
  } finally {client.clear();}
});

test('compact progress does not revive the removed recipient configuration panel',()=>{
  const client=new QueryClient();
  client.setQueryData(['telegram-automatic','copart',9],{...base(),enabled:true,issue:'RUN_INCOMPLETE'});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramAutomatic,
      {compact:true,source:'copart',projectId:9,locale:'ru',read:async()=>{}})));
    assert.match(html,/пропущена для всего запуска/);assert.doesNotMatch(html,/<input|Team|получатель проекта|555/);
  } finally {client.clear();}
});

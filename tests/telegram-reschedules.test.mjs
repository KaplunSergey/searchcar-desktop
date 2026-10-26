import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { TelegramSettings } = await import('../app/telegram-settings.tsx');
const { checkedRescheduleStatus } = await import('../app/telegram-reschedules.tsx');

test('reschedule status rejects mismatched scope and malformed queue counters',()=>{
  const value={project_id:9,source_key:'copart',enabled:true,recipient:null,counts:{QUEUED:1},issue:null,max_result_age_minutes:30};
  assert.equal(checkedRescheduleStatus(value,'copart',9),value);
  for(const change of [{project_id:8},{source_key:'iaa'},{enabled:'yes'},{counts:{QUEUED:-1}},
    {counts:{QUEUED:1.5}},{counts:{UNKNOWN_STATE:1}},{counts:[]},
    {recipient:{project_id:8,title:'wrong project',chat_id:555,binding_id:'b'.repeat(32)}}]) {
    assert.throws(()=>checkedRescheduleStatus({...value,...change},'copart',9),/context/);
  }
});

function render(locale, failed = false) {
  const client = new QueryClient();
  client.setQueryData(['telegram-reschedules','copart',9], {project_id:9,source_key:'copart',enabled:true,
    recipient:{project_id:9,title:'Team <script>',chat_id:555,binding_id:'b'.repeat(32)},
    counts:{SENT:2,QUEUED:1},issue:null,max_result_age_minutes:30});
  if(failed) client.getQueryCache().find({queryKey:['telegram-reschedules','copart',9],exact:true})
    .setState({status:'error',error:new Error('fixture disconnected')});
  try {
    return renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramSettings,{
      source:'copart',initialProjectId:9,locale,projects:[{id:9,source_key:'copart',name:'Test project'}],
      read:async()=>{throw new Error('SSR must not call API');},write:async()=>{throw new Error('SSR must not write');}})));
  } finally {client.clear();}
}

for(const locale of ['ru','uk']) test(`${locale}: project settings have a separate checked reschedule option, not enabled Today`,()=>{
  const html=render(locale);
  const section=html.match(/<section[^>]*class="telegram-reschedule-settings"[\s\S]*?<\/section>/)?.[0];
  assert.ok(section, 'missing independent reschedule settings');
  assert.match(section, /type="checkbox"[^>]*checked=""/);
  assert.match(section, locale==='ru'?/Уведомлять о переносе аукциона/:/Сповіщати про перенесення аукціону/);
  assert.match(section, /Team &lt;script&gt;/);
  assert.match(section, locale==='ru'?/полного успешного поиска/:/повного успішного пошуку/);
  assert.match(section, locale==='ru'?/Старые переносы/:/Минулі перенесення/);
  assert.match(section, locale==='ru'?/выключить/:/вимкнути/);
});

test('a failed status read retains the last setting but disables changes and provides retry',()=>{
  const section=render('ru',true).match(/<section[^>]*class="telegram-reschedule-settings"[\s\S]*?<\/section>/)?.[0];
  assert.ok(section);
  assert.match(section,/role="alert"/);
  assert.match(section,/type="checkbox"[^>]*disabled/);
  assert.match(section,/Повторить/);
});

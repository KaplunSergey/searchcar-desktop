import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { TelegramProject, telegramPairingRefreshInterval, refreshTelegramPairing } = await import('../app/telegram-project.tsx');

const waiting=()=>({available:true,delivery_enabled:false,recipient:null,session:{pairing_id:'d'.repeat(32),project_id:9,
  status:'WAITING_START',expires_at_ms:10000,target_kind:'group',recipient:null}});
const candidate=()=>{const v=waiting();return {...v,session:{...v.session,status:'AWAITING_CONFIRMATION',
  recipient:{title:'Team',chat_id:-100555,chat_type:'supergroup'}}};};
const connected=()=>{const v=candidate();return {...v,session:{...v.session,status:'CONNECTED'},recipient:{project_id:9,
  ...v.session.recipient,binding_id:'b'.repeat(32)}};};
const path='/projects/9/telegram?source_key=copart';

test('pairing refresh is bounded to one available, unexpired pending project and stops on error or terminal state',()=>{
  assert.equal(telegramPairingRefreshInterval(waiting(),9,1000),5000);
  assert.equal(telegramPairingRefreshInterval(candidate(),9,1000),5000);
  for(const value of [undefined,{...waiting(),available:false},connected(),
    {...waiting(),session:{...waiting().session,status:'EXPIRED'}},
    {...waiting(),session:{...waiting().session,status:'REVOKED'}},
    {...waiting(),recipient:{project_id:10}}]) assert.equal(telegramPairingRefreshInterval(value,9,1000),false);
  assert.equal(telegramPairingRefreshInterval(waiting(),10,1000),false);
  assert.equal(telegramPairingRefreshInterval(waiting(),9,10000),false);
  assert.equal(telegramPairingRefreshInterval(waiting(),9,1000,true),false);
});

test('automatic pairing checks once, confirms only the verified matching chat and never sends',async()=>{
  const calls=[];
  const result=await refreshTelegramPairing({current:waiting(),projectId:9,path,signal:new AbortController().signal,now:()=>1000,
    write:async(p,body)=>{calls.push({path:p,...body});return body.action==='CHECK'?candidate():connected();}});
  assert.equal(result.session.status,'CONNECTED');assert.equal(result.delivery_enabled,false);
  assert.deepEqual(calls,[{path,action:'CHECK',pairing_id:'d'.repeat(32)},
    {path,action:'CONFIRM',pairing_id:'d'.repeat(32),chat_id:-100555}]);
});

test('waiting, expired, unavailable and already-connected results never trigger a confirm',async()=>{
  for(const value of [waiting(),connected(),{...candidate(),available:false},
    {...candidate(),session:{...candidate().session,expires_at_ms:999}}]) {
    const calls=[];
    await refreshTelegramPairing({current:waiting(),projectId:9,path,signal:new AbortController().signal,now:()=>1000,
      write:async(_,body)=>{calls.push(body.action);return value;}});
    assert.deepEqual(calls,['CHECK']);
  }
});

test('a changed project, pairing or target kind cannot be confirmed automatically',async()=>{
  for(const change of [{project_id:10},{pairing_id:'e'.repeat(32)},{target_kind:'channel'}]) {
    const calls=[];
    await assert.rejects(()=>refreshTelegramPairing({current:waiting(),projectId:9,path,signal:new AbortController().signal,now:()=>1000,
      write:async(_,body)=>{calls.push(body.action);return {...candidate(),session:{...candidate().session,...change}};}}),
      e=>e.code==='telegram_pairing_changed');
    assert.deepEqual(calls,['CHECK']);
  }
});

test('leaving the screen during a check aborts automatic confirmation',async()=>{
  const controller=new AbortController(),calls=[];
  await assert.rejects(()=>refreshTelegramPairing({current:waiting(),projectId:9,path,signal:controller.signal,now:()=>1000,
    write:async(_,body)=>{calls.push(body.action);controller.abort();return candidate();}}),e=>e.name==='AbortError');
  assert.deepEqual(calls,['CHECK']);
});

test('an ambiguous confirmation is not retried; the next check recovers a connected result',async()=>{
  const calls=[];
  await assert.rejects(()=>refreshTelegramPairing({current:waiting(),projectId:9,path,signal:new AbortController().signal,now:()=>1000,
    write:async(_,body)=>{calls.push(body.action);if(body.action==='CONFIRM')throw Error('timeout');return candidate();}}),/timeout/);
  assert.deepEqual(calls,['CHECK','CONFIRM']);
  calls.length=0;
  const recovered=await refreshTelegramPairing({current:waiting(),projectId:9,path,signal:new AbortController().signal,now:()=>1000,
    write:async(_,body)=>{calls.push(body.action);return connected();}});
  assert.equal(recovered.session.status,'CONNECTED');assert.deepEqual(calls,['CHECK']);
});

for(const enabled of [false,true]) test(`chat recipient kinds are visible but gated (${enabled})`,()=>{
  const client=new QueryClient();
  client.setQueryData(['project-telegram','copart',9],{available:true,chat_pairing_enabled:enabled,session:null,recipient:null,delivery_enabled:false});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramProject,
      {source:'copart',projectId:9,locale:'ru',nowMs:1000,read:async()=>{},write:async()=>{}})));
    assert.match(html,/Тип получателя/);
    assert.match(html,enabled?/<option value="group">Группа<\//:/<option value="group" disabled="">Группа<\//);
    assert.match(html,enabled?/<option value="channel">Канал<\//:/<option value="channel" disabled="">Канал<\//);
    assert.doesNotMatch(html,/<input/);
  } finally {client.clear();}
});

test('reopening a saved group reflects its type rather than silently selecting private messages',()=>{
  const client=new QueryClient();
  client.setQueryData(['project-telegram','copart',9],{available:true,chat_pairing_enabled:true,session:null,
    recipient:{project_id:9,title:'Team',chat_id:-100555,chat_type:'supergroup',binding_id:'b'.repeat(32)},delivery_enabled:false});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramProject,
      {source:'copart',projectId:9,locale:'ru',nowMs:1000,read:async()=>{},write:async()=>{}})));
    assert.match(html,/<option value="group" selected="">Группа<\//);
  } finally {client.clear();}
});

test('a project offers saved recipients and a new recipient without accepting an arbitrary address',()=>{
  const client=new QueryClient();
  client.setQueryData(['project-telegram','iaa',2],{available:true,session:null,recipient:null,delivery_enabled:false});
  client.setQueryData(['telegram-recipients'],{available:true,recipients:[{id:'a'.repeat(32),title:'Alex <script>',chat_id:555,chat_type:'private'}]});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramProject,
      {source:'iaa',projectId:2,locale:'ru',nowMs:1000,read:async()=>{},write:async()=>{}})));
    assert.match(html,/<select/);assert.match(html,/Alex &lt;script&gt;.*555/);assert.match(html,/Добавить нового/);
    assert.doesNotMatch(html,/<input/);
  } finally {client.clear();}
});

for(const locale of ['ru','uk']) {
  for(const available of [false,true]) test(`${locale}: project pairing is token-free, scoped and never enables delivery (${available})`,()=>{
    const client=new QueryClient();
    client.setQueryData(['project-telegram','copart',9],{available,issue:available?null:'telegram_relay_not_configured',
      delivery_enabled:false,session:null,recipient:available?{project_id:9,title:'Alex <script>',chat_id:555,chat_type:'private',binding_id:'b'.repeat(32)}:null});
    try {
      const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramProject,
        {source:'copart',projectId:9,locale,nowMs:1000,read:async()=>{throw Error('No network during render');},write:async()=>{throw Error('No commands during render');}})));
      assert.doesNotMatch(html,/<input|type="password"|sendMessage|sendPhoto/);
      assert.match(html,/Start/);
      if(available) {assert.match(html,/Alex &lt;script&gt;/);assert.match(html,/555/);}
      else {assert.match(html,/disabled=""/);assert.match(html,locale==='ru'?/не настроен/:/не налаштовано/);}
      assert.match(html,locale==='ru'?/Отправка сообщений ещё не подключена/:/Надсилання повідомлень ще не підключено/);
    } finally {client.clear();}
  });
}

test('another project candidate is not rendered or confirmable, and an expired link cannot be confirmed',()=>{
  const client=new QueryClient();
  const candidate={pairing_id:'d'.repeat(32),project_id:10,status:'AWAITING_CONFIRMATION',expires_at_ms:10000,
    recipient:{title:'Foreign person',chat_id:777,chat_type:'private'}};
  const render=()=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramProject,
    {source:'copart',projectId:9,locale:'ru',nowMs:1000,read:async()=>{},write:async()=>{throw Error('Never during render');}})));
  try {
    client.setQueryData(['project-telegram','copart',9],{available:true,session:candidate,recipient:null,delivery_enabled:false});
    let html=render();assert.match(html,/другому проекту/);assert.doesNotMatch(html,/Foreign person|>Подтвердить получателя</);
    client.setQueryData(['project-telegram','copart',9],{available:true,session:{...candidate,project_id:9,expires_at_ms:900},recipient:null,delivery_enabled:false});
    html=render();assert.match(html,/Срок ссылки истёк/);assert.doesNotMatch(html,/>Подтвердить получателя</);
    assert.match(html,/Проверить состояние/);
  } finally {client.clear();}
});

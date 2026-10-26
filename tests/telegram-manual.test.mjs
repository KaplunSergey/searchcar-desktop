import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { auctionTelegramMessage } = await import('../app/auction-telegram-preview.tsx');
const { AuctionCard } = await import('../app/auction-workspace.tsx');
const { AuctionTelegramPreview } = await import('../app/auction-telegram-preview.tsx');
const context = {generated_at:'2026-10-22T10:00:00Z',local_date:'2026-10-22',computer_timezone:'Europe/Warsaw'};
const lot = {id:41,source_key:'iaa',source_listing_id:'12345678',title:'BMW X2',details:{},schedule:{},money_values:[],
  url:'https://www.iaai.com/VehicleDetail/12345678~US',time:{event_kind:'START',precision:'UNKNOWN',local_date:null,local_datetime:null,is_today:null,remaining_seconds:null,time_state:'UNKNOWN'}};
test('manual messages support undated/future collections without changing automatic Today eligibility',()=>{
  assert.equal(auctionTelegramMessage(lot,context,'ru'),null);
  const manual=auctionTelegramMessage(lot,context,'ru',undefined,undefined,true);
  assert.ok(manual);assert.match(manual.text,/Дата не назначена/);assert.doesNotMatch(manual.text,/null|undefined|До начала/);
  const archived={...lot,project_search_status:'NOT_FOUND_IN_SEARCH'};
  const archive=auctionTelegramMessage(archived,context,'ru',undefined,undefined,true);
  assert.match(archive.text,/Последние известные сведения/);assert.doesNotMatch(archive.text,/До начала/);assert.equal(archive.reply_markup,undefined);
  const future={...lot,schedule:{utc_at:'2026-10-23T12:00:00Z'},time:{...lot.time,local_date:'2026-10-23',local_datetime:'2026-10-23T14:00:00+02:00',is_today:false,precision:'EXACT'}};
  assert.equal(auctionTelegramMessage(future,context,'ru'),null);
  assert.match(auctionTelegramMessage(future,context,'ru',undefined,undefined,true).text,/23 октября 2026, 14:00/);
});
test('single-car Telegram action is icon-only, accessible, and absent from historical snapshots',()=>{
  const props={lot,context,locale:'ru',imageUrl:v=>v,telegramAction:React.createElement('button',{'aria-label':'Отправить в Telegram: BMW X2'},'icon')};
  assert.match(renderToStaticMarkup(React.createElement(AuctionCard,props)),/Отправить в Telegram: BMW X2/);
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(AuctionCard,{...props,historicalAt:context.generated_at})),/Отправить в Telegram/);
});
test('auction settings offer their own source-labelled project choice and own recipient connection UI',async()=>{
  const { TelegramSettings } = await import('../app/telegram-settings.tsx');
  const client=new QueryClient();
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramSettings,{
      source:'iaa',locale:'uk',projects:[{id:9,source_key:'iaa',name:'BMW X2'},{id:10,source_key:'copart',name:'BMW X2'}],read:async()=>{},write:async()=>{}})));
    assert.match(html,/Проєкт для налаштування Telegram/);assert.match(html,/IAA · BMW X2/);assert.doesNotMatch(html,/Copart · BMW X2/);
    assert.doesNotMatch(html,/type="password"/);
  } finally {client.clear();}
});
test('auction bot settings list only this auction, while global settings retain Encar only',async()=>{
  const { TelegramSettings } = await import('../app/telegram-settings.tsx');
  const projects=[{id:9,source_key:'iaa',name:'IAA project'},{id:10,source_key:'copart',name:'Copart project'},{id:11,source_key:'encar',name:'Encar project'}];
  const client=new QueryClient();
  const render=(source)=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramSettings,{
    source,locale:'ru',projects,read:async()=>{},write:async()=>{}})));
  try {
    const auction=render('iaa');assert.match(auction,/IAA project|Общий бот SearchCar/);assert.doesNotMatch(auction,/Copart project|Encar project/);
    const global=render();assert.match(global,/Encar project/);assert.doesNotMatch(global,/IAA project|Copart project|Общий бот SearchCar/);
    const empty=renderToStaticMarkup(React.createElement(TelegramSettings,{locale:'ru',projects:projects.slice(0,2),read:async()=>{},write:async()=>{}}));
    assert.equal(empty,'');
  } finally {client.clear();}
});
test('recipient settings select the originating project ID, not a similarly named project or another source',async()=>{
  const {TelegramSettings}=await import('../app/telegram-settings.tsx');
  const client=new QueryClient();
  const projects=[{id:6,source_key:'copart',name:'тест 2'},{id:14,source_key:'copart',name:'Тест2'},
    {id:15,source_key:'iaa',name:'тест 2'}];
  const render=id=>renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(TelegramSettings,{
    source:'copart',initialProjectId:id,locale:'ru',projects,read:async()=>{},write:async()=>{}})));
  try {
    const html=render(6);
    assert.match(html,/<option value="copart:6" selected="">Copart · тест 2/);
    assert.doesNotMatch(html,/<option value="copart:14" selected=""/);
    assert.doesNotMatch(render(15),/<option value="copart:[0-9]+" selected=""/);
  } finally {client.clear();}
});
test('readiness is project-scoped and cannot report delivery or silently call a sender',async()=>{
  const { checkTelegramSend } = await import('../app/telegram-delivery.ts');
  for(const [status,code] of [
    [{available:true,recipient:{project_id:10}},'context'],
    [{available:false,recipient:null},'unavailable'],
    [{available:true,recipient:null},'recipient'],
    [{available:true,recipient:{project_id:9},delivery_enabled:false},'delivery'],
  ]) {
    const calls=[];
    await assert.rejects(checkTelegramSend('iaa',9,async path=>{calls.push(path);return status;}),new RegExp(code));
    assert.deepEqual(calls,['/projects/9/telegram?source_key=iaa']);
  }
  assert.equal((await checkTelegramSend('iaa',9,async()=>({available:true,recipient:{project_id:9},delivery_enabled:true}))).delivery_enabled,true);
});
test('delivery progress accepts only the exact project, action and selected cars; queued is never sent',async()=>{
  const { checkedTelegramBatch,deliveryActive }=await import('../app/telegram-delivery.ts');
  const request={intent_id:'a'.repeat(32),car_ids:[41,42]};
  const batch={id:request.intent_id,project_id:9,source_key:'iaa',recipient:{title:'Alex',chat_id:555,chat_type:'private'},jobs:[41,42].map((id)=>({id:id.toString(16).padStart(32,'0'),car_id:id,state:'QUEUED',message_id:null}))};
  assert.equal(checkedTelegramBatch(batch,'iaa',9,request),batch);assert.equal(deliveryActive(batch),true);
  for(const invalid of [{...batch,project_id:10},{...batch,source_key:'copart'},{...batch,id:'b'.repeat(32)},
    {...batch,jobs:[batch.jobs[0],batch.jobs[0]]},{...batch,jobs:[{...batch.jobs[0],state:'SENT'} ,batch.jobs[1]]}]) {
    assert.throws(()=>checkedTelegramBatch(invalid,'iaa',9,request));
  }
  assert.equal(deliveryActive({...batch,jobs:batch.jobs.map(job=>({...job,state:'UNKNOWN'}))}),false);
});
test('web Settings retains the legacy Encar project choice without offering native pairing',async()=>{
  const { TelegramSettings } = await import('../app/telegram-settings.tsx');
  const html=renderToStaticMarkup(React.createElement(TelegramSettings,{
    native:false,locale:'ru',projects:[{id:9,source_key:'encar',name:'Encar project'},{id:10,source_key:'iaa',name:'IAA project'}],read:async()=>{},write:async()=>{}}));
  assert.match(html,/Encar project/);assert.doesNotMatch(html,/IAA project|Start|search_car_app_bot/);
});
test('a loading or failed project list is not presented as an empty workspace',async()=>{
  const { TelegramSettings } = await import('../app/telegram-settings.tsx');
  for(const state of [{projectsLoading:true},{projectsError:true}]) {
    const html=renderToStaticMarkup(React.createElement(TelegramSettings,{
      ...state,native:false,locale:'ru',projects:[],read:async()=>{},write:async()=>{},retryProjects(){}}));
    assert.doesNotMatch(html,/Сначала создайте проект/);assert.match(html,/<select disabled=""/);
    assert.match(html,state.projectsLoading?/Читаем проекты/:/Не удалось обновить список проектов/);
  }
});

for(const locale of ['ru','uk']) test(`${locale}: empty bulk selection explains missing cars, not a missing Telegram connection`,()=>{
  const client=new QueryClient();
  client.setQueryData(['auction','telegram-preview','copart',9,'active'],{
    ...context,project_id:9,source_key:'copart',collection:'active',lots:[]});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionTelegramPreview,{
      source:'copart',projectId:9,projectName:'New project',locale,collection:'active',nowMs:Date.parse(context.generated_at),
      read:async()=>{throw Error('No traffic during render');},write:async()=>{throw Error('No sends during render');},
      imageUrl:v=>v,close(){},returnFocus:React.createRef(),fallbackFocus:React.createRef()})));
    assert.match(html,locale==='ru'?/Обновите данные проекта.*Подключать Telegram повторно не нужно/:/Оновіть дані проєкту.*Підключати Telegram повторно не потрібно/);
    assert.match(html,/<button[^>]*disabled=""[^>]*>.*?(?:Отправить выбранные|Надіслати вибрані)/);
  } finally {client.clear();}
});

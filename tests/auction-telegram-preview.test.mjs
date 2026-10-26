import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { auctionTelegramMessage, AuctionTelegramPreview } = await import('../app/auction-telegram-preview.tsx');
const { AUCTION_QUERY_KEY } = await import('../app/auction-time-client.ts');
const { SOURCE_CATALOG } = await import('../app/source-catalog.ts');
const { readCalendarPage } = await import('../app/auction-calendar-link.ts');
const context = { generated_at:'2026-10-20T10:00:00Z', local_date:'2026-10-20',computer_timezone:'Europe/Warsaw',
  day_start_utc:'2026-10-19T22:00:00Z',day_end_utc:'2026-10-20T22:00:00Z' };
const base = {id:40,source_key:'copart',source_listing_id:'65507986',url:'https://www.copart.com/lot/65507986',title:'Audi',
  image:'https://cs.copart.com/fixture.jpg',details:{mileage:{value:0,unit:'mi',accuracy:'ACTUAL'},primary_damage:'FRONT END'},
  schedule:{utc_at:'2026-10-20T13:30:00Z'},time:{event_kind:'START',precision:'EXACT',local_date:'2026-10-20',
    local_datetime:'2026-10-20T15:30:00+02:00',is_today:true,remaining_seconds:999999,time_state:'UPCOMING_START'}};
const message=(overrides={},locale='ru',ctx=context)=>auctionTelegramMessage({...base,...overrides},ctx,locale);
const calendarEntity=result=>(result.caption_entities||result.entities||[]).find(entity=>entity.type==='text_link'&&entity.url===result.reply_markup?.inline_keyboard[0][0].url);
test('preview formats a vehicle caption without the project name, recalculating time at the recorded instant',()=>{
  const result=message();
  assert.equal(result.photo,base.image);assert.equal(result.limit,1024);
  for(const part of ['Audi','Copart · № 65507986','Одометр: 0 mi · ACTUAL','20 октября 2026, 15:30','Europe/Warsaw','До начала: 3 ч 30 мин']) assert.ok(result.text.includes(part),part);
  assert.doesNotMatch(result.text,/Проект:|My project/);assert.ok(result.text.startsWith('🚙 Audi\n\n'));
  assert.equal(result.link,base.url);assert.ok(result.caption_entities.some(entity=>entity.type==='text_link'&&entity.url===base.url));
});
test('calendar is a clickable part of the vehicle caption/text, not only a separate preview action',()=>{
  for(const locale of ['ru','uk']) for(const photo of [base.image,null]) {
    const result=message({title:'🚘 2026 CHEVROLET TAHOE C1500 HIGH COUNTRY',image:photo},locale);
    const label=locale==='uk'?'Додати до календаря':'Добавить в календарь';
    assert.ok(result.text.includes(label),'Calendar must remain visible when reading the message text');
    const entity=calendarEntity(result);
    assert.equal(entity?.type,'text_link');
    assert.equal(result.text.slice(entity.offset,entity.offset+entity.length),label);
    assert.equal(entity.url,result.reply_markup.inline_keyboard[0][0].url);
    assert.equal(photo?result.entities:result.caption_entities,undefined);
    assert.equal(result.photo,photo);assert.ok(result.text.length<=(photo?1024:4096));
    const event=readCalendarPage(new URL(entity.url).hash,Date.parse(context.generated_at));
    assert.match(event.calendar.title,/CHEVROLET TAHOE/);
    assert.equal(event.calendar.start_utc,'2026-10-20T13:30:00.000Z');
    assert.equal(event.listing,base.url);assert.match(event.calendar.ics,/TRIGGER:-PT15M/);
  }
});
test('the reported Tahoe preview includes its own calendar link with the exact Warsaw start',()=>{
  const ctx={...context,generated_at:'2026-10-22T10:11:00Z',local_date:'2026-10-22',
    day_start_utc:'2026-10-21T22:00:00Z',day_end_utc:'2026-10-22T22:00:00Z'};
  const result=message({source_listing_id:'69046336',title:'2026 CHEVROLET TAHOE C1500 HIGH COUNTRY',
    url:'https://www.copart.com/lot/69046336/salvage-2026-chevrolet-tahoe-c1500-high-country-tx-dallas',
    details:{primary_damage:'REAR END',mileage:{value:6155,unit:'mi',accuracy:'ACTUAL'}},
    schedule:{utc_at:'2026-10-22T17:00:00Z'},time:{...base.time,local_date:'2026-10-22',local_datetime:'2026-10-22T19:00:00+02:00'}},'ru',ctx);
  assert.match(result.text,/22 октября 2026, 19:00\n🌍 Часовой пояс: Europe\/Warsaw\n⏳ До начала: 6 ч 49 мин/);
  const link=calendarEntity(result),event=readCalendarPage(new URL(link.url).hash,Date.parse(ctx.generated_at));
  assert.equal(result.text.slice(link.offset,link.offset+link.length),'Добавить в календарь');
  assert.equal(event.calendar.start_utc,'2026-10-22T17:00:00.000Z');assert.match(event.calendar.title,/CHEVROLET TAHOE/);
  assert.equal(event.listing,result.link);
});
test('an exact upcoming START message carries a public Google Calendar button with prefilled vehicle, instant and listing',()=>{
  const result=auctionTelegramMessage(base,context,'ru',SOURCE_CATALOG,'');
  const button=result.reply_markup?.inline_keyboard?.[0]?.[0];
  assert.ok(button,'The message must carry its calendar action, not just a local desktop download');
  const url=new URL(button.url);
  assert.equal(url.origin,'https://calendar.google.com');assert.equal(url.searchParams.get('action'),'TEMPLATE');
  assert.match(url.searchParams.get('text'),/Audi/);
  assert.equal(url.searchParams.get('dates'),'20261020T133000Z/20261020T133000Z');
  assert.ok(url.searchParams.get('details').includes(base.url));
  assert.doesNotMatch(url.href,/localhost|127\.0\.0\.1|token|project|data:/);
  assert.match(result.calendar.ics,/TRIGGER:-PT15M\r\n/);
});
test('the default vehicle message and its preview use the deployed calendar-page action, with Google retained as a fallback',()=>{
  for(const locale of ['ru','uk']) {
    const result=message({},locale);
    const button=result.reply_markup.inline_keyboard[0][0];
    assert.equal(new URL(button.url).origin,'https://searchcar-auction-calendar-preview.pages.dev');
    assert.equal(result.calendar_page_url,button.url);
    assert.equal(button.text,locale==='uk'?'Додати до календаря':'Добавить в календарь');
    const event=readCalendarPage(new URL(button.url).hash,Date.parse(context.generated_at));
    assert.equal(event.calendar.start_utc,'2026-10-20T13:30:00.000Z');assert.equal(event.listing,base.url);
    const client=new QueryClient();
    client.setQueryData([...AUCTION_QUERY_KEY,'telegram-preview','copart',9,'today'],{...context,project_id:9,source_key:'copart',lots:[base]});
    try {
      const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionTelegramPreview,{
        source:'copart',projectId:9,projectName:'Project',locale,nowMs:Date.parse(context.generated_at),read:async()=>({}),imageUrl:v=>v,close(){},returnFocus:React.createRef(),fallbackFocus:React.createRef()})));
      assert.ok(html.includes(button.url.replaceAll('&','&amp;')));
      assert.match(html,/<p class="telegram-preview-text">[\s\S]*?<a class="telegram-message-calendar"/);
      assert.match(html,/<strong>Audi<\/strong>/);assert.match(html,/<strong>(Аукцион|Аукціон):<\/strong>/);
      assert.match(html,/class="telegram-message-listing"/);
      assert.match(html,locale==='uk'?/>Додати до календаря<\//:/>Добавить в календарь<\//);
      assert.match(html,/Google Calendar/);
    } finally {client.clear();}
  }
});
test('an event that cannot fit the portable-page limit retains a complete Google fallback instead of an invalid message action',()=>{
  const result=message({title:'🚘'.repeat(120),url:base.url+'/'+'%'.repeat(465),round:{id:'%'.repeat(128)}});
  assert.equal(result.calendar_page_url,null);
  const button=result.reply_markup.inline_keyboard[0][0];assert.equal(new URL(button.url).origin,'https://calendar.google.com');
  assert.ok(button.url.length<=2048);assert.ok(new URL(button.url).searchParams.get('details').includes('%'.repeat(465)));
});
test('a configured public calendar page is attached to the same vehicle message, never a separate local link',()=>{
  const result=auctionTelegramMessage(base,context,'ru',SOURCE_CATALOG,'https://calendar.example.com/');
  assert.match(result.text,/Audi\n\n🏷 Аукцион: Copart · № 65507986/);
  const button=result.reply_markup.inline_keyboard[0][0];
  assert.equal(button.text,'Добавить в календарь');
  const url=new URL(button.url);assert.equal(url.origin,'https://calendar.example.com');
  const event=readCalendarPage(url.hash,Date.parse(context.generated_at));
  assert.equal(event.calendar.start_utc,'2026-10-20T13:30:00.000Z');assert.equal(event.listing,base.url);
  const invalid=auctionTelegramMessage(base,context,'ru',SOURCE_CATALOG,'http://localhost/');
  assert.equal(new URL(invalid.reply_markup.inline_keyboard[0][0].url).origin,'https://calendar.google.com');
  const end=auctionTelegramMessage({...base,time:{...base.time,event_kind:'END'}},context,'ru',SOURCE_CATALOG,'https://calendar.example.com/');
  assert.equal(end.reply_markup,undefined);
});
test('today preview excludes future, archive, uncertain local days and invalid clock context',()=>{
  for(const overrides of [{project_search_status:'NOT_FOUND_IN_SEARCH'},{time:{...base.time,is_today:false}},
    {time:{...base.time,is_today:null}},{time:{...base.time,local_date:'2026-10-21'}}]) assert.equal(message(overrides),null);
  assert.equal(message({},'ru',{...context,generated_at:'invalid'}),null);
});
test('event kind, precision and elapsed schedule never imply a LIVE or SOLD outcome',()=>{
  const end=message({time:{...base.time,event_kind:'END'}}).text;
  assert.match(end,/Окончание:|До окончания: 3 ч 30 мин/);assert.doesNotMatch(end,/До начала/);
  const passed=message({schedule:{utc_at:'2026-10-20T09:00:00Z'}}).text;
  assert.match(passed,/время прошло — результат не проверен/);assert.doesNotMatch(passed,/LIVE|SOLD|Продан|До начала/);
  assert.equal(message({time:{...base.time,event_kind:'END'}}).reply_markup,undefined);
  assert.equal(message({schedule:{utc_at:'2026-10-20T09:00:00Z'}}).reply_markup,undefined);
  for(const overrides of [{time:{...base.time,event_kind:'END'}},{schedule:{utc_at:'2026-10-20T09:00:00Z'}},
    {time:{...base.time,precision:'DATE_ONLY',local_datetime:null,remaining_seconds:null}}]) {
    const result=message(overrides);assert.equal(calendarEntity(result),undefined);
    assert.ok(result.caption_entities.some(entity=>entity.type==='text_link'&&entity.url===base.url));
    assert.doesNotMatch(result.text,/Добавить в календарь|Добавить в Google Calendar/);
  }
  for(const time of [{...base.time,event_kind:'UNKNOWN'},{...base.time,precision:'DATE_ONLY',local_datetime:null,remaining_seconds:null}]) {
    const text=message({time}).text;assert.doesNotMatch(text,/До начала|До окончания/);
  }
  assert.match(message({time:{...base.time,precision:'DATE_ONLY',local_datetime:null,remaining_seconds:null}}).text,/точное время не указано/);
});
test('preview validates links and images, strips URL parameters and uses a text-only fallback',()=>{
  assert.equal(message({url:base.url+'?token=private#fragment'}).link,base.url);
  for(const image of ['http://cs.copart.com/x.jpg','https://other.invalid/photo.jpg','https://vis.iaai.com/x.jpg','https://user:pass@cs.copart.com/x.jpg',
    '/storage/../secret','/storage/%2e%2e/secret','/storage/\\other','/storage/photo.jpg?token=private','javascript:alert(1)']) {
    const result=message({image});assert.equal(result.photo,null);assert.equal(result.limit,4096);
  }
  assert.equal(message({image:'/storage/fixture-photo.svg'}).photo,'/storage/fixture-photo.svg');
  for(const url of ['https://www.copart.com/lot/12345678','https://www.iaai.com/VehicleDetail/123~US','javascript:alert(1)']) {
    const result=message({url});assert.equal(result.link,null);assert.match(result.text,/Ссылка на лот недоступна/);
  }
  const iaa=message({source_key:'iaa',url:'https://www.iaai.com/VehicleDetail/12345678~US',image:'https://vis.iaai.com/photo.jpg'});
  assert.equal(iaa.photo,'https://vis.iaai.com/photo.jpg');assert.match(iaa.text,/IAA · №/);
});
test('captions stay within Telegram limits, keep a complete link and do not split emoji',()=>{
  const longLink=base.url+'/'+'x'.repeat(465);
  const result=auctionTelegramMessage({...base,url:longLink,title:'🚙'.repeat(500),details:{primary_damage:'🚘'.repeat(500),
    mileage:{value:12345,unit:'km',accuracy:'x'.repeat(500)}}},context,'uk');
  assert.ok(result.text.length<=1024);assert.ok(result.caption_entities.some(entity=>entity.type==='text_link'&&entity.url===longLink));assert.ok(result.text.includes('…'));
  assert.doesNotMatch(result.text,/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/);
  assert.doesNotMatch(result.text,/Проєкт:/);assert.match(result.text,/До початку:|Одометр:/);
  const entity=calendarEntity(result);
  assert.equal(result.text.slice(entity.offset,entity.offset+entity.length),'Додати до календаря');
  assert.ok(entity.offset>=0 && entity.offset+entity.length<=result.text.length);
});
test('invalid mileage stays unknown, and raw titles are plain escaped text, not executable markup',()=>{
  for(const mileage of [{value:-1,unit:'mi'},{value:100,unit:'unknown'},{value:'100',unit:'km'},{value:Infinity,unit:'mi'}]) assert.match(message({details:{mileage}}).text,/Одометр: нет данных/);
  assert.doesNotMatch(message({title:'car\n\u202eSpoof'}).text,/\u202e|car\nSpoof/);
  const client=new QueryClient();
  client.setQueryData([...AUCTION_QUERY_KEY,'telegram-preview','copart',9,'today'],{...context,project_id:9,source_key:'copart',lots:[{...base,title:'<script>alert(1)</script>'}]});
  try {
    const html=renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionTelegramPreview,{
      source:'copart',projectId:9,projectName:'Project',locale:'ru',nowMs:Date.parse(context.generated_at),read:async()=>({}),imageUrl:v=>v,close(){},returnFocus:React.createRef(),fallbackFocus:React.createRef()})));
    assert.match(html,/&lt;script&gt;alert\(1\)&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>|>Отправить<\/button>/);
    assert.match(html,/Выберите автомобили/);assert.match(html,/type="checkbox"/);
    assert.match(html,/Telegram · Project · Сегодня/);assert.doesNotMatch(html,/Проект: Project/);
  } finally {client.clear();}
});

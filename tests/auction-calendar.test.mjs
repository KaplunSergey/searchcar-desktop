import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const {auctionCalendar}=await import('../app/auction-calendar.ts');
const {AuctionCalendarActions}=await import('../app/auction-calendar-actions.tsx');
const {calendarPageLink,AUCTION_CALENDAR_PAGE_URL}=await import('../app/auction-calendar-link.ts');
const NOW=Date.parse('2026-10-21T10:00:00Z');
const lot={id:40,source_key:'copart',source_listing_id:'65507986',title:'2026 Audi A5',url:'https://www.copart.com/lot/65507986?token=private#fragment',
  details:{},round:{id:'round-1'},schedule:{utc_at:'2026-10-21T13:30:00Z'},time:{event_kind:'START',precision:'EXACT'}};
const build=(overrides={},now=NOW,locale='ru')=>auctionCalendar({...lot,...overrides},now,locale);
const unfold=ics=>ics.replace(/\r\n /g,'');
test('calendar file preserves an absolute instant, one event, listing link and 15-minute display alarm without inventing end/duration',()=>{
  const result=build();assert.ok(result.available);
  const ics=unfold(result.ics);
  for(const line of ['BEGIN:VCALENDAR','BEGIN:VEVENT','DTSTAMP:20261021T100000Z','DTSTART:20261021T133000Z',
    'SUMMARY:2026 Audi A5 · Copart № 65507986','URL:https://www.copart.com/lot/65507986','ACTION:DISPLAY','TRIGGER:-PT15M']) assert.ok(ics.includes(line+'\r\n'),line);
  assert.equal((ics.match(/BEGIN:VEVENT/g)||[]).length,1);assert.equal((ics.match(/BEGIN:VALARM/g)||[]).length,1);
  assert.doesNotMatch(ics,/DTEND:|DURATION:|ATTENDEE|ORGANIZER|token=|fragment|Проект:/);
  assert.ok(ics.endsWith('END:VCALENDAR\r\n'));assert.equal(result.filename,'SearchCar-copart-65507986.ics');
});
test('calendar UTC survives US DST boundaries and explicit offsets without using the machine timezone',()=>{
  for(const [raw,expected] of [['2026-11-01T01:30:00-04:00','20261101T053000Z'],['2026-11-01T01:30:00-05:00','20261101T063000Z'],
    ['2026-03-08T03:30:00-04:00','20260308T073000Z']]) {
    const result=build({schedule:{utc_at:raw}},Date.parse('2026-01-01T00:00:00Z'));
    assert.ok(result.available);assert.match(result.ics,new RegExp(`DTSTART:${expected}`));
    assert.equal(new URL(result.google_url).searchParams.get('dates'),`${expected}/${expected}`);
  }
});
test('calendar blocks uncertain starts, END, missing/invalid instants and expired/inactive lots',()=>{
  for(const time of [{event_kind:'END',precision:'EXACT'},{event_kind:'UNKNOWN',precision:'EXACT'},{event_kind:'START',precision:'DATE_ONLY'}]) assert.equal(build({time}).available,false);
  for(const utc_at of [null,'TBD','2026-10-22T10:00:00','2026-02-30T10:00:00Z','2026-13-01T10:00:00Z','2026-10-22T10:00:00-00:00','2026-10-22T10:00:00+14:30']) assert.equal(build({schedule:{utc_at}}).available,false,utc_at);
  assert.equal(build({},NaN).available,false);assert.equal(build({},1e30).available,false);
  assert.equal(build({schedule:{utc_at:'2026-10-21T10:00:00Z'}}).issue,'PAST');
  for(const auction_phase of ['LIVE','CLOSED','CANCELLED']) assert.equal(build({details:{auction_phase}}).issue,'UNAVAILABLE');
  assert.equal(build({project_search_status:'NOT_FOUND_IN_SEARCH'}).available,false);
});
test('calendar does not emit unsafe links or identities, while IAA accepts its actual vehicle detail ID',()=>{
  for(const url of ['javascript:alert(1)','https://www.copart.com/lot/12345678','https://evil.invalid/lot/65507986','https://user:pass@www.copart.com/lot/65507986']) assert.equal(build({url}).issue,'NO_LINK');
  const result=build({source_key:'iaa',url:'https://www.iaai.com/VehicleDetail/12345678~US'});
  assert.ok(result.available);assert.match(result.ics,/URL:https:\/\/www.iaai.com\/VehicleDetail\/12345678~US/);
});
test('ICS escapes text and folds UTF-8 at 75 octets, preserving unicode and preventing injected fields',()=>{
  const title='🚗Авто, тест; \\'.repeat(15)+'\r\nBEGIN:VEVENT\nATTENDEE:evil';
  const result=build({title});assert.ok(result.available);
  for(const line of result.ics.split('\r\n')) assert.ok(Buffer.byteLength(line,'utf8')<=75,line);
  const ics=unfold(result.ics);
  assert.match(ics,/\\,|\\;|\\\\/);
  assert.equal((ics.match(/^BEGIN:VEVENT$/gm)||[]).length,1);
  assert.doesNotMatch(ics,/\r\nATTENDEE:|\uFFFD/);
  assert.equal(new URL(result.google_url).searchParams.get('text'),result.title);
});
test('calendar URL respects the desktop 2048-character contract without losing the full listing URL',()=>{
  const url='https://www.copart.com/lot/65507986/'+'%'.repeat(470);
  const result=build({title:'🚗'.repeat(120),url});assert.ok(result.available);
  assert.ok(result.google_url.length<=2048,`URL length: ${result.google_url.length}`);
  assert.ok(new URL(result.google_url).searchParams.get('details').includes(url));
  assert.match(unfold(result.ics),/SUMMARY:🚗/);
  const html=renderToStaticMarkup(React.createElement(AuctionCalendarActions,{calendar:result,locale:'ru',nowMs:NOW}));
  assert.match(html,/дополнительно сокращено/);
});
test('UID stays stable across schedule moves of one round, changes for another round, and warns when reminder time has passed',()=>{
  const first=build(), moved=build({schedule:{utc_at:'2026-10-22T13:30:00Z'}}), next=build({round:{id:'round-2'}});
  const uid=result=>unfold(result.ics).match(/UID:([^\r]+)/)[1];
  assert.equal(uid(first),uid(moved));assert.notEqual(uid(first),uid(next));
  assert.equal(build({schedule:{utc_at:'2026-10-21T10:10:00Z'}}).reminder_late,true);
  assert.equal(build({schedule:{utc_at:'2026-10-21T10:15:00Z'}}).reminder_late,false);
});
test('preview actions expose the same public URL, explain confirmation/export limits, and stay absent for past events',()=>{
  for(const locale of ['ru','uk']) {
    const result=build({},NOW,locale);
    const html=renderToStaticMarkup(React.createElement(AuctionCalendarActions,{calendar:result,locale,nowMs:NOW}));
    assert.ok(html.includes('calendar.google.com/calendar/render?'));
    assert.match(html,/download="SearchCar-copart-65507986.ics"/);
    assert.ok(html.includes(locale==='ru'?'проверьте напоминание за 15 минут':'перевірте нагадування за 15 хвилин'));
    assert.doesNotMatch(html,/localhost|127\.0\.0\.1|Проект:/);
    const past=renderToStaticMarkup(React.createElement(AuctionCalendarActions,{calendar:build({},Date.parse('2026-10-22T10:00:00Z')),locale,nowMs:NOW}));
    assert.doesNotMatch(past,/href=|download=/);
    const after=renderToStaticMarkup(React.createElement(AuctionCalendarActions,{calendar:result,locale,nowMs:Date.parse('2026-10-22T00:00:00Z')}));
    assert.doesNotMatch(after,/href=|download=/);
  }
});
test('calendar preview opens the published event page with a direct Google fallback and never trusts another page host',()=>{
  const calendar=build(), pageUrl=calendarPageLink(lot,NOW,'ru',AUCTION_CALENDAR_PAGE_URL);
  const render=page=>renderToStaticMarkup(React.createElement(AuctionCalendarActions,{calendar,pageUrl:page,locale:'ru',nowMs:NOW}));
  const html=render(pageUrl);assert.ok(html.includes(`href="${pageUrl}"`));
  assert.match(html,/>Добавить в календарь<\//);assert.match(html,/>Google Calendar напрямую<\//);
  assert.match(html,/Импорт Apple\/Outlook на телефоне ещё требует проверки/);
  for(const invalid of ['javascript:alert(1)','https://evil.org/#event',AUCTION_CALENDAR_PAGE_URL.replace('.pages.dev','.pages.dev.evil.org')+'#event',pageUrl+'x'.repeat(2048)]) {
    const safe=render(invalid);assert.doesNotMatch(safe,/>Добавить в календарь<\//);assert.match(safe,/>Добавить в Google Calendar<\//);
    assert.ok(safe.includes(calendar.google_url.replaceAll('&','&amp;')));
  }
});

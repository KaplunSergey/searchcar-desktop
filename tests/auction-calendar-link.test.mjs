import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
const {calendarPageLink,readCalendarPage}=await import('../app/auction-calendar-link.ts');
const now=Date.parse('2026-10-21T10:00:00Z');
const lot={source_key:'copart',source_listing_id:'65507986',title:'2026 Audi A5 🚙',url:'https://www.copart.com/lot/65507986?token=secret#private',
  details:{},round:{id:'round-1'},schedule:{utc_at:'2026-10-21T13:30:00Z'},time:{event_kind:'START',precision:'EXACT'},project_name:'PRIVATE PROJECT',token:'SECRET'};
const link=(overrides={},page='https://calendar.example.com/')=>calendarPageLink({...lot,...overrides},now,'uk',page);
test('public calendar link preserves only the event fields in a fragment and reconstructs the same instant, URL and alarm',()=>{
  const href=link();assert.ok(href,'A confirmed future start must produce a portable calendar-page link');
  const url=new URL(href);assert.equal(url.origin,'https://calendar.example.com');assert.equal(url.search,'');
  assert.ok(href.length<=2048);assert.doesNotMatch(decodeURIComponent(href),/PRIVATE PROJECT|SECRET|token=|private/);
  const event=readCalendarPage(url.hash,now);assert.ok(event);assert.equal(event.locale,'uk');
  assert.equal(event.calendar.start_utc,'2026-10-21T13:30:00.000Z');assert.match(event.calendar.title,/Audi A5 🚙/);
  assert.match(event.calendar.ics,/TRIGGER:-PT15M\r\n/);assert.equal(event.listing,'https://www.copart.com/lot/65507986');
  assert.equal(new URL(event.calendar.google_url).searchParams.get('dates'),'20261021T133000Z/20261021T133000Z');
});
test('link rejects private/non-HTTPS endpoints and does not export uncertain, expired or missing lots',()=>{
  for(const endpoint of ['http://calendar.example.com/','https://localhost/','https://127.0.0.1/','https://[::1]/','https://10.0.0.1/','https://192.168.1.2/','https://user:secret@calendar.example.com/','https://calendar.example.com/?token=x','https://calendar.example.com/#x','https://calendar.example.com:8443/']) assert.equal(link({},endpoint),null,endpoint);
  assert.equal(link({time:{event_kind:'END',precision:'EXACT'}}),null);
  assert.equal(link({project_search_status:'NOT_FOUND_IN_SEARCH'}),null);
  assert.equal(link({schedule:{utc_at:'2026-10-21T09:00:00Z'}}),null);
});
test('untrusted fragments fail closed for malformed, extra, overlong, invalid-time and unsafe-source input',()=>{
  const good=new URL(link()).hash;
  const data=JSON.parse(decodeURIComponent(good.slice(1)));
  for(const hash of ['', '#%zz','#null','#[]','#'+encodeURIComponent('x'.repeat(9000)),
    '#'+encodeURIComponent(JSON.stringify({...data,token:'bad'})),
    '#'+encodeURIComponent(JSON.stringify({...data,v:2})),
    '#'+encodeURIComponent(JSON.stringify({...data,locale:'en'})),
    '#'+encodeURIComponent(JSON.stringify({...data,url:'javascript:alert(1)'})),
    '#'+encodeURIComponent(JSON.stringify({...data,url:'https://evil.invalid/lot/65507986'})),
    '#'+encodeURIComponent(JSON.stringify({...data,start:'2026-02-30T10:00:00Z'}))]) assert.equal(readCalendarPage(hash,now),null,hash.slice(0,100));
  assert.equal(readCalendarPage(good,Date.parse('2026-10-22T10:00:00Z')),null);
  assert.equal(readCalendarPage(good,NaN),null);
});
test('IAA calendar links preserve vehicle-detail identity and long unicode titles fit without damaging listing URL',()=>{
  const iaa=link({source_key:'iaa',url:'https://www.iaai.com/VehicleDetail/12345678~US'});
  assert.equal(readCalendarPage(new URL(iaa).hash,now).listing,'https://www.iaai.com/VehicleDetail/12345678~US');
  const long=link({title:'🚘'.repeat(120),url:'https://www.copart.com/lot/65507986/'+'%'.repeat(465)});
  assert.ok(long);assert.ok(long.length<=2048);
  const result=readCalendarPage(new URL(long).hash,now);assert.ok(result);assert.ok(result.listing.endsWith('%'.repeat(465)));
  assert.doesNotMatch(result.calendar.title,/\uFFFD/);
});

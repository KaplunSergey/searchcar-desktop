import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
const {calendarIcsResponse}=await import('../app/auction-calendar-response.ts');
const NOW=Date.parse('2026-10-24T08:00:00Z');
const payload={v:1,source:'copart',id:'65507986',title:'Audi 🚙',url:'https://www.copart.com/lot/65507986',
  start:'2026-10-25T13:30:00.000Z',round:'round-1',locale:'ru'};
const path=value=>'https://calendar.fixture.test/event.ics?'+new URLSearchParams({event:encodeURIComponent(JSON.stringify(value))});
test('HTTPS calendar response is inline, private-cache safe and contains one event, listing and 15-minute alarm',async()=>{
  const response=calendarIcsResponse(new Request(path(payload)),NOW);
  assert.equal(response.status,200);
  assert.equal(response.headers.get('content-type'),'text/calendar; charset=utf-8');
  assert.equal(response.headers.get('content-disposition'),'inline; filename="SearchCar-copart-65507986.ics"');
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('referrer-policy'),'no-referrer');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  const text=(await response.text()).replace(/\r\n /g,'');
  assert.match(text,/DTSTART:20261025T133000Z\r\n/);assert.match(text,/TRIGGER:-PT15M\r\n/);
  assert.ok(text.includes(payload.url));assert.equal((text.match(/BEGIN:VEVENT/g)||[]).length,1);
  assert.doesNotMatch(text,/ATTENDEE:|ORGANIZER:|DTEND:|METHOD:REQUEST/);
});
test('calendar endpoint rejects invalid, expired, private or injected event data without echoing it',async()=>{
  for(const value of [null,{},[],{...payload,token:'sensitive-fixture'},
    {...payload,url:'https://127.0.0.1/private'}, {...payload,url:'javascript:alert(1)'},
    {...payload,start:'2026-10-23T13:30:00Z'}, {...payload,start:'2026-02-30T10:00:00Z'},
    {...payload,id:'x\r\nSet-Cookie:evil'}]) {
    const response=calendarIcsResponse(new Request(path(value)),NOW);
    assert.equal(response.status,400);assert.equal(response.headers.get('cache-control'),'no-store');
    assert.equal(await response.text(),'Calendar event unavailable.');
  }
});
test('calendar methods, duplicate/unknown query fields and request size fail closed; HEAD has no body',async()=>{
  const url=path(payload);
  for(const suffix of ['&event=x','&token=private']) assert.equal(calendarIcsResponse(new Request(url+suffix),NOW).status,400);
  assert.equal(calendarIcsResponse(new Request('https://calendar.fixture.test/event.ics'),NOW).status,400);
  assert.equal(calendarIcsResponse(new Request(url.replace('/event.ics','/wrong')),NOW).status,404);
  assert.equal(calendarIcsResponse(new Request(url+'x'.repeat(13000)),NOW).status,414);
  const post=calendarIcsResponse(new Request(url,{method:'POST'}),NOW);
  assert.equal(post.status,405);assert.equal(post.headers.get('allow'),'GET, HEAD');
  const head=calendarIcsResponse(new Request(url,{method:'HEAD'}),NOW);
  assert.equal(head.status,200);assert.equal(await head.text(),'');assert.match(head.headers.get('content-type'),/^text\/calendar/);
});
test('same calendar response supports IAA, unicode escaping and an exact immutable UTC instant',async()=>{
  const data={...payload,source:'iaa',title:'Auto, 🚗;\nBEGIN:VEVENT',url:'https://www.iaai.com/VehicleDetail/12345678~US',locale:'uk'};
  const response=calendarIcsResponse(new Request(path(data)),NOW);
  const text=await response.text();
  assert.equal(response.status,200);assert.equal((text.match(/^BEGIN:VEVENT\r?$/gm)||[]).length,1);
  assert.match(text,/DTSTART:20261025T133000Z\r\n/);
  assert.match(text,/SUMMARY:Auto\\, 🚗\\;/);assert.ok(text.includes(data.url));
  for(const line of text.split('\r\n')) assert.ok(Buffer.byteLength(line,'utf8')<=75);
});

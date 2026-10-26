import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
const { calendarPageLink, readCalendarPage } = await import('../app/auction-calendar-link.ts');
const now = Date.parse('2026-10-25T10:00:00Z');
const lot = { source_key:'copart',source_listing_id:'65507986',title:'🚙 Tahoe <test> & авто',
  url:'https://www.copart.com/lot/65507986?token=private#private',details:{},round:{id:'round-1'},
  schedule:{utc_at:'2026-10-26T13:30:00Z'},time:{event_kind:'START',precision:'EXACT'} };
const event = (locale='ru', at=now, overrides={}) => {
  const link=calendarPageLink({...lot,...overrides},now,locale,'https://calendar.example.com/');
  return readCalendarPage(new URL(link).hash,at);
};

test('Outlook opens a draft with the exact UTC start, title and listing, without invented end or calendar write',()=>{
  const value=event();
  assert.ok(value.calendar.outlook_url, 'a Windows user needs a direct Outlook compose action');
  const url=new URL(value.calendar.outlook_url);
  assert.equal(url.origin,'https://outlook.live.com');
  assert.equal(url.pathname,'/calendar/deeplink/compose');
  assert.equal(url.searchParams.get('rru'),'addevent');
  assert.equal(url.searchParams.get('path'),'/calendar/action/compose');
  assert.equal(Date.parse(url.searchParams.get('startdt')),Date.parse(lot.schedule.utc_at));
  assert.match(url.searchParams.get('subject'),/Tahoe <test> & авто/);
  assert.ok(url.searchParams.get('body').includes('https://www.copart.com/lot/65507986'));
  assert.match(url.searchParams.get('body'),/окончания неизвестно/);
  assert.match(url.searchParams.get('body'),/15 минут/);
  assert.equal(url.searchParams.has('enddt'),false);
  assert.doesNotMatch(value.calendar.outlook_url,/token|private|client_secret|access_token/);
});

for(const locale of ['ru','uk']) test(`${locale}: Apple reminder payload is data-only, timezone-independent and exactly 15 minutes early`,()=>{
  const value=event(locale);
  assert.ok(value.reminder, 'the validated event needs a portable reminder payload');
  assert.equal(value.reminder.available,true);
  const url=new URL(value.reminder.shortcut_url);
  assert.equal(url.protocol,'shortcuts:');
  assert.equal(url.hostname,'run-shortcut');
  assert.equal(url.searchParams.get('name'),'SearchCar Reminder');
  assert.equal(url.searchParams.get('input'),'text');
  const data=JSON.parse(url.searchParams.get('text'));
  assert.deepEqual(Object.keys(data).sort(),['alert_utc','locale','notes','start_utc','title','url','v']);
  assert.equal(data.v,1);assert.equal(data.locale,locale);
  assert.equal(data.start_utc,'2026-10-26T13:30:00.000Z');
  assert.equal(data.alert_utc,'2026-10-26T13:15:00.000Z');
  assert.equal(data.url,'https://www.copart.com/lot/65507986');
  assert.equal(data.title,value.calendar.title);
  assert.ok(data.notes.includes(data.url));
  assert.doesNotMatch(url.href,/token|private/);
});

test('native Shortcuts URL uses percent-encoded spaces, preserving Unicode, literal plus and JSON without form decoding',()=>{
  for(const locale of ['ru','uk']) {
    const value=event(locale,now,{title:'BMW i3 + Audi & авто 🚙'});
    const url=new URL(value.reminder.shortcut_url);
    // iOS percent-decodes custom URL parameters; it does not turn form-style + into spaces.
    const native=new Map(url.search.slice(1).split('&').map(field=>field.split('=').map(decodeURIComponent)));
    assert.equal(native.get('name'),'SearchCar Reminder');
    assert.equal(native.get('input'),'text');
    assert.ok(url.search.includes('name=SearchCar%20Reminder'));
    assert.ok(!url.search.includes('+'));
    assert.ok(url.search.includes('%2B'));
    const data=JSON.parse(native.get('text'));
    assert.equal(data.title,value.calendar.title);
    assert.ok(data.title.includes('BMW i3 + Audi & авто 🚙'));
    assert.ok(data.notes.includes(' '));
    assert.equal(data.locale,locale);
    assert.equal(data.alert_utc,'2026-10-26T13:15:00.000Z');
  }
});

test('Apple reminder refuses an elapsed alert rather than silently scheduling now or in the past',()=>{
  assert.equal(event('ru',Date.parse('2026-10-26T13:14:59Z')).reminder.available,true);
  assert.deepEqual(event('ru',Date.parse('2026-10-26T13:15:00Z')).reminder,{available:false,issue:'REMINDER_PAST'});
  assert.deepEqual(event('ru',Date.parse('2026-10-26T13:20:00Z')).reminder,{available:false,issue:'REMINDER_PAST'});
});

test('DST instants and maximum unicode titles preserve reminder timing and the complete listing',()=>{
  const value=event('uk',now,{title:'🚘'.repeat(120),schedule:{utc_at:'2026-11-01T01:30:00-05:00'},
    url:'https://www.copart.com/lot/65507986/'+'x'.repeat(450)});
  assert.ok(value.reminder?.available);
  const data=JSON.parse(new URL(value.reminder.shortcut_url).searchParams.get('text'));
  assert.equal(data.start_utc,'2026-11-01T06:30:00.000Z');
  assert.equal(data.alert_utc,'2026-11-01T06:15:00.000Z');
  assert.ok(data.url.endsWith('x'.repeat(450)));
  assert.doesNotMatch(data.title,/\uFFFD/);
});

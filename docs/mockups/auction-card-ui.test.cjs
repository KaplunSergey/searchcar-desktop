const { test } = require('node:test');
const assert = require('node:assert/strict');
const { ui, observed, scenarios, cardHTML, icon } = require('./auction-card-ui.js');
const now = Date.parse('2026-10-08T12:37:00+02:00');

test('UI icons share one decorative SVG grid; unknown names use the same grid', () => {
  for(const name of ['refresh','settings','down','up','clock','check','stop','alert','help','external','plus','close','menu','list','grid','unknown']){
    const html=icon(name);
    assert.match(html,/class="ui-icon" viewBox="0 0 24 24"/);
    assert.match(html,/stroke-width="2"/);
    assert.match(html,/aria-hidden="true" focusable="false"/);
    assert.doesNotMatch(html,/undefined|<text|font-size/);
  }
});

test('unknown source labels stay readable; blank is not a negative', () => {
  assert.equal(ui.translate('keys', null, 'ru'), 'Нет данных');
  assert.equal(ui.translate('keys', false, 'ru'), 'Ключей нет');
  assert.equal(ui.translate('damage', 'New vendor status', 'uk'), 'New vendor status');
  assert.equal(ui.translate('engine', 'Enhanced', 'ru'), 'Подготовка автомобиля');
});
test('confirmed live is independent of the planned timestamp', () => {
  assert.equal(ui.eventView({ state: 'live' }, now, 'ru').text, 'Аукцион идёт');
  const elapsed = ui.eventView({ state: 'scheduled', at: '2026-10-08T12:00:00+02:00', kind: 'start' }, now, 'ru');
  assert.equal(elapsed.text, 'Время по расписанию наступило');
  assert.equal(elapsed.countdown, null);
});
test('unknown precision, zone or event meaning must not produce a countdown', () => {
  for (const event of [
    { state: 'scheduled', date: '2026-10-08', precision: 'day' },
    { state: 'scheduled', at: '2026-10-08T15:30:00', kind: 'start' },
    { state: 'timed', at: '2026-10-08T15:30:00+02:00', kind: 'unknown' }
  ]) assert.equal(ui.eventView(event, now, 'ru').countdown, null);
  assert.match(ui.eventView({ state: 'scheduled', at: '2026-10-08T15:30:00+02:00', kind: 'start' }, now, 'ru').countdown, /2 ч 53 мин/);
});
test('zero odometer is retained; unknown and unreliable readings are not actual mileage', () => {
  assert.equal(ui.odometerView({ value: 0, unit: 'mi', status: 'Not Actual' }, 'ru').value, '0');
  assert.equal(ui.odometerView({ value: null, unit: 'mi' }, 'ru').value, 'Нет данных');
  for (const status of [null, 'Not Actual', 'Exceeds Mechanical Limits', 'Inoperable Digital Dash', 'New status']) {
    assert.equal(ui.odometerView({ value: 100, unit: 'mi', status }, 'ru').km, null);
  }
  assert.equal(ui.odometerView({ value: 100, unit: 'mi', status: 'Actual' }, 'ru').km, 161);
});
test('new undated lots stay out of the active selection; existing unknown dates need review', () => {
  assert.equal(ui.eligible({ state: 'future' }), false);
  assert.equal(ui.eligible({ state: 'unassigned' }), false);
  assert.equal(ui.eligible({ state: 'scheduled', date: '2026-10-08', precision: 'day' }), true);
});
test('raw source strings are escaped and links cannot become executable', () => {
  assert.equal(ui.escape('<img src=x onerror=alert(1)>'), '&lt;img src=x onerror=alert(1)&gt;');
  assert.equal(ui.safeLink('javascript:alert(1)'), null);
  assert.equal(ui.safeLink('https://evil.example/lot/12'), null);
  assert.equal(ui.safeLink('https://www.copart.com/lot/57006866'), 'https://www.copart.com/lot/57006866');
});
test('listing action opens the exact vehicle URL, never search or a different source', () => {
  const fixture={source:'Copart',name:'Audi',id:'65507986',event:{},odo:{}};
  for(const url of ['https://www.copart.com/lotSearchResults?searchCriteria=x',
    'https://www.iaai.com/Search?url=x','https://www.copart.com/',
    'https://www.iaai.com/VehicleDetail/46628754~US',null]) {
    assert.doesNotMatch(cardHTML({...fixture,url},now,'ru'),/class="source-link"/);
  }
  const copart='https://www.copart.com/ru/lot/65507986/salvage-2023-audi-a5-prestige-45-mi-detroit';
  assert.match(cardHTML({...fixture,url:copart},now,'ru'),new RegExp('href="'+copart+'"'));
  const iaa='https://www.iaai.com/VehicleDetail/46628754~US';
  assert.match(cardHTML({...fixture,source:'IAA',url:iaa},now,'uk'),/href="https:\/\/www.iaai.com\/VehicleDetail\/46628754~US"/);
});
test('invalid source timezone does not crash the card or invent a countdown', () => {
  assert.equal(ui.eventView({state:'scheduled',kind:'start',at:'2026-10-08T15:30:00+02:00',zone:'Unrecognised vendor zone'},now,'ru').countdown,null);
});
test('auction date is compact in RU/UK, retains a different year and uses the display timezone', () => {
  const event={state:'scheduled',kind:'start',at:'2026-10-08T15:30:00+02:00'};
  assert.equal(ui.eventView(event,now,'ru').date,'Сегодня · 8 октября');
  assert.equal(ui.eventView(event,now,'uk').date,'Сьогодні · 8 жовтня');
  assert.equal(ui.eventView({...event,at:'2026-09-30T15:30:00+02:00'},now,'ru').date,'30 сентября');
  assert.equal(ui.eventView({...event,at:'2027-01-09T15:30:00+01:00'},now,'ru').date,'9 января 2027');
  assert.equal(ui.eventView({...event,at:'2027-01-09T15:30:00+01:00'},now,'uk').date,'9 січня 2027');
  const nearMidnight=Date.parse('2026-12-31T23:30:00Z');
  assert.equal(ui.eventView({...event,at:'2027-01-01T00:10:00Z',zone:'America/New_York'},nearMidnight,'ru').date,'Сегодня · 31 декабря');
  assert.match(ui.eventView(event,now,'ru').fullDate,/2026/);
});
test('UI catalogue covers all audited damage values in both languages', () => {
  assert.equal(observed.IAA.damage.length,35);
  assert.equal(observed.Copart.damage.length,18);
  for(const source of ['IAA','Copart']) for(const raw of observed[source].damage) for(const lang of ['ru','uk']) {
    assert.notEqual(ui.translate('damage',raw,lang),raw,`${source}: ${raw}/${lang}`);
  }
});
test('every scenario renders for both sources and languages without reintroducing the footer', () => {
  for(const source of ['IAA','Copart']) for(const scenario of Object.values(scenarios)) for(const lang of ['ru','uk']) {
    const html=cardHTML({source,name:'<New vendor name>',id:'demo',odo:{value:null},damage:null,event:scenario.event,airbags:'Missing',action:'Bid now'},now,lang);
    assert.match(html,/&lt;New vendor name&gt;/);
    assert.doesNotMatch(html,/NaN|Invalid Date|<footer|ACV|<details|<New vendor name>/);
    assert.match(html,/Bid now/);
    if(source==='Copart')assert.doesNotMatch(html,/Отсутствуют|Відсутні/);
  }
});

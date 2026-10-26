import './app-loader.mjs';
import assert from 'node:assert/strict';
import test from 'node:test';
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
const {auctionTelegramMessage}=await import('../app/auction-telegram-preview.tsx');
const context={generated_at:'2026-10-24T10:30:00Z',local_date:'2026-10-24',computer_timezone:'Europe/Warsaw'};
const lot={id:1,source_key:'copart',source_listing_id:'72267656',title:'2025 CHEVROLET TAHOE K1500 HIGH COUNTRY',
  url:'https://www.copart.com/lot/72267656/salvage-2025-chevrolet-tahoe-k1500-high-country-ny-syracuse',image:'https://cs.copart.com/test.jpg',
  details:{primary_damage:'MINOR DENT/SCRATCHES',mileage:{value:36160,unit:'mi',accuracy:'ACTUAL'}},
  schedule:{utc_at:'2026-10-26T14:00:00Z'},time:{event_kind:'START',precision:'EXACT',local_date:'2026-10-26',
    local_datetime:'2026-10-26T15:00:00+01:00',is_today:false,remaining_seconds:185400,time_state:'UPCOMING_START'}};
const message=(value=lot,locale='ru')=>auctionTelegramMessage(value,context,locale,undefined,undefined,true);
const entities=value=>value.caption_entities||value.entities;
const fragment=(value,entity)=>value.text.slice(entity.offset,entity.offset+entity.length);
test('approved message has bold static labels, emoji, readable dates/days and hidden vehicle/calendar links',()=>{
  for(const photo of [lot.image,null]){
    const result=message({...lot,image:photo});const items=entities(result);
    assert.ok(result.text.startsWith('🚙 '+lot.title+'\n\n'));
    for(const text of ['🏷 Аукцион: Copart · № 72267656','🛠 Повреждения: MINOR DENT/SCRATCHES','🛣 Одометр: 36 160 mi · ACTUAL',
      '🗓 Начало: 26 октября 2026, 15:00','🌍 Часовой пояс: Europe/Warsaw','⏳ До начала: 2 д 3 ч 30 мин'])assert.ok(result.text.includes(text),text);
    assert.deepEqual(items.filter(e=>e.type==='bold').map(e=>fragment(result,e)),[lot.title,'Аукцион:','Повреждения:','Одометр:','Начало:','Часовой пояс:','До начала:']);
    const links=items.filter(e=>e.type==='text_link');assert.equal(links.length,2);
    assert.equal(fragment(result,links[0]),'Открыть автомобиль на Copart');assert.equal(links[0].url,lot.url);
    assert.equal(fragment(result,links[1]),'Добавить в календарь');assert.equal(links[1].url,result.calendar_page_url);
    assert.doesNotMatch(result.text,/https?:|\*\*|<b>/);assert.equal(result.photo,photo);
  }
});
test('preview and real sender format the same RU/UK photo/text payload and safe UTF-16 entities',()=>{
  const scenarios=['ru','uk'].flatMap(locale=>[lot,{...lot,image:null,title:'🚘 <b>car</b> & [link](evil)',details:{primary_damage:'🚘'.repeat(500),mileage:{value:1234.5,unit:'mi',accuracy:'ACTUAL'}}},
    {...lot,time:{...lot.time,local_date:null,local_datetime:null,precision:'UNKNOWN',remaining_seconds:null},schedule:{}}].map(value=>({lot:value,context,locale})));
  const localPython=process.platform==='win32'?'.desktop-build-venv-windows/Scripts/python.exe':'.desktop-build-venv/bin/python';
  const executable=process.env.SEARCHCAR_TEST_PYTHON||(existsSync(localPython)?localPython:'python');
  const python=spawnSync(executable,['-c',
    'import json,sys;sys.path.insert(0,"backend");from app.telegram_message import auction_message;print(json.dumps([auction_message(**s) for s in json.load(sys.stdin)],ensure_ascii=False))'],
    {input:JSON.stringify(scenarios),encoding:'utf8',env:{...process.env,PYTHONUTF8:'1'}});
  assert.equal(python.error,undefined,python.error?.message);
  assert.equal(python.status,0,python.stderr);const sent=JSON.parse(python.stdout);
  scenarios.forEach((scenario,index)=>{
    const preview=message(scenario.lot,scenario.locale);assert.equal(preview.text,sent[index].text);
    assert.deepEqual(entities(preview),sent[index].entities);assert.ok(preview.text.length<=preview.limit);
    let end=0;for(const item of entities(preview)){assert.ok(item.offset>=end&&item.length>0&&item.offset+item.length<=preview.text.length);end=item.offset+item.length;
      assert.doesNotMatch(fragment(preview,item),/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/);}
  });
});

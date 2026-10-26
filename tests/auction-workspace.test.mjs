import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
const { AuctionWorkspace, AuctionCard, auctionListingLink, AuctionSearchNotice } = await import("../app/auction-workspace.tsx");
const { responseErrorCode } = await import('../app/page.tsx');
const { SelectionSummary, selectionIssueMessage } = await import('../app/search-selection.tsx');
const { AuctionRemovalDialog } = await import('../app/auction-removal.tsx');
const context = { generated_at: "2026-10-11T10:00:00Z", local_date: "2026-10-11", computer_timezone: "Europe/Warsaw" };
const base = { id: 40, title: "Saved car", source_key: "copart", source_listing_id: "65507986", url: "https://www.copart.com/lot/65507986", image: null,
  details: {}, money_values: [], round: { id: "current", last_observed_at: "2026-10-11T09:00:00Z" }, schedule: {},
  time: { event_kind: "START", precision: "EXACT", local_date: "2026-10-11", local_datetime: "2026-10-11T15:30:00+02:00", is_today: true, remaining_seconds: 12600, time_state: "UPCOMING_START" } };
const card = (overrides = {}, locale = "ru") => renderToStaticMarkup(React.createElement(AuctionCard, { lot: { ...base, ...overrides }, context, locale, imageUrl: (value) => value }));
const renderWorkspace=(props={})=>{
    const client=new QueryClient();
    try{return renderToStaticMarkup(React.createElement(QueryClientProvider,{client},React.createElement(AuctionWorkspace,{
      source:'copart',view:'projects',projectId:null,carId:null,locale:'ru',
      projects:[{id:9,source_key:'copart',name:'Audi',cars:0}],projectsLoading:false,projectsError:false,retryProjects(){},
      runs:[],runsLoading:false,runsError:false,retryRuns(){},read:async()=>({}),navigate(){},imageUrl:v=>v,openListing(){},
      sourceTestMode:true,canSearch:true,refreshProjects:async()=>({projects:[]}),...props
    })));}finally{client.clear();}
  };
test('auction saved collections offer future, unconfirmed date and archive without source commands',()=>{
  const html=renderWorkspace({view:'project',projectId:9});
  assert.match(html,/>Будущие<\/button>/);
  assert.match(html,/>Без даты<\/button>/);
  assert.match(html,/>Архив<\/button>/);
  assert.match(renderWorkspace({locale:'uk'}),/>Майбутні<\/button>/);
});
test('a saved auction project offers a local Telegram preview without search permission',()=>{
  const html=renderWorkspace({view:'project',projectId:9,canSearch:false,sourceTestMode:false});
  assert.match(html,/<button[^>]+class="button telegram-button telegram-icon-button"[^>]+aria-label="Telegram"[^>]+aria-haspopup="dialog"/);
  assert.match(html,/<svg[^>]+class="telegram-logo"[^>]+aria-hidden="true"[^>]+focusable="false"/);
  assert.doesNotMatch(html,/<span>Telegram<\/span>/);
  assert.match(renderWorkspace({view:'project',projectId:9,locale:'uk'}),/aria-label="Telegram"/);
  assert.doesNotMatch(html,/telegram-project|Start|Получатель Telegram/);
  assert.doesNotMatch(renderWorkspace({view:'project',projectId:9,write:async()=>({})}),/telegram-project|Start|Получатель Telegram/);
});
test('unavailable project lot shows last known data, never a live countdown, Today or SOLD claim',()=>{
  const html=card({project_search_status:'NOT_FOUND_IN_SEARCH',details:{auction_phase:'LIVE'}});
  assert.match(html,/Недоступен в этом проекте/);
  assert.match(html,/Последнее известное расписание/);
  assert.doesNotMatch(html,/auction-countdown|Сегодня ·|Аукцион идёт|Текущая ставка|>Объявление|Продан/);
});
test('auction history offers project/state filters and reports site coverage rather than global percent',()=>{
  const html=renderWorkspace({view:'scans',runs:[{id:8,kind:'PROJECTS',status:'PARTIAL',progress:100,created_at:'2026-10-19T10:00:00Z',
    payload:{trigger:'MANUAL',project_ids:[9,11],project_plans:[{project_id:9,source_key:'copart',name:'Audi'},{project_id:11,source_key:'iaa',name:'Foreign IAA'}],
      project_statuses:{'9':'SUCCEEDED','11':'FAILED'},pagination:{'9':{mode:'FIRST_PAGE',pages_visited:1,total_pages:9,ads_seen:20,complete:true}}}}]});
  assert.match(html,/aria-label="Проект"/);
  assert.match(html,/aria-label="Состояние поиска"/);
  assert.match(html,/1 из 1 · выбранный охват/);
  assert.match(html,/data-status="SUCCEEDED"/);
  assert.match(html,/Ручной поиск/);
  assert.match(html,/aria-expanded="false"/);
  assert.doesNotMatch(html,/Foreign IAA|100%|1 из 9|2026-10-19T10:00:00Z<\/h2>/);
});
test('project status shares the title line and individual refresh shares the saved/today tabs',()=>{
  for(const [source,locale,refresh] of [['copart','ru','Обновить данные'],['iaa','uk','Оновити дані']]) {
    const html=renderWorkspace({source,locale,view:'project',projectId:9,
      write:async()=>({}),projects:[{id:9,source_key:source,name:'Audi',cars:0}],runs:[{id:1,status:'FAILED',payload:{project_ids:[9]}}]});
    assert.match(html,/<div class="auction-heading-line"><h1[^>]*>Audi<\/h1><div class="auction-heading-status">.*data-status="FAILED"/);
    const toolbar=html.split('<div class="auction-project-toolbar">')[1];
    assert.ok(toolbar,'Project tabs and action must share a toolbar');
    assert.ok(toolbar.startsWith('<div class="auction-tabs"'));
    assert.ok(toolbar.includes('class="project-actions auction-project-controls"'));
    assert.ok(toolbar.includes(`aria-label="${refresh}"`));
    assert.ok(toolbar.includes('auction-project-edit-button'));
    assert.doesNotMatch(toolbar,new RegExp(`>${refresh}</button>|<span>Telegram</span>`));
    assert.doesNotMatch(html,/auction-project-update/);
  }
});
test('successful status disappears from auction projects and project heading, without hiding active or failed states',()=>{
  for(const source of ['copart','iaa']) for(const view of ['projects','project']) {
    const props={source,view,projectId:view==='project'?9:null,projects:[{id:9,source_key:source,name:'Audi',cars:0}]};
    const render=status=>renderWorkspace({...props,runs:[{id:1,status,payload:{project_ids:[9],project_statuses:{'9':status}}}]});
    const success=render('SUCCEEDED');
    assert.doesNotMatch(success,/data-status="SUCCEEDED"|>Завершено<|Нет данных о запуске/);
    for(const status of ['QUEUED','RUNNING','FAILED','PARTIAL','WAITING_FOR_USER']) assert.ok(render(status).includes(`data-status="${status}"`),`${source}/${view}/${status}`);
  }
});
test('auction projects reuse the marketplace panel, compact rows and controls without creation dates or sharing links',()=>{
  for(const [source,locale,list,grid,sort] of [['copart','ru','Список','Плитка','Сортировка проектов'],['iaa','uk','Список','Плитка','Сортування проєктів']]) {
    const html=renderWorkspace({source,locale,projects:[
      {id:9,source_key:source,name:'Audi',cars:7,search_page_mode:'ALL_PAGES',created_at:'2026-10-01T10:00:00Z',last_refreshed_at:'2026-10-18T10:00:00Z'},
      {id:10,source_key:source,name:'BMW',cars:0,search_page_mode:'FIRST_PAGE'},
      {id:11,source_key:'encar',name:'Other site',cars:99},
    ]});
    assert.match(html,/projects-panel auction-projects-panel/);
    assert.match(html,/class="project-list"/);
    assert.match(html,/project-row auction-project/);
    for(const label of [list,grid,sort]) assert.ok(html.includes(`aria-label="${label}"`));
    assert.match(html,locale==='uk'?/Проєкти: 2 · Авто: 7/:/Проекты: 2 · Авто: 7/);
    assert.match(html,/datetime="2026-10-18T10:00:00Z"/i);
    assert.doesNotMatch(html,/datetime="2026-10-01T10:00:00Z"|Создан|Створено|Other site|Быстрый|Швидкий/);
    assert.match(html,/project-page-badge/);
    assert.doesNotMatch(html,/copySearchLink|Копировать ссылку|Поделиться|share/i);
  }
});
test('auction projects expose a site-scoped bulk update beside the tabs, disabled for an empty or unavailable list',()=>{
  const render=renderWorkspace;
  assert.match(render(),/auction-project-toolbar/);
  assert.match(render(),/<button[^>]*>Обновить проекты<\/button>/);
  assert.match(render({source:'iaa',locale:'uk',projects:[{id:2,source_key:'iaa',name:'BMW',cars:0}]}),/>Оновити проєкти<\/button>/);
  for(const props of [{projects:[]},{canSearch:false},{projectsLoading:true},{projectsError:true}])
    assert.match(render(props),/<button[^>]*disabled=""[^>]*>Обновить проекты<\/button>/);
  assert.doesNotMatch(render({sourceTestMode:false}),/>Обновить проекты<\/button>/);
  assert.doesNotMatch(render({view:'project',projectId:9}),/>Обновить проекты<\/button>/);
});
test('project auction cards expose an explicit scoped removal, while context-free cards cannot remove a car',()=>{
  for(const [locale,label] of [['ru','Удалить из проекта'],['uk','Видалити з проєкту']]) {
    const html=renderToStaticMarkup(React.createElement(AuctionCard,{lot:base,context,locale,imageUrl:v=>v,onRemove(){}}));
    assert.ok(html.includes(label));
    assert.match(html,/aria-haspopup="dialog"/);
    assert.doesNotMatch(card({},locale),new RegExp(label));
  }
});
test('auction removal confirmation names its exact project, explains future exclusion, and keeps failure distinct',()=>{
  for(const [locale,scope,other,failure] of [['ru','больше не будет добавляться','В других проектах он останется','Не удалось подтвердить удаление'],
    ['uk','більше не додаватиметься','В інших проєктах він залишиться','Не вдалося підтвердити видалення']]) {
    const props={target:{projectId:4,carId:40,title:'<script>car</script>',projectName:'Audi project'},locale,pending:false,failed:false,
      confirm(){},close(){},returnFocus:React.createRef(),fallbackFocus:React.createRef()};
    const html=renderToStaticMarkup(React.createElement(AuctionRemovalDialog,props));
    assert.match(html,/aria-describedby="auction-removal-help"/);
    assert.ok(html.includes(scope)&&html.includes(other)&&html.includes('Audi project'));
    assert.match(html,/&lt;script&gt;car&lt;\/script&gt;/);assert.doesNotMatch(html,/<script>/);
    const pending=renderToStaticMarkup(React.createElement(AuctionRemovalDialog,{...props,pending:true}));
    assert.equal((pending.match(/disabled=""/g)||[]).length,2);
    const failed=renderToStaticMarkup(React.createElement(AuctionRemovalDialog,{...props,failed:true}));
    assert.ok(failed.includes(failure));assert.match(failed,/role="alert"/);
  }
});
test('rendered countdown advances from the stored UTC schedule rather than stale API remaining_seconds',()=>{
  const lot={...base,schedule:{utc_at:'2026-10-18T16:30:00Z'}};
  const at=iso=>renderToStaticMarkup(React.createElement(AuctionCard,{lot,context,locale:'ru',imageUrl:v=>v,nowMs:Date.parse(iso)}));
  assert.match(at('2026-10-18T16:28:59Z'),/Через 0 ч 2 мин/);
  assert.match(at('2026-10-18T16:29:00Z'),/Через 0 ч 1 мин/);
  const elapsed=at('2026-10-18T18:00:00Z');
  assert.match(elapsed,/Время по расписанию наступило/);
  assert.doesNotMatch(elapsed,/Через|Продано|Аукцион идёт/);
});
test('historical auction card has frozen facts and bids but no current countdown, today, navigation or removal',()=>{
  const lot={...base,money_values:[{amount_minor:12300,currency:'USD',meaning:'CURRENT_BID',state:'KNOWN',auction_round:'old-round'}]};
  for(const [locale,label] of [['ru','Ставка на момент проверки'],['uk','Ставка на момент перевірки']]) {
    const html=renderToStaticMarkup(React.createElement(AuctionCard,{lot,context,locale,imageUrl:v=>v,
      historicalAt:'2026-10-10T10:00:00Z',historicalRoundId:'old-round',open(){},onRemove(){}}));
    assert.ok(html.includes(label));assert.match(html,/123 USD/);
    assert.doesNotMatch(html,/Через|До окончания|До завершення|Сегодня|Сьогодні|Удалить из проекта|Видалити з проєкту|target="_blank"/);
    assert.doesNotMatch(html,/<h2><button/);
  }
});
test('an unbound or other-round historical bid is not presented as the bid for this auction',()=>{
  for(const round of [undefined,null,'other-round']) {
    const html=renderToStaticMarkup(React.createElement(AuctionCard,{lot:{...base,money_values:[
      {amount_minor:12300,currency:'USD',meaning:'CURRENT_BID',state:'KNOWN',auction_round:'old-round'}]},
      context,locale:'ru',imageUrl:v=>v,historicalAt:'2026-10-10T10:00:00Z',historicalRoundId:round}));
    assert.doesNotMatch(html,/123 USD/);assert.match(html,/связь с раундом этих торгов не подтверждена/);
  }
});
test('selection explains why confirmation is unavailable rather than showing endless loading', () => {
  for (const issue of ['NO_SEARCH_CAPTURED', 'SEARCH_RESULTS_NOT_READY', 'SITE_ACCESS_BLOCKED', 'SITE_VERIFICATION_REQUIRED', 'SITE_CONSENT_REQUIRED', 'SEARCH_REQUEST_FAILED', 'SEARCH_FILTERS_UNSUPPORTED']) {
    assert.notEqual(selectionIssueMessage(issue, 'ru'), selectionIssueMessage('UNKNOWN', 'ru'));
    assert.notEqual(selectionIssueMessage(issue, 'ru'), selectionIssueMessage(issue, 'uk'));
  }
});
test('selection summaries distinguish captured drafts, incomplete edits and a missing native restore link in both languages', () => {
  const selection={id:'a'.repeat(32),source_key:'iaa',status:'CLOSED',issue:null,can_use:true,
    snapshot:{summary:['Make: <script>Cadillac</script>','Year: 2017–2027'],revision:'fixture',creation_url:null}};
  for(const [locale,saved,replay,unfinished] of [['ru','Условия сохранены в черновике','нет ссылки для их восстановления','Последнее изменение не подтверждено'],
    ['uk','Умови збережено в чернетці','немає посилання для їх відновлення','Останню зміну не підтверджено']]) {
    const html=renderToStaticMarkup(React.createElement(SelectionSummary,{selection,locale}));
    assert.ok(html.includes(saved)&&html.includes(replay));
    assert.match(html,/&lt;script&gt;Cadillac&lt;\/script&gt;/);
    assert.doesNotMatch(html,/<script>/);
    const pending=renderToStaticMarkup(React.createElement(SelectionSummary,{selection:{...selection,issue:'LAST_CHANGE_UNCONFIRMED',can_use:false},locale}));
    assert.ok(pending.includes(unfinished));
    assert.match(pending,/2017–2027/);
  }
});
test('source URL validation has a safe UI error code without exposing raw server text', () => {
  const detail=[{loc:['body','search_url'],msg:'Value error, Expected IAA Search URL <secret>',type:'value_error'}];
  assert.equal(responseErrorCode(JSON.stringify({detail})),'invalid_source_search_url');
  assert.equal(responseErrorCode(JSON.stringify({detail:[{loc:['body','name'],msg:'Private text'}]})),undefined);
  assert.equal(responseErrorCode('<html>Proxy failure</html>'),undefined);
});
test("auction search explains partial coverage, filtering and the reason, in both locales", () => {
  const run={status:'FAILED',payload:{pagination:{'9':{pages_visited:1,total_pages:4,ads_seen:20,complete:false,
    stop_reason:'SEARCH_PAGE_LIMIT',read_summary:{dated:5,exact_time:4,without_date:12,unrecognized_schedule:2,unsupported_market:1}}}}};
  for(const [locale,partial,limit,absent] of [['ru','Неполный поиск','Достигнут лимит страниц','Без назначенной даты'],['uk','Неповний пошук','Досягнуто ліміту сторінок','Без призначеної дати']]){
    const html=renderToStaticMarkup(React.createElement(AuctionSearchNotice,{run,projectId:9,locale}));
    assert.ok(html.includes(partial)&&html.includes(limit)&&html.includes(absent));
    assert.match(html,/1 \/ 4/);
    assert.match(html,/12/);
    assert.doesNotMatch(html,/Продано|SOLD/);
  }
});
test('an unread first search page is not presented as a failed next page; saved error details stay project-scoped', () => {
  for (const [locale, first, next, details] of [
    ['ru', 'Не удалось прочитать первую страницу результатов', 'Следующая страница не загрузилась', 'Подробности ошибки'],
    ['uk', 'Не вдалося прочитати першу сторінку результатів', 'Наступна сторінка не завантажилася', 'Подробиці помилки'],
  ]) {
    const run={status:'FAILED',payload:{pagination:{'9':{pages_visited:0,total_pages:1,ads_seen:0,complete:false,stop_reason:'SEARCH_PAGE_NOT_READY'}},
      failures:[{project_id:9,code:'SEARCH_PAGE_NOT_READY',technical:'Copart interface <loading> did not finish'},
        {project_id:10,code:'OTHER_PROJECT',technical:'Must not appear'}]}};
    const render=()=>renderToStaticMarkup(React.createElement(AuctionSearchNotice,{run,projectId:9,locale}));
    const html=render();
    assert.ok(html.includes(first)&&html.includes(details));assert.ok(!html.includes(next));
    assert.match(html,/SEARCH_PAGE_NOT_READY/);assert.match(html,/Copart interface &lt;loading&gt; did not finish/);
    assert.doesNotMatch(html,/OTHER_PROJECT|Must not appear|<loading>/);
    run.payload.pagination['9'].pages_visited=1;
    assert.ok(render().includes(next));
  }
});
test('an unavailable IAA reset is not misreported as a busy browser or an instruction to expand a closed window',()=>{
  for(const locale of ['ru','uk']) {
    const html=renderToStaticMarkup(React.createElement(SelectionSummary,{locale,selection:{
      id:'a'.repeat(32),source_key:'iaa',status:'FAILED',issue:'SOURCE_SELECTION_RESET_UNAVAILABLE',can_use:false,snapshot:null}}));
    assert.doesNotMatch(html,/закройте другое окно|закрийте інше вікно|Разверните фильтры|Розгорніть фільтри/);
  }
});
test('auction photos are keyboard-operable preview buttons, not listing links', () => {
  const html=card({image:'https://cs.copart.com/fixture.jpg'});
  assert.match(html,/<button[^>]+aria-label="Посмотреть фото: Saved car"/);
  assert.match(html,/aria-haspopup="dialog"/);
  assert.doesNotMatch(card(),/aria-haspopup="dialog"/);
  assert.match(card({image:'https://vis.iaai.com/fixture.jpg'},'uk'),/Переглянути фото: Saved car/);
});
test("completed first-page search is not presented as failed full coverage", () => {
  const run={status:'SUCCEEDED',payload:{pagination:{'9':{mode:'FIRST_PAGE',pages_visited:1,total_pages:4,ads_seen:20,complete:true}}}};
  const html=renderToStaticMarkup(React.createElement(AuctionSearchNotice,{run,projectId:9,locale:'ru'}));
  assert.match(html,/Первая страница/);
  assert.doesNotMatch(html,/Неполный поиск|1 \/ 4/);
});
test('a waiting project in the expanded journal is still active, not an incomplete result',()=>{
  const html=renderToStaticMarkup(React.createElement(AuctionSearchNotice,{locale:'ru',projectId:9,
    run:{status:'WAITING_FOR_USER',payload:{pagination:{'9':{pages_visited:0,total_pages:2,ads_seen:0,complete:false}}}}}));
  assert.match(html,/Обход выдачи/);assert.doesNotMatch(html,/Неполный поиск/);
});
test('a successful verified search explains the manual window fallback without marking it incomplete', () => {
  const run={status:'SUCCEEDED',payload:{pagination:{'9':{pages_visited:2,total_pages:2,ads_seen:2,complete:true,browser_notice:'MANUAL_MINIMIZE'}}}};
  for(const [locale,copy] of [['ru','Окно сайта можно свернуть вручную'],['uk','Вікно сайту можна згорнути вручну']]){
    const html=renderToStaticMarkup(React.createElement(AuctionSearchNotice,{run,projectId:9,locale}));
    assert.ok(html.includes(copy));
    assert.doesNotMatch(html,/Неполный поиск|Неповний пошук/);
  }
});
test("a challenge after partial reading asks for a fresh scan, not a sold/complete result", () => {
  const run={status:'FAILED',payload:{pagination:{'9':{mode:'ALL_PAGES',pages_visited:1,total_pages:4,ads_seen:20,complete:false,stop_reason:'SEARCH_VERIFICATION_RESTART_REQUIRED'}}}};
  for(const [locale,copy] of [['ru','Повторите поиск для полного результата'],['uk','Повторіть пошук для повного результату']]){
    const html=renderToStaticMarkup(React.createElement(AuctionSearchNotice,{run,projectId:9,locale}));
    assert.ok(html.includes(copy));
    assert.doesNotMatch(html,/Продано|SOLD/);
  }
});
test("only Actual miles receive an approximate distance, zero and unknown units remain distinct", () => {
  assert.doesNotMatch(card({ details: { mileage: { value: 41138, unit: "mi", accuracy: "NOT_ACTUAL" } } }), /≈/);
  assert.match(card({ details: { mileage: { value: 0, unit: "mi", accuracy: "ACTUAL" } } }), /≈ 0 км/);
  assert.doesNotMatch(card({ details: { mileage: { value: 41, unit: "unknown", accuracy: "ACTUAL" } } }), /≈|\bmi\b/);
  assert.doesNotMatch(card({ details: { mileage: { value: null, unit: "mi" }, keys: null } }), /Ключей нет|≈/);
});
test("typed money preserves minor units and meanings, including zero and snapshot round", () => {
  const html = card({ money_values: [
    { amount_minor: 1500000, currency: "KRW", meaning: "SALE_TOTAL", state: "KNOWN", auction_round: null },
    { amount_minor: 0, currency: "USD", meaning: "CURRENT_BID", state: "KNOWN", auction_round: "current" },
    { amount_minor: 2500000, currency: "USD", meaning: "ESTIMATED_RETAIL", state: "KNOWN", auction_round: null },
    { amount_minor: 990000, currency: "USD", meaning: "CURRENT_BID", state: "KNOWN", auction_round: "old" },
  ] });
  assert.match(html, /1[ \s]500[ \s]000 KRW/);
  assert.match(html, /Текущая ставка · снимок/);
  assert.match(html, /0 USD/);
  assert.match(html, /Оценочная стоимость, не ставка/);
  assert.doesNotMatch(html, /9[ \s]900 USD/);
});
test("START, END and UNKNOWN time are separate, an elapsed clock is not source LIVE", () => {
  assert.match(card(), /Через 3 ч 30 мин/);
  const end = card({ time: { ...base.time, event_kind: "END", time_state: "UPCOMING_END" } });
  assert.match(end, /Окончание торгов|До окончания 3 ч 30 мин/);
  assert.doesNotMatch(end, /Начало аукциона|Через/);
  const unknown = card({ time: { ...base.time, event_kind: "UNKNOWN", time_state: "EVENT_KIND_UNKNOWN", remaining_seconds: null } });
  assert.match(unknown, /тип неизвестен/);
  assert.doesNotMatch(unknown, /Через|До окончания/);
  const elapsed = card({ time: { ...base.time, time_state: "SCHEDULED_START_PASSED", remaining_seconds: 0 }, details: { source_action: "Bid now" } });
  assert.match(elapsed, /Текущий этап не подтверждён/);
  assert.doesNotMatch(elapsed, /Аукцион идёт|Торги завершены/);
  assert.match(card({ details: { auction_phase: "LIVE" } }), /Аукцион идёт/);
});
test("date-only and unknown-zone snapshots have no clock/countdown, original evidence stays visible", () => {
  const html = card({ time: { ...base.time, local_date: null, local_datetime: null, precision: "DATE_ONLY", is_today: null, time_state: "LOCAL_DAY_UNCERTAIN", remaining_seconds: null },
    schedule: { local_date: "2026-10-11", source_timezone: "America/Chicago", raw: "Oct 11 CST", raw_timezone: "CST", change_kind: "RESCHEDULED" } });
  assert.match(html, /Точное время не указано|Локальный день не определён|Oct 11 CST|Расписание перенесено/);
  assert.match(html, /Дата источника: 2026-10-11 · America\/Chicago/);
  assert.doesNotMatch(html, /15:30|Через/);
});
test("unknown raw values are escaped; the IAA card uses the same composition and Ukrainian labels", () => {
  const html = card({ source_key: "iaa", url: "https://www.iaai.com/VehicleDetail/46271057~US", details: { primary_damage: "<script>new</script>", engine_status: "Stationary", keys: false } }, "uk");
  assert.match(html, /auction-card/);
  assert.match(html, /Показання одометра|Без руху|Ключів немає|Оголошення/);
  assert.match(html, /&lt;script&gt;new&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>|card-foot|Что изменилось/);
});
test("listing links reject search links, cross-source identities, credentials, ports and unsafe protocols", () => {
  assert.equal(auctionListingLink(base), base.url);
  for (const url of ["javascript:alert(1)", "https://www.copart.com/lotSearchResults", "https://www.copart.com/lot/999", "https://u:p@www.copart.com/lot/65507986", "https://www.copart.com:8443/lot/65507986", "https://www.iaai.com/VehicleDetail/46271057~US"]) {
    assert.equal(auctionListingLink({ ...base, url }), null);
  }
});
test("a valid listing can use the desktop external opener, not a search or webview navigation", () => {
  const calls = [];
  const tree = AuctionCard({ lot: base, context, locale: "ru", imageUrl: (value) => value, openListing: (url) => calls.push(url) });
  const findLink = (element) => {
    if (!React.isValidElement(element)) return null;
    if (element.type === "a") return element;
    return React.Children.toArray(element.props.children).map(findLink).find(Boolean);
  };
  const link = findLink(tree);
  assert.equal(typeof link.props.onClick, "function");
  let prevented = false;
  link.props.onClick({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(calls, [base.url]);
});

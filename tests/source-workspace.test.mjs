import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
const { App, scanActionErrorMessage } = await import("../app/page.tsx");
const { readWorkspace, workspaceUrl, sourceRead, normalizeRoute } = await import("../app/source-workspace.ts");
const { sourceAvailability } = await import("../app/source-navigation.tsx");

const user = { id: 1, username: "fixture", role: "USER", preferred_locale: "ru", must_change_password: false };
const available = { sources: [{ source_key: "encar", family: "MARKETPLACE", can_search: true, reason: null }],
  projects: [{ id: 1, source_key: "encar", name: "Encar fixture", can_search: true, reason: null, active_status: null, scan_run_id: null }] };
function render(t, { search = "", stored = null, locale = "ru", storage = {}, projectOverride = null, carOverride = null,
  scanError = false, runtimeError = false, availabilityError = false, launchOptions = available, activeRuns = [], projectsOverride, auctionProject, auctionCar, auctionError = false, nativeWorkspace, sourceTestMode = false, deliveryEnabled = false } = {}) {
  const previousWindow = globalThis.window;
  globalThis.window = {
    location: { search, pathname: "/" },
    localStorage: { getItem: (key) => key === "searchcar-workspace" ? stored : storage[key] || null },
  };
  t.after(() => { if (previousWindow === undefined) delete globalThis.window; else globalThis.window = previousWindow; });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const project = { id: 1, source_key: "encar", name: "Encar fixture", updated_at: "2026-10-11T10:00:00Z", cars: 0 };
  client.setQueryData(["projects"], [project]);
  client.setQueryData(["projects", "encar"], projectsOverride || [project]);
  const requestedSource = readWorkspace(search, nativeWorkspace !== undefined ? nativeWorkspace : stored).source;
  if (projectsOverride && requestedSource !== "encar") client.setQueryData(["projects", requestedSource], projectsOverride);
  client.setQueryData(["scans"], []);
  client.setQueryData(["scans", "encar"], activeRuns);
  client.setQueryData(["scans", requestedSource], activeRuns);
  client.setQueryData(["scans", "active"], activeRuns);
  client.setQueryData(["desktop-runtime"], { desktop: true, updater: !sourceTestMode, source_test_mode: sourceTestMode, telegram_delivery_enabled: deliveryEnabled });
  if (runtimeError) client.getQueryCache().find({ queryKey: ["desktop-runtime"], exact: true }).setState({ status: "error", error: new Error("fixture disconnected") });
  if (launchOptions) client.setQueryData(["launch-options"], launchOptions);
  if (availabilityError) client.getQueryCache().find({ queryKey: ["launch-options"], exact: true }).setState({ status: "error", error: new Error("fixture unavailable") });
  if (scanError) client.getQueryCache().find({ queryKey: ["scans", "encar"], exact: true }).setState({ status: "error", error: new Error("fixture offline") });
  if (projectOverride) client.setQueryData(["project", projectOverride.id], projectOverride);
  if (carOverride) client.setQueryData(["car", carOverride.id, 1], carOverride);
  if (auctionProject) client.setQueryData(["auction", "project", requestedSource, auctionProject.project_id, false], auctionProject);
  if (auctionCar) client.setQueryData(["auction", "car", requestedSource, auctionCar.project_id, auctionCar.lot.id], auctionCar);
  if (auctionError) client.getQueryCache().find({ queryKey: ["auction", "project", requestedSource, 4, false], exact: true }).setState({ status: "error", error: new Error("fixture offline") });
  const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
    React.createElement(App, { currentUser: { ...user, preferred_locale: locale, workspace: nativeWorkspace }, onSessionChanged() {} })));
  t.after(() => client.clear());
  return { html, client };
}

test('a disconnected desktop never presents cached scans as a healthy worker', (t) => {
  for (const locale of ['ru', 'uk']) {
    const { html } = render(t, { runtimeError: true, locale,
      activeRuns: [{ id: 49, status: 'RUNNING', payload: { project_ids: [1] } }] });
    assert.match(html, /class="dot offline"/);
    const worker = html.match(/class="worker"[\s\S]*?<\/strong>/)?.[0];
    assert.doesNotMatch(worker, /Выполняет сканирование|Виконує сканування/);
    assert.match(html, /role="alert"[^>]*data-backend-disconnected/);
    assert.match(html, /class="button shell-refresh"[^>]*disabled/);
    assert.match(html, /class="activity-trigger"[\s\S]*?<b>\?<\/b>/);
    assert.doesNotMatch(html, /Encar: Обновляется|Encar: Оновлюється/);
    assert.match(html, locale === 'ru' ? /Encar: Состояние обновления неизвестно/ : /Encar: Стан оновлення невідомий/);
    assert.match(html, locale === 'ru' ? /заново откройте приложение/ : /знову відкрийте застосунок/);
    assert.match(html, /Encar fixture/, 'a disconnect must not discard cached projects');
  }
});

test("Encar still renders the original projects toolbar and project rows", (t) => {
  const { html } = render(t);
  assert.match(html, /projects-toolbar-controls/);
  assert.match(html, /Encar fixture/);
  assert.match(html, /projects-refresh-button/);
});
test("source test profile is visibly distinct, localized, and has no updater button", (t) => {
  const normal = render(t).html;
  assert.doesNotMatch(normal, /Тестовый профиль источников/);
  assert.match(normal, /Проверить обновления/);
  const experimental = render(t, { sourceTestMode: true }).html;
  assert.match(experimental, /role="status"[^>]*>Тестовый профиль источников/);
  assert.match(experimental, /данные отдельно от рабочих · только ручные запуски, без автоматической рассылки/);
  assert.doesNotMatch(experimental, /Проверить обновления/);
  assert.match(render(t, { sourceTestMode: true, locale: "uk" }).html, /Тестовий профіль джерел/);
});

test('a delivery-enabled auction preview does not falsely promise there is no automatic messaging', (t) => {
  const html=render(t,{sourceTestMode:true,deliveryEnabled:true}).html;
  assert.match(html,/Telegram — по настройкам проектов/);
  assert.doesNotMatch(html,/без автоматической рассылки/);
});
test("connected test auctions expose project creation and scoped refresh without Encar controls", (t) => {
  const launchOptions={sources:[{source_key:'copart',family:'AUCTION',can_search:true,reason:null}],projects:[{id:4,source_key:'copart',can_search:true,active_status:null}]};
  const {html}=render(t,{search:'?source=copart',sourceTestMode:true,launchOptions,
    projectsOverride:[{id:4,source_key:'copart',name:'Auction fixture',cars:2}]});
  assert.match(html,/Создать проект/);
  assert.match(html,/Обновить данные/);
  assert.doesNotMatch(html,/Поиск Copart ещё не подключён|Сохранённые данные · поиск и Telegram не запускаются/);
  assert.doesNotMatch(render(t,{search:'?source=copart',sourceTestMode:false}).html,/Создать проект/);
});
test('auction project rows show actual refresh, not creation or settings change time, with localized missing dates', (t) => {
  const projectsOverride=[{id:4,source_key:'copart',name:'Long auction project',cars:2,
    created_at:'2026-10-10T10:00:00Z',updated_at:'2026-10-16T10:00:00Z',last_refreshed_at:'2026-10-15T10:00:00Z'},
    {id:5,source_key:'copart',name:'Never refreshed',cars:0}];
  const {html}=render(t,{search:'?source=copart',sourceTestMode:true,projectsOverride});
  assert.doesNotMatch(html,/Создан/);
  assert.match(html,/Обновлён/);
  assert.doesNotMatch(html,/datetime="2026-10-10T10:00:00Z"/i);
  assert.match(html,/datetime="2026-10-15T10:00:00Z"/i);
  assert.doesNotMatch(html,/datetime="2026-10-16T10:00:00Z"/i);
  assert.match(html,/Ещё не обновлялся/);
  const uk=render(t,{search:'?source=copart',sourceTestMode:true,projectsOverride,locale:'uk'}).html;
  assert.doesNotMatch(uk,/Створено/);
  assert.match(uk,/Ще не оновлювався/);
});
test('auction projects and source indicators show each project state, not the whole batch state', (t) => {
  const projectsOverride=[{id:4,source_key:'copart',name:'Finished project',cars:2},
    {id:5,source_key:'copart',name:'Current project',cars:0}, {id:6,source_key:'iaa',name:'Waiting project',cars:0}];
  const activeRuns=[{id:77,kind:'PROJECTS',status:'RUNNING',progress:35,created_at:'2026-10-18T10:00:00Z',payload:{
    project_ids:[4,5,6],current_project_id:5,project_statuses:{4:'SUCCEEDED',5:'RUNNING',6:'QUEUED'},
    project_plans:projectsOverride.map(p=>({project_id:p.id,source_key:p.source_key,name:p.name}))}}];
  const launchOptions={sources:['copart','iaa'].map(source_key=>({source_key,family:'AUCTION',can_search:true,reason:null})),projects:[]};
  const {html}=render(t,{search:'?source=copart',sourceTestMode:true,projectsOverride,activeRuns,launchOptions});
  assert.doesNotMatch(html,/data-project-id="4"[^>]*data-status="SUCCEEDED"/);
  assert.match(html,/Finished project/);
  assert.match(html,/data-project-id="5"[^>]*data-status="RUNNING"/);
  assert.match(html,/source-running/);
  assert.match(html,/source-waiting/);
  assert.match(html,/Copart: Обновляется/);
  assert.match(html,/IAA: В очереди/);
  const history=render(t,{search:'?source=copart&view=scans',sourceTestMode:true,projectsOverride,activeRuns,launchOptions}).html;
  assert.match(history,/Finished project/);
  assert.match(history,/Завершено/);
  assert.match(history,/Current project/);
});
test("the real app groups three sites and separates application settings", (t) => {
  const { html } = render(t);
  assert.match(html, /Площадки/);
  assert.match(html, /Аукционы/);
  assert.match(html, /Copart/);
  assert.match(html, /IAA/);
  assert.match(html, /Настройки приложения/);
});
test("test auction project creation stays enabled when search is denied", (t) => {
  for (const source of ['copart', 'iaa']) {
    const launchOptions={sources:[{source_key:source,family:'AUCTION',can_search:false,reason:'LICENSE_SOURCE_NOT_ALLOWED'}],projects:[]};
    const {html}=render(t,{search:`?source=${source}`,sourceTestMode:true,launchOptions,
      projectsOverride:[{id:4,source_key:source,name:'Saved auction',cars:0}]});
    assert.match(html, /<button type="button" class="button">Создать проект<\/button>/);
    assert.match(html, /aria-label="Обновить данные: Saved auction"[^>]*disabled=""/);
  }
});

test("the shared shell offers an explicit refresh picker even from an unavailable site", (t) => {
  for (const search of ["?source=encar", "?source=copart", "?source=iaa&view=source-settings"]) {
    const { html } = render(t, { search });
    assert.match(html, /aria-haspopup="dialog"[^>]*>Обновить данные<\/button>/);
  }
});
test("active runs open a global activity dialog independently of the selected site", (t) => {
  const { html } = render(t, { search: "?source=iaa" });
  assert.match(html, /aria-haspopup="dialog"[^>]*aria-label="Активные запуски — открыть очередь"/);
});
test("restored Copart workspace never renders the Encar composition or fake auction data", (t) => {
  const { html, client } = render(t, { stored: JSON.stringify({ source: "copart", view: "projects" }) });
  assert.match(html, /Поиск Copart ещё не подключён/);
  assert.doesNotMatch(html, /Encar fixture|2020 Mercedes|copySearchLink/);
  assert.ok(client.getQueryState(["projects", "copart"]));
  assert.ok(client.getQueryState(["scans", "copart"]));
  assert.ok(client.getQueryState(["scans", "active"]));
});
test("the real app restores the current site from multi-site storage", (t) => {
  const { html } = render(t, { stored: JSON.stringify({ current: { source: "copart", view: "source-settings" },
    sites: { encar: { source: "encar", view: "scans" }, copart: { source: "copart", view: "source-settings" } } }) });
  assert.match(html, /Настройки сайта · Copart/);
  assert.doesNotMatch(html, /projects-toolbar-controls|Encar fixture/);
});
test("the desktop app uses owner preferences rather than the new port's empty or stale browser storage", (t) => {
  const { html } = render(t, { nativeWorkspace: JSON.stringify({ current: { source: "iaa", view: "source-settings" }, sites: {} }),
    stored: JSON.stringify({ source: "encar", view: "scans" }) });
  assert.match(html, /Настройки сайта · IAA/);
  assert.doesNotMatch(html, /История запусков|Encar fixture/);
});
test("an explicit IAA deep link overrides the saved Encar workspace; Ukrainian shell is localized", (t) => {
  const { html } = render(t, { search: "?source=iaa&view=scans", stored: JSON.stringify({ source: "encar", view: "projects" }), locale: "uk" });
  assert.match(html, /Пошук IAA ще не підключено/);
  assert.match(html, /Майданчики/);
  assert.match(html, /Налаштування застосунку/);
  assert.doesNotMatch(html, /Encar fixture/);
});

test("the actual request functions ask the API for the selected source, while activity stays global", async (t) => {
  const { client } = render(t, { search: "?source=copart" });
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url) => { requests.push(String(url)); return new Response("[]", { headers: { "content-type": "application/json" } }); });
  for (const key of [["projects", "copart"], ["scans", "copart"], ["scans", "active"]]) {
    const query = client.getQueryCache().find({ queryKey: key, exact: true });
    await client.fetchQuery({ ...query.options, staleTime: 0 });
  }
  assert.deepEqual(requests.map((url) => new URL(url, "https://fixture.test").pathname + new URL(url, "https://fixture.test").search),
    ["/api/projects?source_key=copart", "/api/scans?source_key=copart", "/api/scans/active"]);
});

test("legacy deep links stay Encar and invalid persisted routes safely fall back", () => {
  const stored = JSON.stringify({ source: "copart", view: "scans" });
  assert.deepEqual(readWorkspace("?view=car&project=12&car=42", stored), { source: "encar", view: "car", projectId: 12, carId: 42 });
  for (const value of ["{bad", "null", JSON.stringify({ source: "arbitrary-module", view: "car", carId: 42 })]) {
    assert.deepEqual(readWorkspace("", value), { source: "encar", view: "projects", projectId: null, carId: null });
  }
  assert.equal(readWorkspace("?source=encar&view=car&car=-1", null).carId, null);
  const route = normalizeRoute({ source: "iaa", view: "source-settings" });
  assert.deepEqual(readWorkspace(new URL(workspaceUrl("/", route), "https://fixture.test").search, null), route);
  for (const resource of ["projects", "scans", "favorites"]) {
    assert.notDeepEqual(sourceRead(resource, "encar").queryKey, sourceRead(resource, "copart").queryKey);
  }
});

test("a foreign-site project URL cannot render the Encar project details", (t) => {
  const { html } = render(t, { search: "?source=encar&view=project&project=12", projectOverride: { id: 12, source_key: "copart", name: "Foreign auction", cars: 0 } });
  assert.match(html, /Эти данные относятся к Copart/);
  assert.match(html, /source=copart&amp;view=project&amp;project=12/);
  assert.doesNotMatch(html, /project-price|projectUpdate|Foreign auction/);
});

test("Encar settings separate project controls from app-wide performance without fake fields", (t) => {
  const { html } = render(t, { search: "?source=encar&view=source-settings" });
  assert.match(html, /Настройки Encar/);
  assert.match(html, /Скорость поиска, автообновление, лицензия/);
  assert.doesNotMatch(html, /type="checkbox"|projects-toolbar-controls/);
});

test("Encar project-list sort is restored when returning from another source", (t) => {
  const { html } = render(t, { storage: { "encar-project-list-sort": "name" } });
  assert.match(html, /<option value="name" selected="">По имени/);
});

test("a failed source history read is not presented as an empty successful history", (t) => {
  const { html } = render(t, { search: "?source=encar&view=scans", scanError: true });
  assert.match(html, /role="alert"/);
  assert.match(html, /Backend недоступен/);
  assert.match(html, /Повторить/);
  assert.doesNotMatch(html, /scan-history-list/);
});

test("a failed refresh is not misreported as an already active scan", () => {
  assert.equal(scanActionErrorMessage(new Error("fixture backend offline"), (key) => key), "actionFailed");
});

const blocked = (reason) => ({ sources: [{ ...available.sources[0], can_search: false, reason }],
  projects: [{ ...available.projects[0], can_search: false, reason }] });
test("license-disabled Encar remains readable but local refresh is disabled with its reason", (t) => {
  const { html } = render(t, { launchOptions: blocked("LICENSE_SOURCE_NOT_ALLOWED") });
  assert.match(html, /Сайт недоступен по лицензии/);
  assert.match(html, /projects-refresh-button[^>]*disabled/);
  assert.match(html, /Encar fixture/);
  assert.match(html, /Настройки приложения/);
});
test("availability errors and initial loading cannot masquerade as a connected Encar module", (t) => {
  const { html } = render(t, { search: "?view=source-settings", availabilityError: true });
  assert.match(html, /Не удалось проверить доступность/);
  assert.doesNotMatch(html, /<h2>Поиск подключён/);
  assert.match(html, /role="alert"/);
  const loading = render(t, { launchOptions: null }).html;
  assert.match(loading, /Проверяем доступность/);
  assert.match(loading, /projects-refresh-button[^>]*disabled/);
});
test("one blocked source does not disable another and unavailable-source settings are truthful", (t) => {
  const options = { ...available, sources: [...available.sources,
    { source_key: "copart", family: "AUCTION", can_search: false, reason: "LICENSE_SOURCE_NOT_ALLOWED" }] };
  const encar = render(t, { launchOptions: options }).html;
  assert.doesNotMatch(encar, /projects-refresh-button[^>]*disabled/);
  assert.match(encar, /aria-label="Copart: Сайт недоступен по лицензии"/);
  const copart = render(t, { search: "?source=copart&view=source-settings", launchOptions: options }).html;
  assert.match(copart, /Сайт недоступен по лицензии/);
  assert.doesNotMatch(copart, /Поиск Copart ещё не подключён/);
});
test("project and saved-car refresh obey the same source entitlement without hiding the data", (t) => {
  const project = render(t, { search: "?view=project&project=1", launchOptions: blocked("LICENSE_LEASE_EXPIRED"),
    projectOverride: { id: 1, name: "Saved project", source_key: "encar", cars: 0 } }).html;
  assert.match(project, /<button[^>]*disabled[^>]*>↻ Обновить проект/);
  assert.match(project, /Saved project/);
  const car = render(t, { search: "?view=car&project=1&car=10", launchOptions: blocked("LICENSE_LEASE_EXPIRED"),
    carOverride: { id: 10, title: "Saved car", source_key: "encar", encar_id: "42688672", details: {}, price: 10000000 } }).html;
  assert.match(car, /<button[^>]*disabled[^>]*>↻ Обновить<\/button>/);
  assert.match(car, /Saved car/);
});
test("loss of search entitlement never disables cancellation of an already active job", (t) => {
  const { html } = render(t, { launchOptions: blocked("LICENSE_SOURCE_NOT_ALLOWED"), activeRuns: [
    { id: 7, kind: "PROJECTS", status: "RUNNING", progress: 10, created_at: "2026-10-11T10:00:00Z", payload: { project_ids: [1], current_project_id: 1 } },
  ] });
  assert.match(html, /Сайт недоступен по лицензии/);
  assert.match(html, /projects-refresh-button[^>]*disabled/);
  assert.match(html, /<button[^>]*>Отменить обновление<\/button>/);
});
test("the real project toolbar still disables refresh for no projects and an active job", (t) => {
  assert.match(render(t, { projectsOverride: [] }).html, /projects-refresh-button[^>]*disabled/);
  assert.match(render(t, { activeRuns: [
    { id: 7, kind: "PROJECTS", status: "RUNNING", progress: 10, created_at: "2026-10-11T10:00:00Z", payload: { project_ids: [1] } },
  ] }).html, /projects-refresh-button[^>]*disabled/);
  assert.doesNotMatch(render(t).html, /projects-refresh-button[^>]*disabled/);
});
test("availability distinguishes unread, failed, unshipped, maintenance and module failure without mutating options", () => {
  assert.equal(sourceAvailability(undefined, "encar").reason, "AVAILABILITY_PENDING");
  assert.deepEqual(sourceAvailability(available, "encar", true), { can_search: false, reason: "AVAILABILITY_ERROR" });
  assert.equal(sourceAvailability(available, "iaa").reason, "SOURCE_NOT_CONNECTED");
  for (const reason of ["SOURCE_UNAVAILABLE", "DESKTOP_MAINTENANCE_ACTIVE", "SOURCE_SEARCH_UNSUPPORTED", "LICENSE_SOURCE_NOT_ALLOWED"]) {
    assert.equal(sourceAvailability(blocked(reason), "encar").reason, reason);
  }
  assert.equal(sourceAvailability(available, "encar").can_search, true);
  assert.equal(available.sources[0].can_search, true);
});
test("availability refresh stays enabled outside the picker and on window focus", (t) => {
  const { client } = render(t);
  const query = client.getQueryCache().find({ queryKey: ["launch-options"], exact: true });
  assert.notEqual(query.options.enabled, false);
  assert.equal(query.options.refetchOnWindowFocus, true);
  assert.equal(query.options.refetchInterval(query), 30_000);
  query.setData({...available,projects:[{...available.projects[0],active_status:'RUNNING'}]});
  assert.equal(query.options.refetchInterval(query), 5000);
  const scans=client.getQueryCache().find({queryKey:['scans','encar'],exact:true});
  assert.equal(scans.options.refetchInterval(scans),30000);
  scans.setData([{id:7,status:'RUNNING'}]);
  assert.equal(scans.options.refetchInterval(scans),5000);
  assert.equal(client.getQueryCache().find({queryKey:['scans','active'],exact:true}).options.refetchInterval,5000);
});
test("a project-specific invalid link is explained without disabling the whole site", (t) => {
  const options = { ...available, projects: [{ ...available.projects[0], can_search: false, reason: "INVALID_SOURCE_URL" }] };
  const { html } = render(t, { search: "?view=project&project=1", launchOptions: options,
    projectOverride: { id: 1, name: "Saved project", source_key: "encar", cars: 0 } });
  assert.match(html, /Проверьте ссылку в настройках проекта/);
  assert.match(html, /aria-label="Encar: Нет активных обновлений"/);
  assert.match(html, /<button[^>]*disabled[^>]*>↻ Обновить проект/);
});

const auctionContext = { generated_at: "2026-10-11T10:00:00Z", computer_timezone: "Europe/Warsaw", local_date: "2026-10-11" };
const savedLot = { id: 40, source_key: "copart", source_listing_id: "65507986", url: "https://www.copart.com/lot/65507986", title: "Saved Audi A5",
  image: null, details: { mileage: { value: 41138, unit: "mi", accuracy: "NOT_ACTUAL" }, primary_damage: "Front End", engine_status: "Run & Drive", keys: false },
  money_values: [{ amount_minor: 385000, currency: "USD", meaning: "CURRENT_BID", state: "KNOWN", auction_round: "round-a" }],
  round: { id: "round-a", evidence_kind: "SOURCE_SALE_ID", source_sale_id: "sale-a", last_observed_at: "2026-10-11T09:00:00Z" },
  schedule: { id: "version-a", precision: "EXACT", event_kind: "START", source_timezone: "America/Chicago" },
  time: { computer_timezone: "Europe/Warsaw", event_kind: "START", precision: "EXACT", local_date: "2026-10-11", local_datetime: "2026-10-11T15:30:00+02:00", is_today: true, remaining_seconds: 12600, time_state: "UPCOMING_START" } };
const savedProject = { ...auctionContext, project_id: 4, source_key: "copart", today_only: false, lots: [savedLot] };
test("unconnected auction projects remain navigable without Encar controls or fake search", (t) => {
  const { html } = render(t, { search: "?source=copart", projectsOverride: [{ id: 4, source_key: "copart", name: "Saved auction project", cars: 1 }] });
  assert.match(html, /Saved auction project/);
  assert.match(html, /Сегодня/);
  assert.doesNotMatch(html, /Интерфейс аукционов в разработке|Копировать ссылку|Быстрый/);
});
test("stored auction project renders the shared card while search stays unavailable", (t) => {
  const { html, client } = render(t, { search: "?source=copart&view=project&project=4", auctionProject: savedProject });
  assert.match(html, /Saved Audi A5/);
  assert.match(html, /Не фактический/);
  assert.match(html, /Сегодня · 11 октября/);
  assert.match(html, /15:30/);
  assert.match(html, /Текущая ставка/);
  assert.match(html, /href="https:\/\/www.copart.com\/lot\/65507986"/);
  assert.doesNotMatch(html, /card-foot|Что изменилось|↻ Обновить проект/);
  assert.ok(client.getQueryState(["auction", "project", "copart", 4, false]));
});
test("auction deep link reads the saved single lot without marking it viewed", async (t) => {
  const { html, client } = render(t, { search: "?source=copart&view=car&project=4&car=40", auctionCar: { ...auctionContext, project_id: 4, lot: savedLot } });
  assert.match(html, /Saved Audi A5/);
  assert.match(html, /Сохранённые сведения/);
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push([String(url), options?.method || "GET"]); return new Response(JSON.stringify({ ...auctionContext, lot: savedLot }), { headers: { "content-type": "application/json" } }); });
  const query = client.getQueryCache().find({ queryKey: ["auction", "car", "copart", 4, 40], exact: true });
  await client.fetchQuery({ ...query.options, staleTime: 0 });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1], "GET");
  assert.match(calls[0][0], /\/cars\/40\/auction\?project_id=4&computer_timezone=/);
});
test("failed stored auction reads keep stale data with an error instead of an empty success", (t) => {
  const { html } = render(t, { search: "?source=copart&view=project&project=4", auctionProject: savedProject, auctionError: true });
  assert.match(html, /role="alert"/);
  assert.match(html, /Не удалось прочитать сохранённые данные/);
  assert.match(html, /Saved Audi A5/);
  const empty = render(t, { search: "?source=copart&view=project&project=4", auctionProject: { ...savedProject, lots: [] }, auctionError: true }).html;
  assert.match(empty, /Не удалось прочитать сохранённые данные/);
  assert.doesNotMatch(empty, /Сохранённых лотов нет/);
});
test("a wrong-site auction response offers the correct workspace rather than rendering its card", (t) => {
  const { html } = render(t, { search: "?source=iaa&view=project&project=4", auctionProject: savedProject });
  assert.match(html, /Эти данные относятся к Copart/);
  assert.doesNotMatch(html, /Saved Audi A5/);
});

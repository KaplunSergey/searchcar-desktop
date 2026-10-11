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
  scanError = false, availabilityError = false, launchOptions = available, activeRuns = [], projectsOverride } = {}) {
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
  client.setQueryData(["scans"], []);
  client.setQueryData(["scans", "encar"], activeRuns);
  client.setQueryData(["scans", "active"], activeRuns);
  if (launchOptions) client.setQueryData(["launch-options"], launchOptions);
  if (availabilityError) client.getQueryCache().find({ queryKey: ["launch-options"], exact: true }).setState({ status: "error", error: new Error("fixture unavailable") });
  if (scanError) client.getQueryCache().find({ queryKey: ["scans", "encar"], exact: true }).setState({ status: "error", error: new Error("fixture offline") });
  if (projectOverride) client.setQueryData(["project", projectOverride.id], projectOverride);
  if (carOverride) client.setQueryData(["car", carOverride.id, 1], carOverride);
  const html = renderToStaticMarkup(React.createElement(QueryClientProvider, { client },
    React.createElement(App, { currentUser: { ...user, preferred_locale: locale }, onSessionChanged() {} })));
  t.after(() => client.clear());
  return { html, client };
}

test("Encar still renders the original projects toolbar and project rows", (t) => {
  const { html } = render(t);
  assert.match(html, /projects-toolbar-controls/);
  assert.match(html, /Encar fixture/);
  assert.match(html, /projects-refresh-button/);
});
test("the real app groups three sites and separates application settings", (t) => {
  const { html } = render(t);
  assert.match(html, /Площадки/);
  assert.match(html, /Аукционы/);
  assert.match(html, /Copart/);
  assert.match(html, /IAA/);
  assert.match(html, /Настройки приложения/);
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
  assert.doesNotMatch(html, /projects-toolbar-controls|Encar fixture|2020 Mercedes/);
  assert.ok(client.getQueryState(["projects", "copart"]));
  assert.ok(client.getQueryState(["scans", "copart"]));
  assert.ok(client.getQueryState(["scans", "active"]));
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
  assert.equal(query.options.refetchInterval, 30_000);
});
test("a project-specific invalid link is explained without disabling the whole site", (t) => {
  const options = { ...available, projects: [{ ...available.projects[0], can_search: false, reason: "INVALID_SOURCE_URL" }] };
  const { html } = render(t, { search: "?view=project&project=1", launchOptions: options,
    projectOverride: { id: 1, name: "Saved project", source_key: "encar", cars: 0 } });
  assert.match(html, /Проверьте ссылку в настройках проекта/);
  assert.match(html, /aria-label="Encar: Модуль поиска подключён"/);
  assert.match(html, /<button[^>]*disabled[^>]*>↻ Обновить проект/);
});

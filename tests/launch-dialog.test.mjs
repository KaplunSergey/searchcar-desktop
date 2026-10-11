import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const { LaunchDialog, launchableProjects, launchSummary } = await import("../app/launch-dialog.tsx");

const project = (id, overrides = {}) => ({ id, name: `Project ${id}`, source_key: "encar", can_search: true, reason: null, active_status: null, scan_run_id: null, ...overrides });
const options = {
  sources: [{ source_key: "encar", family: "MARKETPLACE", can_search: true, reason: null }],
  projects: [project(1), project(2, { active_status: "QUEUED" }), project(3, { active_status: "RUNNING" }),
    project(4, { source_key: "copart", can_search: false, reason: "SOURCE_NOT_CONNECTED" }),
    project(5, { can_search: false, reason: "LICENSE_SOURCE_NOT_ALLOWED" })],
};
function render(overrides = {}) {
  return renderToStaticMarkup(React.createElement(LaunchDialog, {
    locale: "ru", options, loading: false, failed: false, retry() {}, close() {},
    launch() { throw new Error("Rendering must not launch a scan"); }, errorMessage: () => "failure", ...overrides,
  }));
}
test("explicit project selection excludes busy, unlicensed and unshipped sources", () => {
  assert.deepEqual(launchableProjects(options).map((row) => row.id), [1]);
  assert.deepEqual(launchableProjects({ ...options, projects: [project(6, { source_key: "unknown" })] }), []);
  assert.deepEqual(launchableProjects(), []);
});
test("the actual picker groups sites and exposes queued, running and unavailable reasons", () => {
  const html = render();
  assert.match(html, /<dialog[^>]*aria-labelledby="launch-title"/);
  assert.match(html, /Площадки/);
  assert.match(html, /Аукционы/);
  assert.match(html, /Выбрать все проекты Encar/);
  assert.match(html, /Выбрать все доступные/);
  assert.match(html, /Уже в очереди/);
  assert.match(html, /Обновляется/);
  assert.match(html, /Сайт недоступен по лицензии/);
  assert.match(html, /Поиск ещё не подключён/);
  assert.match(html, /disabled="">Запустить обновление \(0\)/);
  assert.doesNotMatch(html, /checked=""/);
});
test("loading, failure and empty states cannot silently enable a launch; Ukrainian text is localized", () => {
  assert.match(render({ options: undefined, loading: true }), /Загрузка доступных проектов/);
  const failed = render({ failed: true });
  assert.match(failed, /role="alert"/);
  assert.match(failed, /Повторить/);
  assert.doesNotMatch(failed, /Готов к запуску/);
  assert.match(failed, /disabled="">Запустить обновление \(0\)/);
  const empty = render({ locale: "uk", options: { sources: [], projects: [] } });
  assert.match(empty, /Майданчики/);
  assert.match(empty, /Проєктів поки немає/);
  assert.match(empty, /Оберіть хоча б один доступний проєкт/);
});
test("enqueue feedback does not call an already-active project newly queued", () => {
  const result = { id: 1, created: false, status: "ALREADY_ACTIVE", projects: [{ project_id: 1, status: "ALREADY_ACTIVE", scan_run_id: 1 }] };
  assert.equal(launchSummary(result, "ru"), "В очереди: 0 · Уже активны: 1");
  result.projects.push({ project_id: 2, status: "QUEUED", scan_run_id: 2 });
  assert.equal(launchSummary(result, "uk"), "У черзі: 1 · Уже активні: 1");
});

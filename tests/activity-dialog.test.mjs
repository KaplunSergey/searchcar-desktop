import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
const { ActivityDialog, activityProjects, cancellationBody } = await import("../app/activity-dialog.tsx");
const run = { id: 7, kind: "PROJECTS", status: "RUNNING", progress: 25, payload: {
  project_ids: [1, 2, 3, 4], current_project_id: 1,
  project_statuses: { 3: "SUCCEEDED", 4: "CANCEL_REQUESTED" },
  project_plans: [{ project_id: 1, source_key: "encar", name: "Encar one" },
    { project_id: 2, source_key: "copart", name: "Copart waiting" },
    { project_id: 3, source_key: "iaa", name: "IAA complete" },
    { project_id: 4, source_key: "iaa", name: "IAA cancelling" }],
} };
function render(overrides = {}) {
  let calls = 0;
  const html = renderToStaticMarkup(React.createElement(ActivityDialog, {
    locale: "ru", runs: [run], projects: [], loading: false, failed: false,
    retry() {}, cancel() { calls++; }, close() {}, ...overrides,
  }));
  assert.equal(calls, 0, "Viewing activity must not cancel anything");
  return html;
}
test("project, site and whole-run commands contain only their explicit frozen scope", () => {
  assert.deepEqual(cancellationBody({ scanId: 7, scope: "PROJECT", projectId: 2 }), { project_ids: [2] });
  assert.deepEqual(cancellationBody({ scanId: 7, scope: "SITE", sourceKey: "copart" }), { source_keys: ["copart"] });
  assert.deepEqual(cancellationBody({ scanId: 7, scope: "RUN" }), { all_projects: true });
});
test("activity distinguishes waiting, running, completed and cancellation; frozen names/sites win", () => {
  const rows = activityProjects(run, [{ id: 1, source_key: "iaa", name: "Renamed later" }]);
  assert.deepEqual(rows.map(row => row.status), ["RUNNING", "QUEUED", "SUCCEEDED", "CANCEL_REQUESTED"]);
  assert.equal(rows[0].source_key, "encar");
  assert.equal(rows[0].name, "Encar one");
  assert.equal(activityProjects({ ...run, status: "CANCEL_REQUESTED" }, [])[1].status, "CANCEL_REQUESTED");
});
test("unknown/deleted projects remain visible without inventing a site", () => {
  const legacy = { ...run, payload: { project_ids: [42] } };
  assert.deepEqual(activityProjects(legacy, []), [{ id: 42, name: "#42", source_key: "", status: "QUEUED" }]);
  assert.match(render({ runs: [legacy] }), /#42.*Сайт неизвестен/);
});
test("actual dialog groups sites and offers only cancellable project/site scopes", () => {
  const html = render();
  assert.match(html, /Encar one/);
  assert.match(html, /Copart waiting/);
  assert.match(html, /Отменить весь запуск.*#7/);
  assert.match(html, /IAA complete<\/b><small>Завершено<\/small><\/span><button[^>]*disabled/);
  assert.match(html, /IAA cancelling<\/b><small>Отмена запрошена<\/small><\/span><button[^>]*disabled/);
  assert.match(html, /<b>IAA<\/b><button[^>]*disabled/);
});
test("loading, failed and empty queue are distinct and localized", () => {
  assert.match(render({ loading: true, runs: [] }), /Загрузка очереди/);
  assert.doesNotMatch(render({ loading: true, runs: [] }), /Активных запусков нет/);
  assert.match(render({ failed: true, runs: [] }), /role="alert"/);
  assert.doesNotMatch(render({ failed: true, runs: [] }), /Активных запусков нет/);
  assert.match(render({ locale: "uk", runs: [] }), /Активних запусків немає/);
});
test("manual car refresh allows only whole-job cancellation", () => {
  const html = render({ runs: [{ ...run, kind: "CARS", payload: { car_ids: [1, 2] } }] });
  assert.match(html, /Обновление автомобилей: 2/);
  assert.match(html, /Отменить весь запуск/);
  assert.doesNotMatch(html, /Отменить сайт|Отменить проект/);
});

test("manual site verification stays cancellable and is explained in Russian and Ukrainian", () => {
  const waiting={...run,payload:{...run.payload,pagination:{1:{interaction:{state:'WAITING_FOR_USER',source_key:'encar',reason:'CAPTCHA',expires_at:'2026-10-12T12:03:00Z'}}}}};
  const html=render({runs:[waiting]});
  assert.match(html,/Ожидается проверка сайта/);
  assert.match(html,/открытом окне браузера/);
  assert.match(html,/Отменить проект/);
  assert.match(render({locale:'uk',runs:[waiting]}),/Очікується перевірка сайту/);
  assert.doesNotMatch(render({runs:[{...waiting,status:'FAILED'}]}),/Ожидается проверка сайта/);
});

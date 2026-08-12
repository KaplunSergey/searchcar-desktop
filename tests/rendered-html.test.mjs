import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);
async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", {headers:{accept:"text/html"}}), {ASSETS:{fetch:async()=>new Response("Not found",{status:404})}}, {waitUntil(){},passThroughOnException(){}});
}
test("renders SearchCar without starter artifacts", async()=>{
  const response=await render();assert.equal(response.status,200);
  const html=await response.text();assert.match(html,/<title>SearchCar<\/title>/i);
  assert.match(html,/Мониторинг объявлений Encar Korea/);
  assert.doesNotMatch(html,/Your site is taking shape|codex-preview|react-loading-skeleton/);
});
test("ships required product surfaces and localization",async()=>{
  const [page,translations,layout,pkg,details]=await Promise.all(["app/page.tsx","app/translations.ts","app/layout.tsx","package.json","app/details.css"].map(p=>readFile(new URL(p,root),"utf8")));
  for(const word of ["Projects","Project","Car","Scans","Settings","navigator.clipboard","ResponsiveContainer"])assert.match(page,new RegExp(word));
  assert.match(translations,/export const dict\s*=\s*\{\s*ru,\s*uk\s*\}/);assert.match(layout,/og\.png/);
  assert.match(pkg,/@tanstack\/react-query/);assert.match(pkg,/recharts/);assert.doesNotMatch(pkg,/react-loading-skeleton/);
  assert.match(details,/\.report-list\{[^}]*grid-auto-rows:max-content[^}]*align-content:start/);
  assert.match(page,/function Favorites\(/);
  assert.match(page,/function ScanReportSection\(/);
  assert.match(page,/is-favorite/);
  assert.match(page,/is-unviewed/);
  assert.match(details,/\.car-row\.is-favorite\{/);
  assert.match(details,/\.car-row\.is-unviewed\{/);
  assert.match(details,/\.scan-report-toggle\{/);
  assert.match(page,/localStorage\.setItem\("encar-project-sort"/);
  assert.match(page,/telegram_url/);
  assert.match(page,/condition-chip/);
  assert.doesNotMatch(page,/progress-chip/);
  assert.match(page,/report-favorite/);
  assert.match(page,/carsCountLabel/);
  assert.match(page,/projectSingular/);
  assert.match(page,/projectFavorites/);
  assert.match(page,/removeCarFromProject/);
  assert.match(page,/project_statuses/);
  assert.match(page,/under_contract/);
  assert.match(details,/\.contract-chip\{/);
  assert.match(page,/newListingsFound/);
  assert.match(details,/\.car-row\.is-unavailable\{/);
  assert.match(details,/\.car-row\.is-sold\{/);
  assert.match(details,/\.running-track\{/);
  assert.match(page,/className="project-actions"/);
  assert.match(details,/\.project-grid \.project-row\{[^}]*grid-template-rows:auto auto 1px auto/);
  assert.match(page,/cookieValue\(csrfCookieName\) \|\| csrfToken/);
  assert.doesNotMatch(page,/relationMutation\.mutate\(\{ field: "viewed"/);
  assert.match(page,/currentUser\.preferred_locale \|\| "ru"/);
  assert.match(page,/request<AuthUser>\("\/auth\/profile"/);
  assert.match(page,/newListingsCount > 0/);
  assert.match(page,/const displayedReport = current && completedProjectCount \? current : latestReport/);
  assert.match(page,/currentUpdateReport/);
  assert.match(page,/didAutoOpen/);
  assert.match(translations,/currentUpdateReport: "Промежуточный отчёт"/);
});
test("web and desktop dropdown controls use the same cross-platform styling",async()=>{
  const [entry,sharedStyles,desktopStyles]=await Promise.all([
    readFile(new URL("desktop/frontend/main.tsx",root),"utf8"),
    readFile(new URL("app/globals.css",root),"utf8"),
    readFile(new URL("desktop/frontend/desktop.css",root),"utf8"),
  ]);
  assert.match(entry,/import "\.\/desktop\.css"/);
  assert.match(desktopStyles,/reuses the shared dropdown styling/);
  assert.match(sharedStyles,/select:not\(\[multiple\]\)\{[^}]*appearance:none/s);
  assert.match(sharedStyles,/background-image:url\(/);
  assert.match(sharedStyles,/background-position:right 16px center/);
  assert.match(sharedStyles,/padding-right:44px!important/);
  assert.match(sharedStyles,/select:not\(\[multiple\]\):focus-visible/);
});
test("desktop exit requires confirmation and preserves graceful scan cancellation",async()=>{
  const [shell,runtime,queue]=await Promise.all([
    readFile(new URL("desktop/src-tauri/src/lib.rs",root),"utf8"),
    readFile(new URL("backend/app/desktop_runtime.py",root),"utf8"),
    readFile(new URL("backend/app/job_queue.py",root),"utf8"),
  ]);
  assert.match(shell,/fn request_exit_confirmation/);
  assert.match(shell,/Выйти и остановить поиск/);
  assert.match(shell,/WindowEvent::CloseRequested/);
  assert.match(shell,/RunEvent::ExitRequested/);
  assert.match(shell,/request_sidecar_shutdown/);
  assert.match(runtime,/request_shutdown_cancellation/);
  assert.match(queue,/job\.status = "CANCEL_REQUESTED"/);
  assert.match(queue,/cancellation_reason.*APPLICATION_SHUTDOWN/s);
});
test("desktop licensing keeps device secrets outside portable data",async()=>{
  const [client,license,page,config]=await Promise.all([
    readFile(new URL("backend/app/desktop_license_client.py",root),"utf8"),
    readFile(new URL("backend/app/desktop_license.py",root),"utf8"),
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("desktop/license-service.json",root),"utf8"),
  ]);
  assert.match(client,/class MacOSKeychainStore/);
  assert.match(client,/class WindowsDpapiStore/);
  assert.match(client,/SEARCHCAR-DEVICE-REQUEST-V1/);
  assert.match(client,/run_periodic_license_sync/);
  assert.match(license,/SEARCHCAR-LICENSE-LEASE-V1/);
  assert.match(page,/desktop\/license\/trial/);
  assert.match(page,/desktop\/license\/redeem/);
  assert.match(page,/desktop\/license\/refresh/);
  assert.deepEqual(JSON.parse(config),{
    protocol_version:1,
    service_url:"",
    public_keys:{},
    enforcement:"disabled",
  });
});

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
});
test("desktop dropdown controls use cross-platform styling",async()=>{
  const [entry,desktopStyles]=await Promise.all([
    readFile(new URL("desktop/frontend/main.tsx",root),"utf8"),
    readFile(new URL("desktop/frontend/desktop.css",root),"utf8"),
  ]);
  assert.match(entry,/import "\.\/desktop\.css"/);
  assert.match(desktopStyles,/select\s*\{[^}]*appearance:\s*none/s);
  assert.match(desktopStyles,/background-image:\s*url\(/);
  assert.match(desktopStyles,/select:focus-visible/);
});

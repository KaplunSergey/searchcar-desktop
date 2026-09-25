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
  assert.match(translations,/projectSingular:/);
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
  assert.match(page,/new EventSource\(`\$\{api\}\/events`, \{ withCredentials: true \}\)/);
  assert.match(page,/refetchInterval: 30_000/);
  assert.match(page,/const displayedReport = current \|\| latestReport/);
  assert.match(page,/currentUpdateReport/);
  assert.match(page,/className="projects-collapsible-content"/);
  assert.match(page,/<>{t\("projects"\)}: \{projects\.length\} · \{t\("carsCountLabel"\)\}: \{totalCars\}<\/>/);
  assert.match(page,/t\("carsCountLabel"\).*totalCars/);
  assert.match(page,/className="button projects-refresh-button"/);
  assert.match(page,/disabled=\{!projects\.length \|\| Boolean\(current\)\}/);
  assert.match(page,/`\$\{t\("refreshSelected"\)\} \(\$\{selected\.length\}\)`/);
  assert.match(page,/`\$\{t\("updatingProject"\)\}…`/);
  const projectToolbarAt=page.indexOf('className="projects-toolbar-controls"');
  const projectRefreshAt=page.indexOf('className="button projects-refresh-button"');
  const projectCollapseAt=page.indexOf('className="projects-collapse-toggle"');
  assert.ok(projectToolbarAt < projectRefreshAt && projectRefreshAt < projectCollapseAt);
  assert.doesNotMatch(page,/projectsCollapsed && current/);
  assert.doesNotMatch(page,/label=\{t\("activeProjects"\)\}/);
  assert.doesNotMatch(page,/label=\{t\("activeScans"\)\}/);
  assert.doesNotMatch(page,/label=\{t\("attention"\)\}/);
  assert.match(page,/const displayedPagination = Object\.values\(/);
  assert.match(page,/displayedPagination\.reduce\(\(sum, item\) => sum \+ item\.total_pages, 0\)/);
  assert.match(page,/aria-expanded=\{!projectsCollapsed\}/);
  assert.match(details,/\.projects-panel\.collapsed \.projects-collapsible-content\{grid-template-rows:0fr/);
  assert.match(page,/didAutoOpen/);
  assert.match(page,/search_page_mode: project\?\.search_page_mode \|\| "ALL_PAGES"/);
  assert.match(page,/className="failure-listing-link"/);
  assert.match(page,/openExternalUrl\(failure\.url/);
  assert.match(translations,/initialProjectScanHelp:/);
  assert.match(translations,/openListing:/);
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
test("scan reports combine project and status filters",async()=>{
  const [page,styles]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("app/details.css",root),"utf8"),
  ]);
  assert.match(page,/const availableProjects = useMemo/);
  assert.match(page,/import \{ filterReportItems, reportProjectIds \} from "\.\/report-filter"/);
  assert.match(page,/function reportProjectNames\(/);
  assert.match(page,/currentProjectNames\.get\(id\)[\s\S]*savedNames\[index\]/);
  assert.match(page,/const sorted = filterReportItems\(/);
  assert.match(page,/setExcludedProjectIds\(new Set\(\)\);[\s\S]*setExcludedChanges\(new Set\(\)\)/);
  assert.match(page,/projects=\{projectsQuery\.data \|\| \[\]\}/);
  assert.match(styles,/\.report-filter-menu\{[^}]*max-height:min\(70vh,520px\)[^}]*overflow:auto/);
});
test("scan history uses expandable run cards and keeps report filters beside changes",async()=>{
  const [page,styles,translations]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("app/details.css",root),"utf8"),
    readFile(new URL("app/translations.ts",root),"utf8"),
  ]);
  assert.match(page,/className="scan-history-list"/);
  assert.match(page,/className=\{`scan-run-card \$\{scan\.status\.toLowerCase\(\)\} \$\{open \? "open" : ""\}`\}/);
  assert.match(page,/className="scan-run-header"/);
  assert.match(page,/className="scan-run-progress"/);
  assert.match(page,/heading=\{t\("changesTitle"\)\}/);
  assert.doesNotMatch(page,/className="table-head"/);
  assert.match(styles,/\.scan-run-card\{/);
  assert.match(styles,/\.report-filter-toolbar\.with-heading\{/);
  assert.match(translations,/changesTitle: "Изменения"/);
});
test("rental listings label monthly payments and rental terms",async()=>{
  const [page,translations]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("app/translations.ts",root),"utf8"),
  ]);
  assert.match(page,/offer_type/);
  assert.match(page,/rental_monthly_payment_krw/);
  assert.match(page,/rental_term_months/);
  assert.match(page,/rental_acquisition_price_krw/);
  assert.match(page,/vehicle_price_krw/);
  assert.match(translations,/monthlyPayment:/);
  assert.match(translations,/perMonth:/);
  assert.match(translations,/rentalOffer:/);
});
test("lease listings keep a distinct offer type and monthly payment",async()=>{
  const [page,scanner,translations]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("backend/app/scanner.py",root),"utf8"),
    readFile(new URL("app/translations.ts",root),"utf8"),
  ]);
  assert.match(page,/lease_monthly_payment_krw/);
  assert.match(page,/lease_term_months/);
  assert.match(scanner,/DETAIL_LEASE_MONTHLY/);
  assert.match(translations,/leaseOffer:/);
});
test("desktop onboarding and notifications are clear inside a small app window",async()=>{
  const [page,styles]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("app/globals.css",root),"utf8"),
  ]);
  assert.doesNotMatch(page,/Логин и пароль не нужны|Логін і пароль не потрібні/);
  assert.match(styles,/\.toast\{[^}]*top:20px[^}]*bottom:auto[^}]*transform:translateX\(-50%\)/);
  assert.match(styles,/\.toast\{[^}]*max-height:calc\(100vh - 40px\)[^}]*overflow:auto[^}]*overflow-wrap:anywhere/);
});
test("passwordless desktop backup tools support portable import and export",async()=>{
  const [page,backend,styles,translations]=await Promise.all([
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("backend/app/main.py",root),"utf8"),
    readFile(new URL("app/globals.css",root),"utf8"),
    readFile(new URL("app/translations.ts",root),"utf8"),
  ]);
  assert.match(page,/type="file"/);
  assert.match(page,/accept="\.searchcar-backup,application\/octet-stream,application\/zip"/);
  assert.match(page,/\/desktop\/backups\/import\?name=/);
  assert.match(page,/\/download`/);
  assert.match(backend,/async def import_desktop_backup\(/);
  assert.match(backend,/MAX_DESKTOP_BACKUP_UPLOAD_BYTES/);
  assert.match(backend,/validate_backup\(temporary\)/);
  assert.match(backend,/os\.replace\(temporary, destination\)/);
  assert.match(backend,/def download_desktop_backup\(/);
  assert.match(styles,/\.backup-file-input\{display:none\}/);
  assert.match(page,/restoreResultPresentation/);
  assert.match(page,/role="status"/);
  assert.match(translations,/restoreStatusPending: "Ожидает перезапуска"/);
  assert.match(translations,/restoreStatusRestored: "Восстановлено"/);
  assert.match(styles,/\.restore-result\.pending,/);
});
test("product version metadata has one checked source of truth", async () => {
  const [pkg, cargo, tauri, backend, frontend, script, page] = await Promise.all([
    "package.json",
    "desktop/src-tauri/Cargo.toml",
    "desktop/src-tauri/tauri.conf.json",
    "backend/app/version.py",
    "app/version.ts",
    "scripts/sync_app_version.mjs",
    "app/page.tsx",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  const version = JSON.parse(pkg).version;
  assert.match(cargo, new RegExp(`^version\\s*=\\s*"${version}"`, "m"));
  assert.equal(JSON.parse(tauri).version, version);
  assert.match(backend, new RegExp(`APP_VERSION\\s*=\\s*"${version}"`));
  assert.match(frontend, new RegExp(`APP_VERSION\\s*=\\s*"${version}"`));
  assert.match(script, /package\.json version must be SemVer/);
  assert.match(page, /import \{ APP_VERSION \} from "\.\/version"/);
  assert.match(page, /className="app-version"/);
  assert.match(page, /<span>v\{APP_VERSION\}<\/span>/);
  assert.match(page, /request<\{ status: "requested" \}>\("\/desktop\/update-check"/);
});
test("updater key generation keeps the private key outside Git with strict permissions", async () => {
  const [script, ignore, runbook] = await Promise.all([
    "scripts/generate_tauri_updater_key.command",
    ".gitignore",
    "docs/RELEASE_RUNBOOK.md",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  assert.match(script, /umask 077/);
  assert.match(script, /Refusing to store the updater private key inside the Git repository/);
  assert.match(script, /chmod 600 "\$TEMP_PRIVATE"/);
  assert.match(script, /TAURI_SIGNING_PRIVATE_KEY_PASSWORD/);
  assert.doesNotMatch(script, /cat "\$PRIVATE_KEY"/);
  assert.match(ignore, /\.searchcar-release-secrets\//);
  assert.match(runbook, /generate_tauri_updater_key\.command/);
  assert.match(runbook, /independent from the Cloudflare license signing key/i);
});
test("desktop updater uses the checked public key and signed CI artifacts", async () => {
  const [tauriConfig, updaterPublicKey, shell, runtime, macWorkflow, windowsWorkflow, publishWorkflow, sidecarBuild, updaterVerifier, cargo] = await Promise.all([
    "desktop/src-tauri/tauri.conf.json",
    "desktop/updater-public-key.txt",
    "desktop/src-tauri/src/lib.rs",
    "backend/app/desktop_runtime.py",
    ".github/workflows/macos-desktop.yml",
    ".github/workflows/windows-desktop.yml",
    ".github/workflows/publish-desktop-release.yml",
    "scripts/build_desktop_sidecar.py",
    "desktop/src-tauri/tools/verify_updater_signature.rs",
    "desktop/src-tauri/Cargo.toml",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  const tauri = JSON.parse(tauriConfig);
  const publicKey = updaterPublicKey.trim();
  assert.equal(tauri.bundle.createUpdaterArtifacts, true);
  assert.equal(tauri.plugins.updater.pubkey, publicKey);
  assert.deepEqual(tauri.plugins.updater.endpoints, [
    "https://github.com/KaplunSergey/searchcar-desktop-releases/releases/latest/download/latest.json",
  ]);
  assert.equal(tauri.plugins.updater.windows.installMode, "passive");
  assert.match(publicKey, /^[A-Za-z0-9+/=]+$/u);
  assert.match(shell, /tauri_plugin_updater::\{Update, UpdaterExt\}/);
  assert.match(shell, /fn start_update_check/);
  assert.match(shell, /UPDATE_CHECK_TIMEOUT: Duration = Duration::from_secs\(30\)/);
  assert.match(shell, /updater_builder\(\)[\s\S]*\.timeout\(UPDATE_CHECK_TIMEOUT\)/);
  assert.match(shell, /fn consume_update_check_request/);
  assert.match(runtime, /class DesktopUpdateCheckRelay/);
  assert.match(runtime, /"\/desktop\/update-check\/consume"/);
  assert.match(shell, /UPDATE_CHECK_INTERVAL/);
  assert.match(shell, /update_request_terminated\.load\(Ordering::SeqCst\)/);
  assert.match(shell, /periodic_update_terminated\.load\(Ordering::SeqCst\)/);
  assert.doesNotMatch(shell, /thread::spawn\(move \|\| loop \{\s*if !health_is_ready\(port\)/);
  assert.match(shell, /\.download\(/);
  assert.match(shell, /prepare_update_install/);
  assert.match(shell, /set_update_status/);
  assert.match(shell, /"downloading"/);
  assert.match(shell, /for attempt in 0\.\.2/);
  assert.match(shell, /\/desktop\/update\/prepare/);
  assert.match(shell, /UpdatePreparation::ActiveScan/);
  assert.match(runtime, /def prepare_desktop_update\(/);
  assert.match(runtime, /pre-update-/);
  assert.match(runtime, /\/desktop\/update\/abort/);
  assert.match(runtime, /class DesktopUpdateStateRelay/);
  assert.match(runtime, /\/desktop\/update\/status/);
  assert.match(shell, /stop_sidecar\(&install_handle\)/);
  assert.match(shell, /allow_programmatic_exit\(&install_handle\)/);
  assert.match(macWorkflow, /TAURI_SIGNING_PRIVATE_KEY: \$\{\{ secrets\.TAURI_SIGNING_PRIVATE_KEY \}\}/);
  assert.match(macWorkflow, /pnpm exec tauri signer sign "\$updater"/);
  assert.match(macWorkflow, /test -s "\$updater\.sig"/);
  assert.match(macWorkflow, /playwright-driver\/package\/cli\.js/);
  assert.match(macWorkflow, /--bin verify_updater_signature/);
  assert.match(macWorkflow, /cd work\/macos-artifact/);
  assert.match(windowsWorkflow, /TAURI_SIGNING_PRIVATE_KEY: \$\{\{ secrets\.TAURI_SIGNING_PRIVATE_KEY \}\}/);
  assert.match(windowsWorkflow, /updater_signed = \$true/);
  assert.match(windowsWorkflow, /SearchCar-Desktop-Windows-x64-setup\.exe/);
  assert.match(windowsWorkflow, /\$artifactSignature = "\$artifactInstaller\.sig"/);
  assert.match(windowsWorkflow, /actions\/cache\/restore@v4/);
  assert.match(windowsWorkflow, /path: work\/nuitka\/cache/);
  assert.match(windowsWorkflow, /--bin verify_updater_signature/);
  assert.match(publishWorkflow, /workflow_dispatch:/);
  assert.match(publishWorkflow, /RELEASES_REPO_TOKEN/);
  assert.match(publishWorkflow, /actions\/download-artifact@v4/);
  assert.match(publishWorkflow, /generate_tauri_updater_manifest\.mjs/);
  assert.match(publishWorkflow, /--draft=false/);
  assert.match(publishWorkflow, /releases\/latest\/download\/latest\.json/);
  assert.match(publishWorkflow, /fetch-depth: 0/);
  assert.match(publishWorkflow, /Run this workflow from tag \$tag/);
  assert.match(publishWorkflow, /GITHUB_REF_TYPE/);
  assert.match(publishWorkflow, /work\/SHA256SUMS\.txt/);
  assert.match(sidecarBuild, /Nuitka is still compiling/);
  assert.match(sidecarBuild, /--report=/);
  assert.match(sidecarBuild, /backend_root \/ "requirements\.txt"/);
  assert.match(sidecarBuild, /backend_root \/ "alembic" \/ "script\.py\.mako"/);
  assert.match(updaterVerifier, /PublicKey::decode/);
  assert.match(updaterVerifier, /decode\(encoded_signature\.trim\(\)\)/);
  assert.match(updaterVerifier, /Signature::decode\(&decoded_signature\)/);
  assert.doesNotMatch(updaterVerifier, /Signature::from_file/);
  assert.match(updaterVerifier, /public_key\s*\.verify\(&artifact, &signature, false\)/);
  assert.match(windowsWorkflow, /--features updater-signature-verifier/);
  assert.match(macWorkflow, /--features updater-signature-verifier/);
  assert.match(macWorkflow, /Tampered updater unexpectedly passed signature verification/);
  assert.match(windowsWorkflow, /Tampered updater unexpectedly passed signature verification/);
  assert.match(windowsWorkflow, /Tampered updater unexpectedly passed signature verification[\s\S]*\$LASTEXITCODE = 0/);
  assert.match(cargo, /path = "tools\/verify_updater_signature\.rs"/);
  assert.match(cargo, /required-features = \["updater-signature-verifier"\]/);
  assert.doesNotMatch(cargo, /path = "src\/bin\/verify_updater_signature\.rs"/);
});
test("local desktop launchers keep macOS and Windows builds reproducible", async () => {
  const [macLauncher, windowsLauncher, windowsBuild, windowsSmoke, sidecarBuild, buildGuide, rustMain, rustShell, startupPage] = await Promise.all([
    "Build SearchCar for macOS.command",
    "Build SearchCar for Windows.cmd",
    "Build SearchCar for Windows.ps1",
    "scripts/windows_desktop_smoke.ps1",
    "scripts/build_desktop_sidecar.py",
    "docs/DESKTOP_BUILD.md",
    "desktop/src-tauri/src/main.rs",
    "desktop/src-tauri/src/lib.rs",
    "public/searchcar-startup.html",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  assert.match(macLauncher, /scripts\/build_mac_fixed\.command/);
  assert.match(windowsLauncher, /pwsh\.exe -NoLogo -NoProfile -ExecutionPolicy Bypass/);
  assert.match(windowsBuild, /stable-x86_64-pc-windows-msvc/);
  assert.match(windowsBuild, /scripts\\windows_desktop_smoke\.ps1/);
  assert.match(windowsBuild, /Invoke-CheckedNative "Windows NSIS installer build"/);
  assert.match(windowsBuild, /"desktop:tauri:build", "--bundles", "nsis", "--no-sign", "--ci"/);
  assert.match(windowsBuild, /Invoke-CheckedNative "Backend tests"/);
  assert.match(windowsBuild, /failed with exit code \$exitCode/);
  assert.match(windowsBuild, /\[switch\]\$ResumeAfterSidecar/);
  assert.match(windowsBuild, /\[switch\]\$RebuildSidecar/);
  assert.match(windowsBuild, /Transcript log is unavailable; continuing without it/);
  assert.match(windowsBuild, /PowerShell 7 x64 is required/);
  assert.match(windowsBuild, /SearchCar-Desktop-Windows-x64-setup\.exe/);
  assert.match(windowsBuild, /TAURI_SIGNING_PRIVATE_KEY/);
  assert.match(windowsSmoke, /RandomNumberGenerator\]::Create\(\)/);
  assert.doesNotMatch(windowsSmoke, /RandomNumberGenerator\]::GetBytes/);
  assert.match(windowsSmoke, /HealthTimeoutSeconds = 300/);
  assert.match(windowsSmoke, /System\.Net\.Http\.HttpClientHandler/);
  assert.match(windowsSmoke, /UseProxy = \$false/);
  assert.match(windowsSmoke, /Invoke-LoopbackHttpRequest/);
  assert.match(windowsSmoke, /LAST HEALTH ERROR/);
  assert.match(windowsSmoke, /Invoke-LoggedSidecarCommand/);
  assert.match(windowsSmoke, /compiled-database-check\.stderr\.log/);
  assert.doesNotMatch(windowsSmoke, /Invoke-WebRequest/);
  assert.doesNotMatch(windowsSmoke, /-NoProxy/);
  assert.match(windowsSmoke, /Stop-CompiledSidecarTree/);
  assert.match(windowsSmoke, /-WindowStyle Hidden/);
  assert.match(sidecarBuild, /--onefile-cache-mode=cached/);
  assert.match(sidecarBuild, /if mode == "onefile":/);
  assert.doesNotMatch(sidecarBuild, /mode == "onefile" and platform\.system\(\) == "Windows"/);
  assert.match(sidecarBuild, /--onefile-no-compression/);
  assert.match(sidecarBuild, /--windows-console-mode=hide/);
  assert.match(sidecarBuild, /command\.remove\("--remove-output"\)/);
  assert.match(sidecarBuild, /\{CACHE_DIR\}\/SearchCar\/searchcar-core/);
  assert.match(sidecarBuild, /sidecar_source_fingerprint/);
  assert.match(sidecarBuild, /onefile_cache_fingerprint/);
  assert.match(rustMain, /windows_subsystem = "windows"/);
  assert.match(rustShell, /WebviewUrl::App\("searchcar-startup\.html"\.into\(\)\)/);
  assert.match(rustShell, /STARTUP_TIMEOUT: Duration = Duration::from_secs\(5 \* 60\)/);
  assert.match(rustShell, /Локальный сервис завершился во время запуска/);
  assert.match(startupPage, /Запускаем SearchCar/);
  assert.match(startupPage, /Проверяем локальные данные/);
  assert.match(buildGuide, /Local Windows x64 build without GitHub Actions/);
  assert.match(buildGuide, /-RebuildSidecar/);
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
  assert.match(shell,/SEARCHCAR_DESKTOP_PARENT_PID/);
  assert.match(shell,/--allow-device-key-file-fallback/);
  assert.match(shell,/RunEvent::Exit => stop_sidecar/);
  assert.match(runtime,/request_shutdown_cancellation/);
  assert.match(runtime,/class DesktopInstanceLock/);
  assert.match(runtime,/watch_parent_process/);
  assert.match(runtime,/allow-device-key-file-fallback/);
  assert.match(queue,/job\.status = "CANCEL_REQUESTED"/);
  assert.match(queue,/cancellation_reason.*APPLICATION_SHUTDOWN/s);
});
test("desktop licensing keeps device secrets outside portable data",async()=>{
  const [client,license,page,onboarding,config,workerConfig,ownerUi,service]=await Promise.all([
    readFile(new URL("backend/app/desktop_license_client.py",root),"utf8"),
    readFile(new URL("backend/app/desktop_license.py",root),"utf8"),
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("backend/app/desktop_onboarding.py",root),"utf8"),
    readFile(new URL("desktop/license-service.json",root),"utf8"),
    readFile(new URL("license-service/wrangler.jsonc",root),"utf8"),
    readFile(new URL("license-service/src/owner_ui.ts",root),"utf8"),
    readFile(new URL("license-service/src/service.ts",root),"utf8"),
  ]);
  assert.match(client,/class MacOSKeychainStore/);
  assert.match(client,/class WindowsDpapiStore/);
  assert.match(client,/SEARCHCAR-DEVICE-REQUEST-V1/);
  assert.match(client,/run_periodic_license_sync/);
  assert.match(license,/SEARCHCAR-LICENSE-LEASE-V1/);
  assert.match(page,/desktop\/onboarding\/activate/);
  assert.match(page,/desktop\/onboarding\/transfer\/request/);
  assert.match(page,/desktop\/onboarding\/transfer\/claim/);
  assert.match(page,/Переносится только лицензия/);
  assert.match(page,/function DesktopOnboardingScreen/);
  assert.match(page,/canManageDesktopData=\{/);
  assert.match(page,/currentUser\.passwordless_workspace === true/);
  assert.match(page,/function DesktopSessionRecoveryScreen/);
  assert.match(page,/!currentUser\.passwordless_workspace \? \(/);
  assert.match(page,/desktop\/license\/redeem/);
  assert.match(page,/desktop\/license\/refresh/);
  assert.match(page,/desktop\/license\/transfer\/request/);
  assert.match(page,/desktop\/license\/transfer\/claim/);
  assert.match(page,/licenseSources/);
  assert.match(page,/license_deleted/);
  assert.match(page,/licenseSourcesDisabled/);
  assert.match(client,/def request_transfer/);
  assert.match(client,/def claim_transfer/);
  assert.match(license,/license state lives outside the SQLite backup/i);
  assert.match(page,/function responseErrorCode/);
  assert.match(page,/function licenseErrorMessage/);
  assert.match(page,/className="license-action-error" role="alert"/);
  assert.match(page,/LICENSE_SERVICE_CONNECT_FAILED/i);
  assert.match(onboarding,/DESKTOP_WORKSPACE_PASSWORD_MARKER/);
  assert.match(onboarding,/DESKTOP_WORKSPACE_CREATED/);
  assert.doesNotMatch(onboarding,/ensure_initial_admin/);
  assert.match(license,/LICENSE_SOURCE_NOT_ALLOWED/);
  assert.match(client,/LICENSE_DELETED/);
  assert.match(client,/_clear_local_license_state/);
  assert.match(page,/DesktopOnboardingScreen/);
  assert.match(ownerUi,/renderTransferSummary/);
  assert.match(ownerUi,/Локальные проекты и история не переносятся/);
  assert.match(ownerUi,/window\.confirm\('Подтвердить перенос лицензии/);
  assert.match(service,/LICENSE_NOT_ACTIVE/);
  assert.match(page,/const localAdminEnabled = currentUser\.role === "ADMIN"\s*&& \(desktopRuntimeQuery\.isError \|\| desktopRuntimeQuery\.data\?\.desktop === false\)/);
  assert.match(page,/\{localAdminEnabled \? \(/);
  const licenseConfig=JSON.parse(config);
  assert.equal(licenseConfig.protocol_version,1);
  assert.equal(licenseConfig.enforcement,"required");
  assert.equal(licenseConfig.device_key_storage,"file-preview");
  assert.match(licenseConfig.service_url,/^https:\/\/[a-z0-9.-]+$/u);
  assert.match(licenseConfig.public_keys["searchcar-license-v1"],/^[A-Za-z0-9_-]{43}$/u);
  const workerLicenseConfig=JSON.parse(workerConfig);
  const workerKeyId=workerLicenseConfig.vars.LICENSE_SIGNING_KEY_ID;
  assert.equal(workerKeyId,"searchcar-license-v1");
  assert.equal(
    workerLicenseConfig.vars.LICENSE_SIGNING_PUBLIC_KEY,
    licenseConfig.public_keys[workerKeyId],
  );
});

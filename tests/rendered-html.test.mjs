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
  const [tauriConfig, updaterPublicKey, shell, runtime, macWorkflow, windowsWorkflow] = await Promise.all([
    "desktop/src-tauri/tauri.conf.json",
    "desktop/updater-public-key.txt",
    "desktop/src-tauri/src/lib.rs",
    "backend/app/desktop_runtime.py",
    ".github/workflows/macos-desktop.yml",
    ".github/workflows/windows-desktop.yml",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  const tauri = JSON.parse(tauriConfig);
  const publicKey = updaterPublicKey.trim();
  assert.equal(tauri.bundle.createUpdaterArtifacts, true);
  assert.equal(tauri.plugins.updater.pubkey, publicKey);
  assert.deepEqual(tauri.plugins.updater.endpoints, [
    "https://github.com/KaplunSergey/searchcar-desktop/releases/latest/download/latest.json",
  ]);
  assert.equal(tauri.plugins.updater.windows.installMode, "passive");
  assert.match(publicKey, /^[A-Za-z0-9+/=]+$/u);
  assert.match(shell, /tauri_plugin_updater::\{Update, UpdaterExt\}/);
  assert.match(shell, /fn start_update_check/);
  assert.match(shell, /fn consume_update_check_request/);
  assert.match(runtime, /class DesktopUpdateCheckRelay/);
  assert.match(runtime, /"\/desktop\/update-check\/consume"/);
  assert.match(shell, /UPDATE_CHECK_INTERVAL/);
  assert.match(shell, /update\.download/);
  assert.match(shell, /stop_sidecar\(&install_handle\)/);
  assert.match(shell, /allow_programmatic_exit\(&install_handle\)/);
  assert.match(macWorkflow, /TAURI_SIGNING_PRIVATE_KEY: \$\{\{ secrets\.TAURI_SIGNING_PRIVATE_KEY \}\}/);
  assert.match(macWorkflow, /pnpm exec tauri signer sign "\$updater"/);
  assert.match(macWorkflow, /test -s "\$updater\.sig"/);
  assert.match(windowsWorkflow, /TAURI_SIGNING_PRIVATE_KEY: \$\{\{ secrets\.TAURI_SIGNING_PRIVATE_KEY \}\}/);
  assert.match(windowsWorkflow, /updater_signed = \$true/);
  assert.match(windowsWorkflow, /SearchCar-Desktop-Windows-x64-setup\.exe/);
  assert.match(windowsWorkflow, /\$artifactSignature = "\$artifactInstaller\.sig"/);
});
test("local desktop launchers keep macOS and Windows builds reproducible", async () => {
  const [macLauncher, windowsLauncher, windowsBuild, windowsSmoke, buildGuide] = await Promise.all([
    "Build SearchCar for macOS.command",
    "Build SearchCar for Windows.cmd",
    "Build SearchCar for Windows.ps1",
    "scripts/windows_desktop_smoke.ps1",
    "docs/DESKTOP_BUILD.md",
  ].map((path) => readFile(new URL(path, root), "utf8")));
  assert.match(macLauncher, /scripts\/build_mac_fixed\.command/);
  assert.match(windowsLauncher, /pwsh\.exe -NoLogo -NoProfile -ExecutionPolicy Bypass/);
  assert.match(windowsBuild, /stable-x86_64-pc-windows-msvc/);
  assert.match(windowsBuild, /scripts\\windows_desktop_smoke\.ps1/);
  assert.match(windowsBuild, /desktop:tauri:build --bundles nsis --no-sign --ci/);
  assert.match(windowsBuild, /\[switch\]\$ResumeAfterSidecar/);
  assert.match(windowsBuild, /Transcript log is unavailable; continuing without it/);
  assert.match(windowsBuild, /SearchCar-Desktop-Windows-x64-setup\.exe/);
  assert.match(windowsBuild, /TAURI_SIGNING_PRIVATE_KEY/);
  assert.match(windowsSmoke, /RandomNumberGenerator\]::Create\(\)/);
  assert.doesNotMatch(windowsSmoke, /RandomNumberGenerator\]::GetBytes/);
  assert.match(windowsSmoke, /HealthTimeoutSeconds = 600/);
  assert.match(windowsSmoke, /-NoProxy/);
  assert.match(windowsSmoke, /Stop-CompiledSidecarTree/);
  assert.match(windowsSmoke, /-WindowStyle Hidden/);
  assert.match(buildGuide, /Local Windows x64 build without GitHub Actions/);
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
  const [client,license,page,onboarding,config,workerConfig]=await Promise.all([
    readFile(new URL("backend/app/desktop_license_client.py",root),"utf8"),
    readFile(new URL("backend/app/desktop_license.py",root),"utf8"),
    readFile(new URL("app/page.tsx",root),"utf8"),
    readFile(new URL("backend/app/desktop_onboarding.py",root),"utf8"),
    readFile(new URL("desktop/license-service.json",root),"utf8"),
    readFile(new URL("license-service/wrangler.jsonc",root),"utf8"),
  ]);
  assert.match(client,/class MacOSKeychainStore/);
  assert.match(client,/class WindowsDpapiStore/);
  assert.match(client,/SEARCHCAR-DEVICE-REQUEST-V1/);
  assert.match(client,/run_periodic_license_sync/);
  assert.match(license,/SEARCHCAR-LICENSE-LEASE-V1/);
  assert.match(page,/desktop\/onboarding\/activate/);
  assert.match(page,/function DesktopOnboardingScreen/);
  assert.match(page,/desktop\/license\/redeem/);
  assert.match(page,/desktop\/license\/refresh/);
  assert.match(page,/desktop\/license\/transfer\/request/);
  assert.match(page,/desktop\/license\/transfer\/claim/);
  assert.match(page,/licenseSources/);
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
  assert.match(page,/DesktopOnboardingScreen/);
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

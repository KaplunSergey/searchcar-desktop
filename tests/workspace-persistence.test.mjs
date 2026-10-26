import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
const workspace = await import("../app/source-workspace.ts");
const route = (source, view, projectId = null, carId = null) => ({ source, view, projectId, carId });

test("a persisted multi-site workspace restores its current route, while explicit URLs still win", () => {
  const current = route("copart", "car", 4, 40);
  const stored = JSON.stringify({ current, sites: { encar: route("encar", "scans"), copart: current } });
  assert.deepEqual(workspace.readWorkspace("", stored), current);
  assert.deepEqual(workspace.readWorkspace("?view=scans", stored), route("encar", "scans"));
  assert.deepEqual(workspace.readWorkspace("?source=iaa&view=source-settings", stored), route("iaa", "source-settings"));
});

function storageWindow(t, initial = null, search = "") {
  const previous = globalThis.window;
  let stored = initial;
  globalThis.window = { location: { search }, localStorage: {
    getItem: () => stored,
    setItem: (key, value) => { assert.equal(key, workspace.WORKSPACE_STORAGE_KEY); stored = value; },
  } };
  t.after(() => { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; });
  return () => stored;
}

test("all three sites survive restart independently, including a saved lot and project", (t) => {
  const stored = storageWindow(t);
  const sites = {};
  const expected = { encar: route("encar", "scans"), copart: route("copart", "car", 4, 40), iaa: route("iaa", "project", 5) };
  for (const current of Object.values(expected)) workspace.persistWorkspace(current, sites);
  assert.deepEqual(workspace.initialWorkspace(), expected.iaa);
  assert.deepEqual(workspace.initialSiteRoutes(workspace.initialWorkspace()), expected);
  assert.deepEqual(workspace.readSiteRoutes(stored()), expected);
  assert.deepEqual(Object.keys(JSON.parse(stored())).sort(), ["current", "sites"]);
});

test("application settings and admin preserve the last actual section of every site", (t) => {
  const stored = storageWindow(t);
  const sites = { copart: route("copart", "source-settings"), encar: route("encar", "favorites") };
  for (const view of ["settings", "admin"]) {
    const current = route("copart", view);
    workspace.persistWorkspace(current, sites);
    assert.deepEqual(workspace.initialWorkspace(), current);
    assert.deepEqual(workspace.initialSiteRoutes(current), sites);
    assert.deepEqual(workspace.readSiteRoutes(stored()), sites);
  }
});

test("old single-route storage migrates, and an explicit new route preserves other sites", (t) => {
  storageWindow(t, JSON.stringify(route("copart", "scans")), "?source=iaa&view=source-settings");
  const current = workspace.initialWorkspace();
  const sites = workspace.initialSiteRoutes(current);
  assert.deepEqual(sites, { copart: route("copart", "scans"), iaa: route("iaa", "source-settings") });
  workspace.persistWorkspace(current, sites);
  assert.deepEqual(workspace.initialSiteRoutes(current), sites);
});

test("corrupt storage, cross-site routes, unsafe IDs and global sections are sanitized", () => {
  for (const stored of [null, "{bad", "null", "[]", "42"]) {
    assert.deepEqual(workspace.readSiteRoutes(stored), {});
  }
  const stored = JSON.stringify({ current: route("iaa", "settings"), sites: {
    encar: route("copart", "scans"), copart: route("copart", "car", 4, -2), iaa: route("iaa", "admin"),
    other: route("other", "scans"),
  } });
  assert.deepEqual(workspace.readSiteRoutes(stored), { copart: route("copart", "projects") });
});

test("blocked WebView storage still allows in-session navigation and explicit links", (t) => {
  storageWindow(t, null, "?source=iaa&view=scans");
  Object.defineProperty(globalThis.window, "localStorage", { get() { throw new Error("storage blocked"); } });
  const current = workspace.initialWorkspace();
  assert.deepEqual(current, route("iaa", "scans"));
  const sites = workspace.initialSiteRoutes(current);
  assert.doesNotThrow(() => workspace.persistWorkspace(route("copart", "source-settings"), sites));
  assert.deepEqual(sites, { iaa: current, copart: route("copart", "source-settings") });
});

test("quota failures do not discard the site's updated in-session route", (t) => {
  storageWindow(t);
  globalThis.window.localStorage.setItem = () => { throw new Error("quota exceeded"); };
  const sites = { encar: route("encar", "projects") };
  const current = route("encar", "scans");
  assert.doesNotThrow(() => workspace.persistWorkspace(current, sites));
  assert.deepEqual(sites.encar, current);
});

test("desktop restoration uses stable account preferences despite a new empty localhost origin", (t) => {
  storageWindow(t); // The new native port has a different, empty localStorage.
  const current = route("copart", "car", 4, 40);
  const stored = JSON.stringify({ current, sites: { copart: current, encar: route("encar", "scans") } });
  assert.deepEqual(workspace.initialWorkspace(stored), current);
  assert.deepEqual(workspace.initialSiteRoutes(current, stored), { copart: current, encar: route("encar", "scans") });
});

test("a desktop account without preferences never inherits another account's browser snapshot", (t) => {
  storageWindow(t, JSON.stringify({ current: route("copart", "scans"), sites: { copart: route("copart", "scans") } }));
  assert.deepEqual(workspace.initialWorkspace(null), route("encar", "projects"));
  assert.deepEqual(workspace.initialSiteRoutes(workspace.initialWorkspace(null), null), { encar: route("encar", "projects") });
  // Without a desktop profile field, the original web storage behavior remains.
  assert.deepEqual(workspace.initialWorkspace(), route("copart", "scans"));
});

test("explicit links override native preferences without discarding other site routes", (t) => {
  storageWindow(t, null, "?source=iaa&view=source-settings");
  const stored = JSON.stringify({ current: route("copart", "car", 4, 40), sites: { copart: route("copart", "car", 4, 40) } });
  const current = workspace.initialWorkspace(stored);
  assert.deepEqual(current, route("iaa", "source-settings"));
  assert.deepEqual(workspace.initialSiteRoutes(current, stored), { copart: route("copart", "car", 4, 40), iaa: current });
});

test("blocked browser storage does not block native restoration or producing the API snapshot", (t) => {
  storageWindow(t);
  Object.defineProperty(globalThis.window, "localStorage", { get() { throw new Error("storage blocked"); } });
  const current = route("copart", "car", 4, 40);
  const stored = JSON.stringify({ current, sites: { copart: current } });
  assert.deepEqual(workspace.initialWorkspace(stored), current);
  const sites = workspace.initialSiteRoutes(current, stored);
  const next = route("iaa", "source-settings");
  assert.deepEqual(JSON.parse(workspace.persistWorkspace(next, sites)), { current: next, sites: { copart: current, iaa: next } });
});

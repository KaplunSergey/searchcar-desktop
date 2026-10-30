import "./app-loader.mjs";
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
const workspace = await import("../app/source-workspace.ts");
const { SourceNavigation } = await import("../app/source-navigation.tsx");
const { LaunchDialog, launchableProjects } = await import("../app/launch-dialog.tsx");
const { App } = await import("../app/page.tsx");
const { auctionListingLink } = await import("../app/auction-workspace.tsx");

const definition = (key, name, family, ui) => ({ key, name, family, ui, domains: [`${key}.example.test`], capabilities: [] });
const catalog = [definition("encar", "Encar", "MARKETPLACE", "encar"),
  definition("copart", "Copart", "AUCTION", "auction"), definition("iaa", "IAA", "AUCTION", "auction"),
  definition("sampleauction", "Sample Auction", "AUCTION", "auction"),
  definition("samplemarket", "Sample Market", "MARKETPLACE", "fixture-market")];
const route = (source, view = "projects", projectId = null, carId = null) => ({ source, view, projectId, carId });
const options = { sources: catalog.map((site) => ({ source_key: site.key, family: site.family, can_search: true, reason: null })),
  projects: [{ id: 41, name: "Fourth-source project", source_key: "sampleauction", can_search: true, active_status: null, reason: null, scan_run_id: null }] };

test("a fourth and fifth registered site restore without a hard-coded route union or site count", () => {
  const current = route("sampleauction", "car", 41, 410);
  const sites = Object.fromEntries(catalog.map((site) => [site.key, route(site.key, "scans")]));
  sites.sampleauction = current;
  const stored = JSON.stringify({ current, sites });
  assert.deepEqual(workspace.readWorkspace("", stored, catalog), current);
  assert.deepEqual(workspace.readSiteRoutes(stored, catalog), sites);
  assert.deepEqual(workspace.readWorkspace("?source=samplemarket&view=project&project=51", stored, catalog), route("samplemarket", "project", 51));
  assert.deepEqual(workspace.normalizeRoute({ source: "unregistered", view: "scans" }, catalog), route("encar"));
});

test("navigation groups added sites by family and keeps unavailable modules visible", () => {
  const html = renderToStaticMarkup(React.createElement(SourceNavigation, {
    source: "sampleauction", view: "scans", locale: "uk", catalog, options: { ...options, sources: [] }, navigate() {}, selectSource() {},
  }));
  assert.match(html, /Sample Auction/);
  assert.match(html, /Sample Market/);
  assert.ok(html.indexOf("Sample Market") < html.indexOf("Аукціони"));
  assert.ok(html.indexOf("Sample Auction") > html.indexOf("Аукціони"));
  assert.match(html, /source-sampleauction-sections/);
  assert.match(html, /Пошук ще не підключено/);
});

test('license visibility applies to navigation and the launch picker, not module readiness', () => {
  const restricted = { ...options, licensed_sources: ['sampleauction'], sources: options.sources.map(site => ({ ...site, can_search: false, reason: 'SOURCE_UNAVAILABLE' })) };
  const nav = renderToStaticMarkup(React.createElement(SourceNavigation, {
    source: 'sampleauction', view: 'projects', locale: 'ru', catalog, options: restricted, navigate() {}, selectSource() {},
  }));
  assert.match(nav, /Sample Auction/);
  assert.doesNotMatch(nav, /Площадки|Encar|Copart|Sample Market/);
  const picker = renderToStaticMarkup(React.createElement(LaunchDialog, {
    catalog, locale: 'ru', options: restricted, loading: false, failed: false, retry() {}, close() {},
    launch() { throw new Error('render must never launch'); }, errorMessage: () => 'failure',
  }));
  assert.match(picker, /Sample Auction/);
  assert.doesNotMatch(picker, /Площадки|Encar|Copart|Sample Market/);
  assert.deepEqual(launchableProjects({ ...options, licensed_sources: [] }, catalog), []);
});

test("a registered extra source participates in the picker, but missing source availability cannot enable it", () => {
  assert.deepEqual(launchableProjects(options, catalog).map((project) => project.id), [41]);
  assert.deepEqual(launchableProjects({ ...options, sources: [] }, catalog), []);
  const html = renderToStaticMarkup(React.createElement(LaunchDialog, {
    catalog, locale: "ru", options, loading: false, failed: false, retry() {}, close() {},
    launch() { throw new Error("render must never launch"); }, errorMessage: () => "failure",
  }));
  assert.match(html, /Выбрать все проекты Sample Auction/);
  assert.match(html, /Fourth-source project/);
  assert.match(html, /Sample Market/);
  const unavailable = renderToStaticMarkup(React.createElement(LaunchDialog, {
    catalog, locale: "ru", options: { ...options, sources: [] }, loading: false, failed: false, retry() {}, close() {},
    launch() { throw new Error("unavailable source must never launch"); }, errorMessage: () => "failure",
  }));
  assert.doesNotMatch(unavailable, /Fourth-source project<\/b><small>Готов к запуску/);
});

function renderApp(t, source, modules) {
  const previous = globalThis.window;
  globalThis.window = { location: { search: `?source=${source}`, pathname: "/" }, localStorage: { getItem: () => null } };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["projects", source], [{ id: 41, source_key: source, name: "Extension project", cars: 0 }]);
  client.setQueryData(["scans", source], []);
  client.setQueryData(["launch-options"], options);
  t.after(() => { client.clear(); if (previous === undefined) delete globalThis.window; else globalThis.window = previous; });
  return { client, html: renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(App, {
    currentUser: { id: 1, username: "fixture", role: "USER", preferred_locale: "ru", must_change_password: false },
    sourceCatalog: catalog, workspaceModules: modules, onSessionChanged() {},
  }))) };
}

test("a new auction reuses the family workspace and source-scoped queries in the actual app", (t) => {
  const { html, client } = renderApp(t, "sampleauction");
  assert.match(html, /Проекты · Sample Auction/);
  assert.match(html, /Extension project/);
  assert.ok(client.getQueryState(["projects", "sampleauction"]));
  assert.match(html, /auction-projects-panel/);
  assert.doesNotMatch(html, /Копировать ссылку|Быстрый|price-filter/);
});

test("a marketplace registers its own workspace without entering the auction composition", (t) => {
  const { html } = renderApp(t, "samplemarket", { "fixture-market": ({ source }) => React.createElement("h1", null, `Marketplace extension ${source}`) });
  assert.match(html, /Marketplace extension samplemarket/);
  assert.doesNotMatch(html, /Выборка лотов|Сохранённые сведения|auction-tabs|projects-toolbar-controls/);
});

test("missing UI extension does not fall back to Encar or an auction", (t) => {
  const { html } = renderApp(t, "samplemarket");
  assert.match(html, /Интерфейс сайта ещё не подключён/);
  assert.doesNotMatch(html, /auction-tabs|projects-toolbar-controls/);
});

test("a source supplies its own listing identity check without weakening shared URL safety", () => {
  const lot = { source_key: "sampleauction", source_listing_id: "41", url: "https://sampleauction.example.test/items/41" };
  const validators = { sampleauction: (url, id) => url.pathname === `/items/${id}` };
  assert.equal(auctionListingLink(lot, catalog, validators), lot.url);
  assert.equal(auctionListingLink(lot, catalog), null, "registration alone does not infer detail URLs");
  for (const url of ["https://sampleauction.example.test/search", "https://sampleauction.example.test/items/42",
    "https://other.example.test/items/41", "http://sampleauction.example.test/items/41",
    "https://user:password@sampleauction.example.test/items/41", "https://sampleauction.example.test:8443/items/41"]) {
    assert.equal(auctionListingLink({ ...lot, url }, catalog, validators), null, url);
  }
});

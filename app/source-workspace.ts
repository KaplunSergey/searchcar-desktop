import { SOURCE_CATALOG, sourceDefinition, type SourceCatalog, type SourceKey } from "./source-catalog";
export type { SourceKey } from "./source-catalog";
export type View = "projects" | "project" | "favorites" | "car" | "scans" | "source-settings" | "settings" | "admin";
export type WorkspaceRoute = { source: SourceKey; view: View; projectId: number | null; carId: number | null };
export type SiteRoutes = Partial<Record<SourceKey, WorkspaceRoute>>;
export const WORKSPACE_STORAGE_KEY = "searchcar-workspace";
const views: View[] = ["projects", "project", "favorites", "car", "scans", "source-settings", "settings", "admin"];
const emptyRoute: WorkspaceRoute = { source: "encar", view: "projects", projectId: null, carId: null };

export function normalizeRoute(value: Partial<WorkspaceRoute> | null, catalog = SOURCE_CATALOG): WorkspaceRoute {
  if (!value || !sourceDefinition(value.source || "encar", catalog)) return { ...emptyRoute };
  const view = views.includes(value.view as View) ? value.view as View : "projects";
  const id = (value: unknown) => Number.isSafeInteger(value) && Number(value) > 0 ? Number(value) : null;
  const route = { source: value.source || "encar", view,
    projectId: ["project", "car"].includes(view) ? id(value.projectId) : null,
    carId: view === "car" ? id(value.carId) : null };
  if (view === "project" && !route.projectId || view === "car" && !route.carId) return { ...emptyRoute, source: route.source };
  return route;
}

export function readWorkspace(search: string, stored: string | null, catalog = SOURCE_CATALOG): WorkspaceRoute {
  const params = new URLSearchParams(search);
  // Explicit legacy URLs belong to Encar, not whichever site was last open.
  if (["source", "view", "project", "car"].some((key) => params.has(key))) {
    return normalizeRoute({ source: (params.get("source") || "encar") as SourceKey,
      view: (params.get("view") || "projects") as View,
      projectId: Number(params.get("project")), carId: Number(params.get("car")) }, catalog);
  }
  try {
    const value = JSON.parse(stored || "null");
    return normalizeRoute(value && Object.hasOwn(value, "current") ? value.current : value, catalog);
  }
  catch { return { ...emptyRoute }; }
}

export function readSiteRoutes(stored: string | null, catalog = SOURCE_CATALOG): SiteRoutes {
  const routes: SiteRoutes = {};
  try {
    const value = JSON.parse(stored || "null");
    const sites = value?.sites;
    for (const { key: source } of catalog) {
      const value = sites?.[source];
      if (value?.source === source && views.includes(value.view) && !["settings", "admin"].includes(value.view)) {
        routes[source] = normalizeRoute(value, catalog);
      }
    }
    const current = value && Object.hasOwn(value, "current") ? value.current : value;
    if (current && sourceDefinition(current.source, catalog) && views.includes(current.view) && !["settings", "admin"].includes(current.view)) {
      routes[current.source] = normalizeRoute(current, catalog);
    }
  } catch { /* Invalid storage must not block navigation. */ }
  return routes;
}

function storedWorkspace(): string | null {
  try { return typeof window === "undefined" ? null : window.localStorage.getItem(WORKSPACE_STORAGE_KEY); }
  catch { return null; }
}

export function initialSiteRoutes(current: WorkspaceRoute, stored?: string | null, catalog: SourceCatalog = SOURCE_CATALOG): SiteRoutes {
  const routes = readSiteRoutes(stored !== undefined ? stored : storedWorkspace(), catalog);
  if (!["settings", "admin"].includes(current.view)) routes[current.source] = current;
  return routes;
}

export function persistWorkspace(current: WorkspaceRoute, sites: SiteRoutes): string {
  if (!["settings", "admin"].includes(current.view)) sites[current.source] = current;
  const snapshot = JSON.stringify({ current, sites });
  try { window.localStorage.setItem(WORKSPACE_STORAGE_KEY, snapshot); }
  catch { /* Keep the in-session routes even when WebView storage is unavailable. */ }
  return snapshot;
}

export function workspaceUrl(pathname: string, route: WorkspaceRoute): string {
  const params = new URLSearchParams({ source: route.source });
  if (route.view !== "projects") params.set("view", route.view);
  if (route.projectId) params.set("project", String(route.projectId));
  if (route.carId) params.set("car", String(route.carId));
  return `${pathname}?${params}`;
}

export function sourceRead(resource: "projects" | "scans" | "favorites", source: SourceKey) {
  return { queryKey: [resource, source], path: `/${resource}?source_key=${source}` };
}

export function initialWorkspace(stored?: string | null, catalog: SourceCatalog = SOURCE_CATALOG): WorkspaceRoute {
  if (typeof window === "undefined") return { ...emptyRoute };
  return readWorkspace(window.location.search, stored !== undefined ? stored : storedWorkspace(), catalog);
}

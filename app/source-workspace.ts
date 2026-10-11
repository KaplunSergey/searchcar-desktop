export type SourceKey = "encar" | "copart" | "iaa";
export type View = "projects" | "project" | "favorites" | "car" | "scans" | "source-settings" | "settings" | "admin";
export type WorkspaceRoute = { source: SourceKey; view: View; projectId: number | null; carId: number | null };
export const WORKSPACE_STORAGE_KEY = "searchcar-workspace";
export const SITE_NAMES: Record<SourceKey, string> = { encar: "Encar", copart: "Copart", iaa: "IAA" };
const views: View[] = ["projects", "project", "favorites", "car", "scans", "source-settings", "settings", "admin"];
const emptyRoute: WorkspaceRoute = { source: "encar", view: "projects", projectId: null, carId: null };

export function normalizeRoute(value: Partial<WorkspaceRoute> | null): WorkspaceRoute {
  if (!value || !Object.hasOwn(SITE_NAMES, value.source || "encar")) return { ...emptyRoute };
  const view = views.includes(value.view as View) ? value.view as View : "projects";
  const id = (value: unknown) => Number.isSafeInteger(value) && Number(value) > 0 ? Number(value) : null;
  const route = { source: value.source || "encar", view,
    projectId: ["project", "car"].includes(view) ? id(value.projectId) : null,
    carId: view === "car" ? id(value.carId) : null };
  if (view === "project" && !route.projectId || view === "car" && !route.carId) return { ...emptyRoute, source: route.source };
  return route;
}

export function readWorkspace(search: string, stored: string | null): WorkspaceRoute {
  const params = new URLSearchParams(search);
  // Explicit legacy URLs belong to Encar, not whichever site was last open.
  if (["source", "view", "project", "car"].some((key) => params.has(key))) {
    return normalizeRoute({ source: (params.get("source") || "encar") as SourceKey,
      view: (params.get("view") || "projects") as View,
      projectId: Number(params.get("project")), carId: Number(params.get("car")) });
  }
  try { return normalizeRoute(JSON.parse(stored || "null")); }
  catch { return { ...emptyRoute }; }
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

export function initialWorkspace(): WorkspaceRoute {
  if (typeof window === "undefined") return { ...emptyRoute };
  let stored: string | null = null;
  try { stored = window.localStorage.getItem(WORKSPACE_STORAGE_KEY); } catch { /* Navigation still works with storage blocked. */ }
  return readWorkspace(window.location.search, stored);
}

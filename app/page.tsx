"use client";

import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { FormEvent, PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { dict, type Key, type Locale } from "./translations";

type View =
  | "projects"
  | "project"
  | "favorites"
  | "car"
  | "scans"
  | "settings"
  | "admin";
type Translate = (key: Key) => string;
type AuthUser = {
  id: number;
  username: string;
  role: "ADMIN" | "USER";
  status: "ACTIVE" | "BLOCKED" | "PENDING_APPROVAL";
  project_limit: number | null;
  project_count: number;
  car_count: number;
  scan_count: number;
  must_change_password: boolean;
  preferred_locale: Locale;
  created_at: string;
  updated_at: string;
  last_login_at?: string | null;
  last_activity_at?: string | null;
};
type AuthResponse = {
  user: AuthUser;
  csrf_token: string;
  registration_enabled?: boolean;
};
type AdminStats = {
  users: number;
  active_users: number;
  pending_users: number;
  blocked_users: number;
  projects: number;
  cars: number;
  scans_30d: number;
  failed_scans_30d: number;
};
type LatestScan = {
  id: number;
  status: string;
  error?: string | null;
  created_at: string;
};
type ProjectRecord = {
  id: number;
  name: string;
  search_url: string;
  telegram_url?: string | null;
  scan_mode: "FAST" | "ACCURATE";
  search_page_mode: "FIRST_PAGE" | "ALL_PAGES";
  auto_update: boolean;
  cars: number;
  updated_at: string;
  latest_scan?: LatestScan | null;
};
type PriceChange = {
  direction: "DOWN" | "UP";
  previous: number;
  current: number;
  difference: number;
  percent: number;
};
type CarRecord = {
  id: number;
  project_id?: number;
  project_name?: string;
  encar_id: string;
  url: string;
  title?: string | null;
  price?: number | null;
  status: string;
  details: Record<string, unknown>;
  favorite: boolean;
  viewed: boolean;
  missing_scans: number;
  image?: string | null;
  first_seen_at?: string | null;
  updated_at?: string | null;
  price_change?: PriceChange | null;
};
type CarDetails = CarRecord & {
  comment?: string | null;
  comment_updated_at?: string | null;
  rating?: number | null;
  images?: Record<string, { path?: string; checksum?: string; url?: string; stored_at?: string }>;
  created_at?: string;
  last_seen_at?: string | null;
};
type ScanReportItem = {
  car_id: number;
  project_id: number;
  project_name?: string | null;
  favorite?: boolean;
  encar_id: string;
  title?: string | null;
  change: string;
  changes?: { field: string; old?: unknown; new?: unknown }[];
  details_unavailable?: boolean;
  old_price?: number | null;
  price?: number | null;
  updated_at: string;
};
const REPORT_CHANGE_TYPES = [
  "NEW",
  "PRICE_DROP",
  "PRICE_INCREASE",
  "MATERIAL_UPDATE",
  "SOLD",
  "NOT_FOUND_IN_SEARCH",
  "UNAVAILABLE",
  "UPDATED",
  "MANUAL_REFRESH",
] as const;
type ScanFailure = {
  scope: string;
  project_id?: number | null;
  project_name?: string | null;
  car_id?: number | null;
  encar_id?: string | null;
  code: string;
  technical?: string | null;
};
type ScanRecord = {
  id: number;
  kind: string;
  status: string;
  progress: number;
  payload?: {
    project_ids?: number[];
    car_ids?: number[];
    report?: ScanReportItem[];
    failures?: ScanFailure[];
    summary?: { changed: number; new: number; price_changes: number; failed: number };
    current_project_id?: number | null;
    project_statuses?: Record<string, string>;
    invalidated_report_count?: number;
    pagination?: Record<
      string,
      {
        mode: "FIRST_PAGE" | "ALL_PAGES";
        current_page: number;
        total_pages: number;
        pages_visited: number;
        found_count: number;
        total_results?: number | null;
        complete: boolean;
      }
    >;
  };
  error?: string | null;
  created_at: string;
};
type SchedulerRecord = {
  enabled: boolean;
  paused: boolean;
  catch_up_enabled: boolean;
  interval_minutes: 60 | 180 | 360 | 720 | 1440;
  project_ids: number[];
  next_run_at?: string | null;
};
type CarLookupResult = {
  found: boolean;
  reason?: "invalid_encar_url" | "not_in_database";
  encar_id?: string;
  project_id?: number | null;
  projects?: { id: number; name: string }[];
  car?: CarRecord & { excluded?: boolean };
};
type ImportSummary = {
  available: boolean;
  path: string;
  searches: number;
  history_files: number;
  records: number;
  unique_cars: number;
  screenshots: number;
  projects_created?: number;
  cars_created?: number;
  relations_created?: number;
  snapshots_created?: number;
  images_copied?: number;
  skipped_existing?: number;
};
type DesktopBackup = {
  name: string;
  bytes: number;
  updated_at: string;
};
type DesktopDataStatus = {
  data_directory: string;
  backups: DesktopBackup[];
  restore_result?: { status: string; error?: string } | null;
};
type DesktopMigrationReport = {
  status: "converted";
  backup: { path: string; files: number; bytes: number };
  source_counts: Record<string, number>;
  target_counts: Record<string, number>;
  storage_files: number;
};
type ProjectForm = {
  name: string;
  search_url: string;
  telegram_url: string;
  scan_mode: "FAST" | "ACCURATE";
  search_page_mode: "FIRST_PAGE" | "ALL_PAGES";
  auto_update: boolean;
};

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});
const api = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const apiOrigin = api.replace(/\/api\/?$/, "");
const csrfCookieName = process.env.NEXT_PUBLIC_CSRF_COOKIE_NAME || "encar_csrf";
let csrfToken = "";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function assetUrl(path?: string | null) {
  if (!path) return "";
  return path.startsWith("http") ? path : `${apiOrigin}${path}`;
}

function cookieValue(name: string) {
  if (typeof document === "undefined") return "";
  const prefix = `${encodeURIComponent(name)}=`;
  const cookie = document.cookie
    .split("; ")
    .find((value) => value.startsWith(prefix));
  return cookie ? decodeURIComponent(cookie.slice(prefix.length)) : "";
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method || "GET").toUpperCase();
  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(method);
  const currentCsrfToken = cookieValue(csrfCookieName) || csrfToken;
  const response = await fetch(`${api}${path}`, {
    ...options,
    credentials: "include",
    headers: {
      ...(options?.body ? { "content-type": "application/json" } : {}),
      ...(unsafe && currentCsrfToken ? { "x-csrf-token": currentCsrfToken } : {}),
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const message = await response.text();
    throw new ApiError(response.status, message || `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function openExternalUrl(url: string) {
  try {
    await request("/desktop/open-external", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }
}

function useProjects() {
  return useQuery<ProjectRecord[]>({
    queryKey: ["projects"],
    queryFn: () => request("/projects"),
    refetchInterval: 5000,
  });
}

function formatDate(value: string | null | undefined, locale: Locale) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(locale === "uk" ? "uk-UA" : "ru-RU", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatMoney(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? `₩ ${number.toLocaleString("ru-RU")}` : "—";
}

function formatCountdown(value: string | null | undefined, locale: Locale) {
  if (!value) return "—";
  const seconds = Math.max(0, Math.floor((new Date(value).getTime() - Date.now()) / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = seconds % 60;
  const parts = [
    hours ? `${hours} ${locale === "uk" ? "год" : "ч"}` : "",
    `${String(minutes).padStart(2, "0")} ${locale === "uk" ? "хв" : "мин"}`,
    `${String(rest).padStart(2, "0")} ${locale === "uk" ? "с" : "с"}`,
  ];
  return parts.filter(Boolean).join(" ");
}

function scanStatusKey(status: string): Key {
  const keys: Record<string, Key> = {
    QUEUED: "queued",
    RUNNING: "running",
    CANCEL_REQUESTED: "cancelling",
    CANCELLED: "cancelled",
    INTERRUPTED: "interrupted",
    SUCCEEDED: "success",
    PARTIAL: "partial",
    FAILED: "failed",
    CAPTCHA: "captcha",
    UPDATED: "updated",
  };
  return keys[status] || "failed";
}

function carStatusText(status: string, t: Translate) {
  const keys: Record<string, Key> = {
    NEW: "new",
    UPDATED: "updated",
    PRICE_DROP: "priceDropped",
    PRICE_INCREASE: "increase",
    MATERIAL_UPDATE: "materialUpdate",
    NOT_FOUND_IN_SEARCH: "notFound",
    RELISTED: "relisted",
    UNAVAILABLE: "unavailable",
    REMOVED_FROM_SEARCH: "removedFromSearch",
    SOLD: "sold",
    MANUAL_REFRESH: "manualRefresh",
  };
  return keys[status] ? t(keys[status]) : t("updated");
}

function App({
  currentUser,
  onSessionChanged,
}: {
  currentUser: AuthUser;
  onSessionChanged: () => void;
}) {
  const client = useQueryClient();
  const [locale, setLocale] = useState<Locale>(currentUser.preferred_locale || "ru");
  const [view, setView] = useState<View>("projects");
  const [toast, setToast] = useState("");
  const [activeProjectId, setActiveProjectId] = useState<number | null>(null);
  const [activeCarId, setActiveCarId] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [formProject, setFormProject] = useState<ProjectRecord | "new" | null>(null);
  const [deleteProject, setDeleteProject] = useState<ProjectRecord | null>(null);
  const [showPassword, setShowPassword] = useState(currentUser.must_change_password);
  const t: Translate = (key) => dict[locale][key];
  const localeMutation = useMutation({
    mutationFn: (preferredLocale: Locale) =>
      request<AuthUser>("/auth/profile", {
        method: "PATCH",
        body: JSON.stringify({ preferred_locale: preferredLocale }),
      }),
    onMutate: (preferredLocale) => {
      const previousLocale = locale;
      setLocale(preferredLocale);
      return { previousLocale };
    },
    onError: (_error, _preferredLocale, context) => {
      const previousLocale = context?.previousLocale || currentUser.preferred_locale || "ru";
      setLocale(previousLocale);
      notify(dict[previousLocale].actionFailed);
    },
  });
  const projectsQuery = useProjects();
  const scansQuery = useQuery<ScanRecord[]>({
    queryKey: ["scans"],
    queryFn: () => request("/scans"),
    refetchInterval: 2000,
  });
  const schedulerQuery = useQuery<SchedulerRecord>({
    queryKey: ["scheduler"],
    queryFn: () => request("/settings"),
    refetchInterval: 5000,
  });
  const [, tickCountdown] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => tickCountdown((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const activeScans = (scansQuery.data || []).filter((scan) =>
    ["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(scan.status),
  );

  useEffect(() => {
    window.history.replaceState(
      { view: "projects", projectId: null, carId: null },
      "",
      window.location.pathname,
    );
    const onPopState = (event: PopStateEvent) => {
      const state = event.state || {};
      setView(state.view || "projects");
      setActiveProjectId(state.projectId || null);
      setActiveCarId(state.carId || null);
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const navigate = (next: View, projectId?: number | null, carId?: number | null) => {
    const state = {
      view: next,
      projectId:
        projectId !== undefined
          ? projectId
          : next === "project" || next === "car"
            ? activeProjectId
            : null,
      carId: carId !== undefined ? carId : next === "car" ? activeCarId : null,
    };
    const params = new URLSearchParams();
    if (next !== "projects") params.set("view", next);
    if (state.projectId) params.set("project", String(state.projectId));
    if (state.carId) params.set("car", String(state.carId));
    window.history.pushState(state, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
    setView(next);
    setActiveProjectId(state.projectId);
    setActiveCarId(state.carId);
  };

  const notify = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 4500);
  };

  const refreshMutation = useMutation({
    mutationFn: (projectIds: number[]) =>
      request<{ id: number }>("/scans/projects", {
        method: "POST",
        body: JSON.stringify({ project_ids: projectIds }),
      }),
    onSuccess: () => {
      notify(t("scanQueued"));
      void client.invalidateQueries({ queryKey: ["scans"] });
    },
    onError: () => notify(t("scanAlreadyActive")),
  });
  const cancelScanMutation = useMutation({
    mutationFn: (scanId: number) =>
      request<{ id: number; status: string }>(`/scans/${scanId}/cancel`, {
        method: "POST",
      }),
    onSuccess: () => {
      notify(t("scanCancelRequested"));
      void client.invalidateQueries({ queryKey: ["scans"] });
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: () => notify(t("actionFailed")),
  });
  const cancelScan = (scanId: number) => {
    if (window.confirm(t("cancelUpdateConfirm"))) {
      cancelScanMutation.mutate(scanId);
    }
  };

  const saveProjectMutation = useMutation({
    mutationFn: ({ form, id }: { form: ProjectForm; id?: number }) =>
      request<ProjectRecord>(id ? `/projects/${id}` : "/projects", {
        method: id ? "PATCH" : "POST",
        body: JSON.stringify(form),
      }),
    onSuccess: (_, variables) => {
      notify(variables.id ? t("projectEdited") : t("projectCreated"));
      setFormProject(null);
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) =>
      notify(
        error instanceof ApiError && error.message.includes("project_limit_reached")
          ? locale === "uk"
            ? "Досягнуто ліміт проєктів для вашого облікового запису"
            : "Достигнут лимит проектов для вашей учётной записи"
          : t("projectConflict"),
      ),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => request<void>(`/projects/${id}`, { method: "DELETE" }),
    onSuccess: (_, id) => {
      setDeleteProject(null);
      setSelected((values) => values.filter((value) => value !== id));
      if (activeProjectId === id) {
        navigate("projects");
      }
      notify(t("projectDeleted"));
      void client.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: () => notify(t("deleteFailed")),
  });

  const openProject = (project: ProjectRecord) => {
    navigate("project", project.id, null);
  };
  const openCar = (car: CarRecord) => {
    navigate("car", activeProjectId, car.id);
  };
  const openLookupCar = (result: CarLookupResult) => {
    if (result.car) navigate("car", result.project_id ?? null, result.car.id);
  };

  return (
    <div className="app-shell">
      <aside>
        <button className="brand" onClick={() => navigate("projects")}>
          <span>S</span> SearchCar
        </button>
        <p className="eyebrow">{t("workspace")}</p>
        <nav>
          <button
            className={["projects", "project", "favorites", "car"].includes(view) ? "active" : ""}
            onClick={() => navigate("projects")}
          >
            ▣ {t("projects")}
          </button>
          <button className={view === "scans" ? "active" : ""} onClick={() => navigate("scans")}>
            ▷ {t("scans")}
          </button>
          <button
            className={view === "settings" ? "active" : ""}
            onClick={() => navigate("settings")}
          >
            ⚙ {t("settings")}
          </button>
          {currentUser.role === "ADMIN" ? (
            <button
              className={view === "admin" ? "active" : ""}
              onClick={() => navigate("admin")}
            >
              ◈ {locale === "uk" ? "Адмінка" : "Админка"}
            </button>
          ) : null}
        </nav>
        <div className="worker">
          <span className={`dot ${projectsQuery.isError ? "offline" : ""}`} />
          <div>
            <small>{t("backgroundWorker")}</small>
            <strong>
              {projectsQuery.isError
                ? t("backendUnavailable")
                : activeScans.length
                  ? t("scanInProgress")
                  : t("workerReady")}
            </strong>
          </div>
        </div>
      </aside>
      <section className="workspace">
        <header>
          <div>
            {t("workspace")}　/　{view === "admin"
              ? locale === "uk" ? "Адміністрування" : "Администрирование"
              : t(view === "car" ? "car" : view === "favorites" ? "favorites" : view === "settings" ? "settings" : view === "scans" ? "scans" : "projects")}
          </div>
          <div className="top-actions">
            {schedulerQuery.data?.enabled && schedulerQuery.data.next_run_at ? (
              <span className="next-update">
                {schedulerQuery.data.paused ? t("schedulerTitle") : t("nextUpdateIn")}
                <b>
                  {schedulerQuery.data.paused
                    ? t("schedulerPaused")
                    : formatCountdown(schedulerQuery.data.next_run_at, locale)}
                </b>
              </span>
            ) : null}
            <span>
              {t("activeScans")}
              <b>{activeScans.length}</b>
            </span>
            <span className="current-user">
              {locale === "uk" ? "Користувач" : "Пользователь"}:
              <b>{currentUser.username}</b>
            </span>
            <button
              onClick={() => setShowPassword(true)}
              title={locale === "uk" ? "Змінити пароль" : "Изменить пароль"}
            >
              ◉
            </button>
            <button
              disabled={localeMutation.isPending}
              onClick={() => localeMutation.mutate("ru")}
              className={locale === "ru" ? "on" : ""}
            >
              RU
            </button>
            <button
              disabled={localeMutation.isPending}
              onClick={() => localeMutation.mutate("uk")}
              className={locale === "uk" ? "on" : ""}
            >
              UA
            </button>
            <button
              onClick={async () => {
                await request<void>("/auth/logout", { method: "POST" });
                csrfToken = "";
                client.clear();
                onSessionChanged();
              }}
              title={locale === "uk" ? "Вийти" : "Выйти"}
            >
              ↪
            </button>
          </div>
        </header>
        <main>
          {view === "projects" && (
            <Projects
              t={t}
              locale={locale}
              projects={projectsQuery.data || []}
              loading={projectsQuery.isLoading}
              error={projectsQuery.isError}
              selected={selected}
              setSelected={setSelected}
              activeScans={activeScans}
              latestReport={(scansQuery.data || []).find(
                (scan) =>
                  scan.payload?.project_ids?.length &&
                  ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "INTERRUPTED"].includes(scan.status),
              )}
              refresh={(ids) => refreshMutation.mutate(ids)}
              cancelScan={cancelScan}
              open={openProject}
              create={() => setFormProject("new")}
              edit={setFormProject}
              remove={setDeleteProject}
              notify={notify}
              openLookupCar={openLookupCar}
              openReportCar={(item) => navigate("car", item.project_id, item.car_id)}
              openFavorites={() => navigate("favorites")}
            />
          )}
          {view === "favorites" && (
            <Favorites
              t={t}
              back={() => window.history.back()}
              openCar={(car) => {
                if (car.project_id) navigate("car", car.project_id, car.id);
              }}
              notify={notify}
            />
          )}
          {view === "project" && activeProjectId && (
            <Project
              t={t}
              locale={locale}
              projectId={activeProjectId}
              back={() => window.history.back()}
              openCar={openCar}
              refresh={() => refreshMutation.mutate([activeProjectId])}
              activeScan={activeScans.find((scan) =>
                scan.payload?.project_ids?.includes(activeProjectId),
              )}
              cancelScan={cancelScan}
              edit={(project) => setFormProject(project)}
              remove={setDeleteProject}
              notify={notify}
            />
          )}
          {view === "car" && activeCarId && (
            <Car
              t={t}
              locale={locale}
              carId={activeCarId}
              projectId={activeProjectId}
              activeScan={activeScans.find((scan) =>
                scan.payload?.car_ids?.includes(activeCarId),
              )}
              cancelScan={cancelScan}
              back={() => window.history.back()}
              notify={notify}
            />
          )}
          {view === "scans" && (
            <Scans
              t={t}
              locale={locale}
              scans={scansQuery.data || []}
              openCar={(item) => navigate("car", item.project_id, item.car_id)}
              cancelScan={cancelScan}
            />
          )}
          {view === "settings" && (
            <Settings
              t={t}
              locale={locale}
              projects={projectsQuery.data || []}
              notify={notify}
              isAdmin={currentUser.role === "ADMIN"}
            />
          )}
          {view === "admin" && currentUser.role === "ADMIN" && (
            <Admin
              locale={locale}
              notify={notify}
              currentUserId={currentUser.id}
            />
          )}
        </main>
      </section>
      {formProject && (
        <ProjectModal
          t={t}
          project={formProject === "new" ? undefined : formProject}
          busy={saveProjectMutation.isPending}
          close={() => setFormProject(null)}
          save={(form) =>
            saveProjectMutation.mutate({
              form,
              id: formProject === "new" ? undefined : formProject.id,
            })
          }
        />
      )}
      {deleteProject && (
        <ConfirmModal
          t={t}
          project={deleteProject}
          busy={deleteMutation.isPending}
          close={() => setDeleteProject(null)}
          confirm={() => deleteMutation.mutate(deleteProject.id)}
        />
      )}
      {toast && (
        <div className="toast" role="status">
          <span>✓</span>
          {toast}
          <small>{t("justNow")}</small>
        </div>
      )}
      {showPassword ? (
        <PasswordModal
          locale={locale}
          required={currentUser.must_change_password}
          close={() => {
            if (!currentUser.must_change_password) setShowPassword(false);
          }}
          saved={() => {
            setShowPassword(false);
            onSessionChanged();
          }}
        />
      ) : null}
    </div>
  );
}

export default function Page() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthRoot />
    </QueryClientProvider>
  );
}

function AuthRoot() {
  const client = useQueryClient();
  const [version, setVersion] = useState(0);
  const authQuery = useQuery<AuthResponse>({
    queryKey: ["auth", version],
    queryFn: async () => {
      const result = await request<AuthResponse>("/auth/me");
      csrfToken = result.csrf_token;
      return result;
    },
    retry: false,
  });
  const refresh = () => {
    void client.invalidateQueries();
    setVersion((value) => value + 1);
  };
  if (authQuery.isLoading) {
    return (
      <div className="auth-page">
        <div className="auth-card auth-loading">
          <span className="auth-logo">E</span>
          <b>SearchCar</b>
        </div>
      </div>
    );
  }
  if (!authQuery.data) {
    return <LoginScreen retry={refresh} />;
  }
  return (
    <App
      currentUser={authQuery.data.user}
      onSessionChanged={refresh}
    />
  );
}

function LoginScreen({ retry }: { retry: () => void }) {
  const [locale, setLocale] = useState<Locale>("ru");
  const [username, setUsername] = useState("Serhii");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () =>
      request<AuthResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      }),
    onSuccess: (result) => {
      csrfToken = result.csrf_token;
      retry();
    },
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate();
  };
  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-heading">
          <span className="auth-logo">E</span>
          <div>
            <h1>SearchCar</h1>
            <p>
              {locale === "uk"
                ? "Увійдіть у свій робочий простір"
                : "Войдите в своё рабочее пространство"}
            </p>
          </div>
          <div className="auth-locales">
            <button type="button" className={locale === "ru" ? "on" : ""} onClick={() => setLocale("ru")}>RU</button>
            <button type="button" className={locale === "uk" ? "on" : ""} onClick={() => setLocale("uk")}>UA</button>
          </div>
        </div>
        <label>
          {locale === "uk" ? "Логін" : "Логин"}
          <input
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          {locale === "uk" ? "Пароль" : "Пароль"}
          <input
            type="password"
            autoComplete="current-password"
            required
            minLength={8}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {login.isError ? (
          <p className="auth-error">
            {locale === "uk"
              ? "Невірний логін або пароль. Перевірте дані та спробуйте ще раз."
              : "Неверный логин или пароль. Проверьте данные и попробуйте ещё раз."}
          </p>
        ) : null}
        <Button kind="primary" type="submit" disabled={login.isPending}>
          {login.isPending
            ? locale === "uk" ? "Вхід…" : "Вход…"
            : locale === "uk" ? "Увійти" : "Войти"}
        </Button>
        <small>
          {locale === "uk"
            ? "Нові облікові записи створює адміністратор."
            : "Новые учётные записи создаёт администратор."}
        </small>
      </form>
    </div>
  );
}

function PasswordModal({
  locale,
  required,
  close,
  saved,
}: {
  locale: Locale;
  required: boolean;
  close: () => void;
  saved: () => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      request("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }),
    onSuccess: saved,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmation) return;
    mutation.mutate();
  };
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  return (
    <div className="modal-backdrop auth-modal" role="dialog" aria-modal="true">
      <form className="modal password-modal" onSubmit={submit}>
        <div className="modal-head">
          <div>
            <h2>{l("Изменить пароль", "Змінити пароль")}</h2>
            {required ? (
              <p>{l("Администратор потребовал установить новый пароль.", "Адміністратор вимагає встановити новий пароль.")}</p>
            ) : null}
          </div>
          {!required ? <button type="button" onClick={close}>×</button> : null}
        </div>
        <label>
          {l("Текущий пароль", "Поточний пароль")}
          <input type="password" autoComplete="current-password" required minLength={8} value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
        </label>
        <label>
          {l("Новый пароль", "Новий пароль")}
          <input type="password" autoComplete="new-password" required minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} />
        </label>
        <label>
          {l("Повторите новый пароль", "Повторіть новий пароль")}
          <input type="password" autoComplete="new-password" required minLength={8} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} />
        </label>
        {confirmation && confirmation !== newPassword ? <p className="auth-error">{l("Пароли не совпадают.", "Паролі не збігаються.")}</p> : null}
        {mutation.isError ? <p className="auth-error">{l("Не удалось изменить пароль. Проверьте текущий пароль.", "Не вдалося змінити пароль. Перевірте поточний пароль.")}</p> : null}
        <div className="modal-actions">
          {!required ? <Button onClick={close}>{l("Отмена", "Скасувати")}</Button> : null}
          <Button kind="primary" type="submit" disabled={mutation.isPending || newPassword !== confirmation}>
            {l("Сохранить пароль", "Зберегти пароль")}
          </Button>
        </div>
      </form>
    </div>
  );
}

function Admin({
  locale,
  notify,
  currentUserId,
}: {
  locale: Locale;
  notify: (message: string) => void;
  currentUserId: number;
}) {
  const client = useQueryClient();
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const usersQuery = useQuery<AuthUser[]>({
    queryKey: ["admin-users"],
    queryFn: () => request("/admin/users"),
  });
  const statsQuery = useQuery<AdminStats>({
    queryKey: ["admin-stats"],
    queryFn: () => request("/admin/stats"),
  });
  const [showCreate, setShowCreate] = useState(false);
  const [deleteUser, setDeleteUser] = useState<AuthUser | null>(null);
  const [draft, setDraft] = useState({
    username: "",
    password: "",
    role: "USER" as "ADMIN" | "USER",
    status: "ACTIVE" as AuthUser["status"],
    project_limit: 1 as number | null,
    must_change_password: true,
  });
  const resetDraft = () =>
    setDraft({
      username: "",
      password: "",
      role: "USER",
      status: "ACTIVE",
      project_limit: 1,
      must_change_password: true,
    });
  const closeCreate = () => {
    setShowCreate(false);
    resetDraft();
  };
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["admin-users"] });
    void client.invalidateQueries({ queryKey: ["admin-stats"] });
  };
  const create = useMutation({
    mutationFn: () =>
      request<AuthUser>("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          ...draft,
          project_limit: draft.role === "ADMIN" ? null : draft.project_limit,
        }),
      }),
    onSuccess: () => {
      closeCreate();
      notify(l("Пользователь создан", "Користувача створено"));
      refresh();
    },
    onError: () => notify(l("Не удалось создать пользователя", "Не вдалося створити користувача")),
  });
  const patch = useMutation({
    mutationFn: ({ id, changes }: { id: number; changes: Record<string, unknown> }) =>
      request<AuthUser>(`/admin/users/${id}`, {
        method: "PATCH",
        body: JSON.stringify(changes),
      }),
    onSuccess: () => {
      notify(l("Изменения сохранены", "Зміни збережено"));
      refresh();
    },
    onError: () => notify(l("Не удалось сохранить изменения", "Не вдалося зберегти зміни")),
  });
  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      request(`/admin/users/${id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ password, must_change_password: true }),
      }),
    onSuccess: () => {
      notify(l("Пароль сброшен", "Пароль скинуто"));
      refresh();
    },
    onError: () => notify(l("Не удалось сбросить пароль", "Не вдалося скинути пароль")),
  });
  const remove = useMutation({
    mutationFn: (id: number) =>
      request<void>(`/admin/users/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      setDeleteUser(null);
      notify(l("Пользователь удалён", "Користувача видалено"));
      refresh();
    },
    onError: (error) => {
      const activeScan =
        error instanceof ApiError && error.message.includes("user_has_active_scans");
      notify(
        activeScan
          ? l(
              "Сначала дождитесь завершения обновлений этого пользователя",
              "Спочатку дочекайтеся завершення оновлень цього користувача",
            )
          : l("Не удалось удалить пользователя", "Не вдалося видалити користувача"),
      );
    },
  });
  const stats = statsQuery.data;
  return (
    <>
      <div className="title-row">
        <div>
          <h1>{l("Администрирование", "Адміністрування")}</h1>
          <p>{l("Пользователи, ограничения и статистика системы", "Користувачі, обмеження та статистика системи")}</p>
        </div>
        <Button kind="primary" onClick={() => { resetDraft(); setShowCreate(true); }}>＋ {l("Создать пользователя", "Створити користувача")}</Button>
      </div>
      {stats ? (
        <section className="admin-stats">
          <Stat label={l("Пользователи", "Користувачі")} value={stats.users} note={`${stats.active_users} ${l("активных", "активних")}`} />
          <Stat label={l("Ожидают одобрения", "Очікують схвалення")} value={stats.pending_users} />
          <Stat label={l("Проекты", "Проєкти")} value={stats.projects} />
          <Stat label={l("Уникальные авто", "Унікальні авто")} value={stats.cars} />
          <Stat label={l("Обновления за 30 дней", "Оновлення за 30 днів")} value={stats.scans_30d} note={`${stats.failed_scans_30d} ${l("с ошибкой", "з помилкою")}`} />
        </section>
      ) : null}
      <section className="panel admin-users">
        <div className="admin-user-head">
          <span>{l("Пользователь", "Користувач")}</span>
          <span>{l("Роль и статус", "Роль і статус")}</span>
          <span>{l("Лимит проектов", "Ліміт проєктів")}</span>
          <span>{l("Использование", "Використання")}</span>
          <span>{l("Последняя активность", "Остання активність")}</span>
          <span>{l("Действия", "Дії")}</span>
        </div>
        {usersQuery.isLoading ? <div className="empty-state">{l("Загрузка…", "Завантаження…")}</div> : null}
        {(usersQuery.data || []).map((user) => (
          <article className="admin-user-row" key={user.id}>
            <div><b>{user.username}</b><small>#{user.id}</small></div>
            <div className="admin-selects">
              <select value={user.role} onChange={(event) => patch.mutate({ id: user.id, changes: { role: event.target.value } })}>
                <option value="USER">{l("Пользователь", "Користувач")}</option>
                <option value="ADMIN">{l("Администратор", "Адміністратор")}</option>
              </select>
              <select value={user.status} onChange={(event) => patch.mutate({ id: user.id, changes: { status: event.target.value } })}>
                <option value="ACTIVE">{l("Активен", "Активний")}</option>
                <option value="PENDING_APPROVAL">{l("Ожидает одобрения", "Очікує схвалення")}</option>
                <option value="BLOCKED">{l("Заблокирован", "Заблокований")}</option>
              </select>
            </div>
            <div>
              {user.role === "ADMIN" ? (
                <span>∞</span>
              ) : (
                <input
                  className="limit-input"
                  type="number"
                  min={0}
                  defaultValue={user.project_limit ?? ""}
                  placeholder="∞"
                  onBlur={(event) => patch.mutate({
                    id: user.id,
                    changes: { project_limit: event.target.value === "" ? null : Number(event.target.value) },
                  })}
                />
              )}
            </div>
            <div><b>{user.project_count} {l("проекта", "проєкти")}</b><small>{user.car_count} {l("авто", "авто")} · {user.scan_count} {l("запусков", "запусків")}</small></div>
            <div><span>{formatDate(user.last_activity_at || user.last_login_at, locale)}</span><small>{l("Создан", "Створено")}: {formatDate(user.created_at, locale)}</small></div>
            <div className="admin-actions">
              <label title={l("Потребовать смену пароля", "Вимагати зміну пароля")}>
                <input type="checkbox" checked={user.must_change_password} onChange={(event) => patch.mutate({ id: user.id, changes: { must_change_password: event.target.checked } })} />
                {l("Смена", "Зміна")}
              </label>
              <button onClick={() => {
                const password = window.prompt(l("Новый временный пароль (минимум 8 символов)", "Новий тимчасовий пароль (мінімум 8 символів)"));
                if (password && password.length >= 8) resetPassword.mutate({ id: user.id, password });
              }}>{l("Сбросить пароль", "Скинути пароль")}</button>
              <button
                className="delete-user"
                disabled={user.id === currentUserId}
                title={
                  user.id === currentUserId
                    ? l(
                        "Нельзя удалить свою учётную запись",
                        "Не можна видалити власний обліковий запис",
                      )
                    : l("Удалить пользователя", "Видалити користувача")
                }
                onClick={() => setDeleteUser(user)}
              >
                {l("Удалить", "Видалити")}
              </button>
            </div>
          </article>
        ))}
      </section>
      {showCreate ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <form className="modal admin-create-modal" autoComplete="off" onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
            <div className="modal-head"><h2>{l("Новый пользователь", "Новий користувач")}</h2><button type="button" onClick={closeCreate}>×</button></div>
            <label>{l("Логин", "Логін")}<input name="new-account-login" autoComplete="off" data-1p-ignore required minLength={3} value={draft.username} onChange={(event) => setDraft({ ...draft, username: event.target.value })} /></label>
            <label>{l("Временный пароль", "Тимчасовий пароль")}<input name="new-account-password" type="password" autoComplete="new-password" data-1p-ignore required minLength={8} value={draft.password} onChange={(event) => setDraft({ ...draft, password: event.target.value })} /></label>
            <label>{l("Роль", "Роль")}<select value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value as "ADMIN" | "USER" })}><option value="USER">{l("Пользователь", "Користувач")}</option><option value="ADMIN">{l("Администратор", "Адміністратор")}</option></select></label>
            {draft.role === "USER" ? <label>{l("Лимит проектов", "Ліміт проєктів")}<input type="number" min={0} value={draft.project_limit ?? ""} onChange={(event) => setDraft({ ...draft, project_limit: event.target.value === "" ? null : Number(event.target.value) })} /></label> : null}
            <label className="checkbox-label"><input type="checkbox" checked={draft.must_change_password} onChange={(event) => setDraft({ ...draft, must_change_password: event.target.checked })} />{l("Потребовать смену пароля при входе", "Вимагати зміну пароля під час входу")}</label>
            <div className="modal-actions"><Button onClick={closeCreate}>{l("Отмена", "Скасувати")}</Button><Button kind="primary" type="submit" disabled={create.isPending}>{l("Создать", "Створити")}</Button></div>
          </form>
        </div>
      ) : null}
      {deleteUser ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <section className="modal confirm-modal">
            <h2>{l("Удалить пользователя", "Видалити користувача")}</h2>
            <p>
              {l("Удалить учётную запись", "Видалити обліковий запис")}{" "}
              <b>{deleteUser.username}</b>?
            </p>
            <p className="warning-text">
              {l(
                "Будут навсегда удалены его проекты, настройки, запуски и личные данные. Общие записи автомобилей сохранятся.",
                "Його проєкти, налаштування, запуски та особисті дані буде видалено назавжди. Спільні записи автомобілів збережуться.",
              )}
            </p>
            <div className="modal-actions">
              <Button
                disabled={remove.isPending}
                onClick={() => setDeleteUser(null)}
              >
                {l("Отмена", "Скасувати")}
              </Button>
              <Button
                kind="danger"
                disabled={remove.isPending}
                onClick={() => remove.mutate(deleteUser.id)}
              >
                {remove.isPending
                  ? l("Удаление…", "Видалення…")
                  : l("Удалить навсегда", "Видалити назавжди")}
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function Button({
  children,
  kind = "",
  onClick,
  disabled,
  type = "button",
}: {
  children: React.ReactNode;
  kind?: string;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button className={`button ${kind}`} onClick={onClick} disabled={disabled} type={type}>
      {children}
    </button>
  );
}

function Stat({
  label,
  value,
  note,
  tone = "",
  onClick,
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: string;
  onClick?: () => void;
}) {
  const content = (
    <>
      <small>{label}</small>
      <b>{value}</b>
      {note && <span className={tone}>{note}</span>}
    </>
  );
  return onClick ? (
    <button className="stat stat-button" onClick={onClick}>
      {content}
    </button>
  ) : (
    <article className="stat">{content}</article>
  );
}

function PriceMovement({ change, t }: { change?: PriceChange | null; t: Translate }) {
  if (!change) return <span className="price-movement neutral">{t("noChange")}</span>;
  return (
    <span className={`price-movement ${change.direction.toLowerCase()}`}>
      {change.direction === "DOWN" ? "↓" : "↑"} {change.percent.toLocaleString("ru-RU", {
        maximumFractionDigits: 2,
      })}% {t(change.direction === "DOWN" ? "priceDown" : "priceUp")}
    </span>
  );
}

function carCondition(summary: unknown, t: Translate) {
  const value = String(summary || "UNVERIFIED");
  if (value === "ACCIDENT_VEHICLE") {
    return { tone: "warning", label: t("damageDetected") };
  }
  if (value === "INSURANCE_CLAIM") {
    return { tone: "warning", label: t("insuranceDetected") };
  }
  if (value === "NO_PROBLEMS_STATED") {
    return { tone: "clear", label: t("damageNotDetected") };
  }
  return { tone: "unknown", label: t("conditionUnknown") };
}

function reportPriority(item: ScanReportItem) {
  if (item.change === "NEW") return 0;
  if (item.favorite) return 1;
  if (["PRICE_DROP", "PRICE_INCREASE"].includes(item.change)) return 2;
  if (item.change === "MATERIAL_UPDATE") return 3;
  return 4;
}

function changeTone(change: string) {
  if (change === "NEW") return "new";
  if (change === "PRICE_DROP") return "price-drop";
  if (change === "PRICE_INCREASE") return "price-increase";
  if (change === "MATERIAL_UPDATE") return "material";
  return "other";
}

function changeFieldLabel(field: string, locale: Locale) {
  const labels: Record<string, [string, string]> = {
    price: ["Цена", "Ціна"],
    status: ["Статус", "Статус"],
    title: ["Название", "Назва"],
    trim: ["Комплектация", "Комплектація"],
    year_month: ["Дата производства", "Дата виробництва"],
    mileage_km: ["Пробег", "Пробіг"],
    fuel: ["Топливо", "Пальне"],
    drivetrain: ["Привод", "Привід"],
    transmission: ["Коробка передач", "Коробка передач"],
    engine_displacement_cc: ["Объём двигателя", "Обʼєм двигуна"],
    body_type: ["Тип кузова", "Тип кузова"],
    exterior_color: ["Цвет кузова", "Колір кузова"],
    interior_color: ["Цвет салона", "Колір салону"],
    registration_number: ["Регистрационный номер", "Реєстраційний номер"],
    condition_summary: ["Состояние автомобиля", "Стан автомобіля"],
    new_car_price_percent: ["Стоимость относительно новой", "Вартість відносно нового"],
    condition: ["Данные состояния", "Дані стану"],
    options: ["Опции", "Опції"],
    under_contract: ["Статус цены", "Статус ціни"],
  };
  return labels[field]?.[locale === "uk" ? 1 : 0] || field;
}

function changeValue(value: unknown, field: string, locale: Locale) {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "price") return formatMoney(value);
  if (field === "under_contract") {
    return value
      ? locale === "uk" ? "За контрактом" : "По контракту"
      : locale === "uk" ? "Без контракту" : "Без контракта";
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ReportPrice({ item }: { item: ScanReportItem }) {
  const percent = item.old_price && item.price
    ? ((item.price - item.old_price) / item.old_price) * 100
    : 0;
  return (
    <span className="report-price">
      <b>{formatMoney(item.price)}</b>
      {item.change === "PRICE_DROP" && percent < 0 ? (
        <small className="down">↓ {Math.abs(percent).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%</small>
      ) : item.change === "PRICE_INCREASE" && percent > 0 ? (
        <small className="up">↑ {percent.toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%</small>
      ) : null}
    </span>
  );
}

function ReportList({
  items,
  t,
  locale,
  openCar,
}: {
  items: ScanReportItem[];
  t: Translate;
  locale: Locale;
  openCar: (item: ScanReportItem) => void;
}) {
  const availableChanges = useMemo(() => {
    const present = new Set(items.map((item) => item.change));
    const known = REPORT_CHANGE_TYPES.filter((change) => present.has(change));
    const unknown = [...present].filter(
      (change) => !REPORT_CHANGE_TYPES.includes(change as typeof REPORT_CHANGE_TYPES[number]),
    );
    return [...known, ...unknown];
  }, [items]);
  const [excludedChanges, setExcludedChanges] = useState<Set<string>>(
    () => new Set(),
  );
  const selectedChanges = availableChanges.filter(
    (change) => !excludedChanges.has(change),
  );
  const effectiveSelectedChanges = selectedChanges.length
    ? new Set(selectedChanges)
    : new Set(availableChanges);
  const sorted = items.filter((item) => effectiveSelectedChanges.has(item.change)).sort(
    (left, right) =>
      reportPriority(left) - reportPriority(right) ||
      new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );
  const toggleChange = (change: string) => {
    setExcludedChanges((current) => {
      const next = new Set(current);
      if (next.has(change)) {
        next.delete(change);
      } else {
        const selectedCount = availableChanges.filter(
          (value) => !current.has(value),
        ).length;
        if (selectedCount === 1) return current;
        next.add(change);
      }
      return next;
    });
  };
  const hasFilteredChanges = availableChanges.some((change) =>
    excludedChanges.has(change),
  );

  return (
    <div className="report-list-shell">
      <div className="report-filter-toolbar">
        <details className="report-filter">
          <summary aria-label={t("reportFilter")} title={t("reportFilter")}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 6h16M7 12h10M10 18h4" />
            </svg>
            {hasFilteredChanges ? <i>{effectiveSelectedChanges.size}</i> : null}
          </summary>
          <div className="report-filter-menu">
            <strong>{t("reportStatuses")}</strong>
            {availableChanges.map((change) => {
              const checked = effectiveSelectedChanges.has(change);
              return (
                <label key={change}>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={checked && effectiveSelectedChanges.size === 1}
                    onChange={() => toggleChange(change)}
                  />
                  <span className={`change-chip ${changeTone(change)}`}>
                    {carStatusText(change, t)}
                  </span>
                </label>
              );
            })}
            {hasFilteredChanges ? (
              <button type="button" onClick={() => setExcludedChanges(new Set())}>
                {t("resetFilters")}
              </button>
            ) : null}
          </div>
        </details>
      </div>
      <div className="report-list">
        {sorted.map((item) => (
          <article
            className={item.favorite ? "report-favorite" : undefined}
            key={`${item.project_id}-${item.car_id}`}
          >
            <button className="report-car-main" onClick={() => openCar(item)}>
              <span className="report-car-title">
                <strong>{item.title || `Encar ${item.encar_id}`}</strong>
                <small>
                  {t("foundInProject")}: {item.project_name || `#${item.project_id}`}
                </small>
              </span>
              <span className={`change-chip ${changeTone(item.change)}`}>
                {carStatusText(item.change, t)}
              </span>
              <ReportPrice item={item} />
              <span className="report-open">→</span>
            </button>
            {item.change === "MATERIAL_UPDATE" || item.changes?.length ? (
              <details className="change-details">
                <summary>{t("changeDetails")}</summary>
                {item.changes?.length ? (
                  item.changes.map((change, index) => (
                    <div key={`${change.field}-${index}`}>
                      <strong>{changeFieldLabel(change.field, locale)}</strong>
                      <span>
                        {changeValue(change.old, change.field, locale)} →{" "}
                        {changeValue(change.new, change.field, locale)}
                      </span>
                    </div>
                  ))
                ) : (
                  <p>{t("noChangeDetails")}</p>
                )}
              </details>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function failureText(code: string, t: Translate) {
  if (code === "TIMEOUT") return t("errorTimeout");
  if (code === "CAPTCHA") return t("errorCaptcha");
  if (code === "IDENTITY_MISMATCH") return t("errorIdentityMismatch");
  if (code === "INCOMPLETE_SEARCH") return t("errorIncompleteSearch");
  if (code === "INCOMPLETE_PAGINATION") return t("errorIncompletePagination");
  if (code === "PRICE_NOT_CONFIRMED") return t("errorPriceNotConfirmed");
  if (code === "NOT_FOUND") return t("errorNotFound");
  return t("errorRead");
}

function FailureList({ failures, t }: { failures: ScanFailure[]; t: Translate }) {
  if (!failures.length) return null;
  return (
    <div className="failure-list">
      <strong>{t("searchErrors")} · {failures.length}</strong>
      {failures.map((failure, index) => (
        <article key={`${failure.scope}-${failure.project_id}-${failure.car_id}-${index}`}>
          <div>
            <span className="change-chip error">{failure.code}</span>
            <strong>
              {failure.project_name || failure.encar_id || `${failure.scope} #${failure.project_id || failure.car_id || "—"}`}
            </strong>
            <span>{failureText(failure.code, t)}</span>
          </div>
          {failure.technical ? (
            <details>
              <summary>{t("technicalDetails")}</summary>
              <pre>{failure.technical}</pre>
            </details>
          ) : null}
        </article>
      ))}
    </div>
  );
}

function Projects({
  t,
  locale,
  projects,
  loading,
  error,
  selected,
  setSelected,
  activeScans,
  latestReport,
  refresh,
  cancelScan,
  open,
  create,
  edit,
  remove,
  notify,
  openLookupCar,
  openReportCar,
  openFavorites,
}: {
  t: Translate;
  locale: Locale;
  projects: ProjectRecord[];
  loading: boolean;
  error: boolean;
  selected: number[];
  setSelected: (ids: number[]) => void;
  activeScans: ScanRecord[];
  latestReport?: ScanRecord;
  refresh: (ids: number[]) => void;
  cancelScan: (scanId: number) => void;
  open: (project: ProjectRecord) => void;
  create: () => void;
  edit: (project: ProjectRecord) => void;
  remove: (project: ProjectRecord) => void;
  notify: (message: string) => void;
  openLookupCar: (result: CarLookupResult) => void;
  openReportCar: (item: ScanReportItem) => void;
  openFavorites: () => void;
}) {
  const [grid, setGrid] = useState(false);
  const [sort, setSort] = useState<"updated" | "name">("updated");
  const [carSearch, setCarSearch] = useState("");
  const lookupMutation = useMutation({
    mutationFn: (value: string) =>
      request<CarLookupResult>(`/car-lookup?q=${encodeURIComponent(value.trim())}`),
  });
  const sorted = useMemo(
    () =>
      [...projects].sort((left, right) =>
        sort === "name"
          ? left.name.localeCompare(right.name)
          : new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
      ),
    [projects, sort],
  );
  const totalCars = projects.reduce((sum, project) => sum + project.cars, 0);
  const errors = projects.filter((project) => project.latest_scan?.status === "FAILED").length;
  const current = activeScans[0];
  const currentPagination = current?.payload?.pagination?.[
    String(current.payload.current_project_id || "")
  ];
  const newListingsCount =
    latestReport?.payload?.summary?.new
    ?? latestReport?.payload?.report?.filter((item) => item.change === "NEW").length
    ?? 0;
  return (
    <>
      <div className="title-row">
        <div>
          <h1>{t("projects")}</h1>
          <p>{t("projectsSubtitle")}</p>
        </div>
        <div>
          <Button onClick={openFavorites}>★ {t("favorites")}</Button>
          <Button
            onClick={() => refresh(selected.length ? selected : projects.map((project) => project.id))}
            disabled={!projects.length}
          >
            ↻ {selected.length ? t("refreshSelected") : t("refreshProjects")}
          </Button>
          <Button kind="primary" onClick={create}>
            ＋ {t("createProject")}
          </Button>
        </div>
      </div>
      <div className="stats">
        <Stat label={t("activeProjects")} value={projects.length} />
        <Stat label={t("carsRecent")} value={totalCars} note={t("storedLocally")} tone="blue" />
        <Stat
          label={t("activeScans")}
          value={activeScans.length}
          note={current ? `${current.progress}%` : t("queueEmpty")}
          tone="orange"
        />
        <Stat label={t("attention")} value={errors} note={t("failedScans")} tone="red" />
      </div>
      <section className="panel car-lookup">
        <div>
          <h3>{t("searchCarTitle")}</h3>
          <p>{t("searchCarHelp")}</p>
        </div>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (carSearch.trim()) lookupMutation.mutate(carSearch);
          }}
        >
          <input
            value={carSearch}
            onChange={(event) => setCarSearch(event.target.value)}
            placeholder={t("searchCarPlaceholder")}
            aria-label={t("searchCarTitle")}
          />
          <Button type="submit" kind="primary" disabled={!carSearch.trim() || lookupMutation.isPending}>
            {lookupMutation.isPending ? t("searching") : t("findCar")}
          </Button>
        </form>
        {lookupMutation.data ? (
          lookupMutation.data.found && lookupMutation.data.car ? (
            <button
              className="lookup-result"
              onClick={() => openLookupCar(lookupMutation.data as CarLookupResult)}
            >
              {lookupMutation.data.car.image ? (
                <img src={assetUrl(lookupMutation.data.car.image)} alt="" />
              ) : <span className="lookup-placeholder">E</span>}
              <span>
                <strong>{lookupMutation.data.car.title || `Encar ${lookupMutation.data.encar_id}`}</strong>
                <small>
                  {formatMoney(lookupMutation.data.car.price)} · {t("foundInDatabase")} ·{" "}
                  {lookupMutation.data.projects?.length
                    ? lookupMutation.data.projects.map((project) => project.name).join(", ")
                    : t("removedFromSearch")}
                </small>
              </span>
              <b>{t("openCar")} →</b>
            </button>
          ) : (
            <div className="lookup-empty">
              {lookupMutation.data.reason === "invalid_encar_url"
                ? t("invalidEncarUrl")
                : t("carNotInDatabase")}
            </div>
          )
        ) : null}
      </section>
      <div className="toolbar">
        <strong>
          {t("allProjects")} · {projects.length}
        </strong>
        <button className={!grid ? "chosen" : ""} onClick={() => setGrid(false)}>
          ☷
        </button>
        <button className={grid ? "chosen" : ""} onClick={() => setGrid(true)}>
          ▦
        </button>
        <select value={sort} onChange={(event) => setSort(event.target.value as "updated" | "name")}>
          <option value="updated">{t("recentlyUpdated")}</option>
          <option value="name">{t("name")}</option>
        </select>
      </div>
      {loading && <div className="panel empty-state">{t("loadingProjects")}</div>}
      {error && <div className="error-strip">{t("backendUnavailable")}</div>}
      {!loading && !error && !projects.length && (
        <div className="panel empty-state">
          <b>{t("noProjects")}</b>
          <span>{t("createOrImport")}</span>
        </div>
      )}
      <div className={grid ? "project-grid" : "project-list"}>
        {sorted.map((project) => {
          const activeScan = activeScans.find((scan) =>
            scan.payload?.project_ids?.includes(project.id),
          );
          const completedStatus =
            activeScan?.payload?.project_statuses?.[String(project.id)];
          const status = activeScan
            ? activeScan.status === "CANCEL_REQUESTED"
              ? "CANCEL_REQUESTED"
              : completedStatus === "SUCCEEDED"
              ? "UPDATED"
              : completedStatus === "FAILED"
                ? "FAILED"
                : activeScan.status === "RUNNING" &&
              activeScan.payload?.current_project_id === project.id
                  ? "RUNNING"
                  : "QUEUED"
            : project.latest_scan?.status || "SUCCEEDED";
          return (
            <article className="project-row" key={project.id}>
              <input
                type="checkbox"
                aria-label={t("select")}
                checked={selected.includes(project.id)}
                onChange={(event) =>
                  setSelected(
                    event.target.checked
                      ? [...selected, project.id]
                      : selected.filter((id) => id !== project.id),
                  )
                }
              />
              <button className="project-name" onClick={() => open(project)}>
                <b>{project.name}</b>
              </button>
              <div className="count">
                <span>{t("carsCountLabel")}:</span>
                <b>{project.cars}</b>
              </div>
              <span
                className={`badge project-mode-badge ${
                  project.scan_mode === "FAST" ? "blue" : "outline"
                }`}
              >
                <span>{t(project.scan_mode === "FAST" ? "fast" : "accurate")}</span>
                <small>
                  {t(
                    project.search_page_mode === "ALL_PAGES"
                      ? "allPagesShort"
                      : "firstPageShort",
                  )}
                </small>
              </span>
              <span className={`status ${status.toLowerCase()}`}>
                <i />
                {t(scanStatusKey(status))}
                {status === "RUNNING" ? (
                  <span className="running-track" aria-hidden="true"><span /></span>
                ) : null}
              </span>
              <time className="project-updated">
                <small>{t("lastChanged")}</small>
                <span>{formatDate(project.updated_at, locale)}</span>
              </time>
              <div className="project-actions">
                <button
                  className="icon-button"
                  aria-label={t("edit")}
                  title={t("edit")}
                  onClick={() => edit(project)}
                >
                  ✎
                </button>
                <button
                  className="icon-button"
                  aria-label={t("copySearchLink")}
                  title={t("copySearchLink")}
                  onClick={async () => {
                    await navigator.clipboard.writeText(project.search_url);
                    notify(t("searchLinkCopied"));
                  }}
                >
                  ⧉
                </button>
                <button
                  className="icon-button danger-icon"
                  aria-label={t("delete")}
                  title={t("delete")}
                  onClick={() => remove(project)}
                >
                  ×
                </button>
              </div>
            </article>
          );
        })}
      </div>
      {current && (
        <section className="panel progress-panel live-progress">
          <div>
            <h3>{t("projectUpdate")}</h3>
            <b>{current.progress}%</b>
            <Button
              kind="danger"
              disabled={current.status === "CANCEL_REQUESTED"}
              onClick={() => cancelScan(current.id)}
            >
              {t(current.status === "CANCEL_REQUESTED" ? "cancelling" : "cancelUpdate")}
            </Button>
          </div>
          <p>
            {currentPagination
              ? `${t("page")} ${currentPagination.current_page} ${t("of")} ${currentPagination.total_pages} · ${t("listingsFound")}: ${currentPagination.found_count}`
              : t(scanStatusKey(current.status))}
          </p>
          <div className="progress">
            <i style={{ width: `${current.progress}%` }} />
          </div>
        </section>
      )}
      {!current && latestReport ? (
        <section className="panel scan-report">
          <div className="report-heading">
            <div>
              <h3>{t("latestUpdateReport")}</h3>
              <p>
                {latestReport.payload?.summary?.changed || latestReport.payload?.report?.length || 0} {t("changes")} ·{" "}
                {formatDate(latestReport.created_at, locale)}
              </p>
            </div>
            {newListingsCount > 0 ? (
              <span className="badge blue">
                {t("newListingsFound")}: {newListingsCount}
              </span>
            ) : null}
          </div>
          {!!latestReport.payload?.invalidated_report_count && (
            <p className="integrity-notice">
              {t("invalidatedHistoryHidden")}: {latestReport.payload.invalidated_report_count}
            </p>
          )}
          {!latestReport.payload?.report?.length ? (
            <div className="report-empty"><strong>{t("noChangedCars")}</strong><span>{t("allListingsUnchanged")}</span></div>
          ) : (
            <ReportList
              items={latestReport.payload.report}
              t={t}
              locale={locale}
              openCar={openReportCar}
            />
          )}
          <FailureList failures={latestReport.payload?.failures || []} t={t} />
        </section>
      ) : null}
    </>
  );
}

function CarListRow({
  car,
  t,
  openCar,
  toggleFavorite,
  removeFromProject,
  showProject = false,
}: {
  car: CarRecord;
  t: Translate;
  openCar: (car: CarRecord) => void;
  toggleFavorite: (car: CarRecord) => void;
  removeFromProject?: (car: CarRecord) => void;
  showProject?: boolean;
}) {
  const details = car.details || {};
  const condition = carCondition(details.condition_summary, t);
  const stateClasses = [
    "car-row",
    car.favorite ? "is-favorite" : "",
    !car.viewed ? "is-unviewed" : "",
    car.status === "UNAVAILABLE" ? "is-unavailable" : "",
    car.status === "SOLD" ? "is-sold" : "",
  ].filter(Boolean).join(" ");
  return (
    <article className={stateClasses}>
      <button className="car-thumb" onClick={() => openCar(car)}>
        {car.image ? (
          <img src={assetUrl(car.image)} alt="" />
        ) : (
          <>
            <div className="road" />
            <span>▰</span>
          </>
        )}
      </button>
      <button className="car-name" onClick={() => openCar(car)}>
        <b>{car.title || `${t("car")} ${car.encar_id}`}</b>
        <small>
          {String(details.year_month || "—")} · {details.mileage_km
            ? `${Number(details.mileage_km).toLocaleString()} km`
            : "—"}
          {showProject && car.project_name ? ` · ${car.project_name}` : ""}
        </small>
      </button>
      <div className="price">
        <b>{car.price ? `₩ ${car.price.toLocaleString()}` : "—"}</b>
        <PriceMovement change={car.price_change} t={t} />
        {details.new_car_price_percent ? (
          <span className="new-price-ratio">
            {String(details.new_car_price_percent)}% {t("ofNewCarPrice")}
          </span>
        ) : null}
      </div>
      <div className="tags">
        <span className={`car-status ${car.status.toLowerCase()}`}>{carStatusText(car.status, t)}</span>
        <span className={`condition-chip ${condition.tone}`}>{condition.label}</span>
        {!car.viewed && <span className="unviewed">{t("notViewed")}</span>}
      </div>
      <div className="row-actions">
        <button
          className="external-car-link"
          aria-label={t("openEncar")}
          title={t("openEncar")}
          onClick={() => void openExternalUrl(car.url)}
        >
          🌐
        </button>
        <button
          className={`favorite-star ${car.favorite ? "active" : ""}`}
          aria-label={t(car.favorite ? "removeFavorite" : "addFavorite")}
          title={t(car.favorite ? "removeFavorite" : "addFavorite")}
          onClick={() => toggleFavorite(car)}
        >
          {car.favorite ? "★" : "☆"}
        </button>
        {removeFromProject ? (
          <button
            className="remove-car"
            aria-label={t("removeCarFromProject")}
            title={t("removeCarFromProject")}
            onClick={() => removeFromProject(car)}
          >
            🗑
          </button>
        ) : null}
      </div>
    </article>
  );
}

function Favorites({
  t,
  back,
  openCar,
  notify,
}: {
  t: Translate;
  back: () => void;
  openCar: (car: CarRecord) => void;
  notify: (message: string) => void;
}) {
  const client = useQueryClient();
  const favoritesQuery = useQuery<CarRecord[]>({
    queryKey: ["favorites"],
    queryFn: () => request("/favorites"),
    refetchInterval: 5000,
  });
  const favoriteMutation = useMutation({
    mutationFn: (car: CarRecord) =>
      request(`/projects/${car.project_id}/cars/${car.id}/favorite`, {
        method: "PATCH",
        body: JSON.stringify({ value: false }),
      }),
    onSuccess: (_, car) => {
      void client.invalidateQueries({ queryKey: ["favorites"] });
      void client.invalidateQueries({ queryKey: ["project-cars", car.project_id] });
    },
    onError: () => notify(t("actionFailed")),
  });
  const favorites = [...(favoritesQuery.data || [])].sort(
    (left, right) =>
      Number(left.viewed) - Number(right.viewed) ||
      new Date(right.updated_at || 0).getTime() -
        new Date(left.updated_at || 0).getTime(),
  );
  return (
    <>
      <button className="back" onClick={back}>← {t("backProjects")}</button>
      <div className="title-row">
        <div>
          <h1>★ {t("favorites")}</h1>
          <p>{t("favoritesSubtitle")} · {favorites.length}</p>
        </div>
        <div>
          <span className="badge outline">{favorites.length} {t("cars").toLowerCase()}</span>
        </div>
      </div>
      {favoritesQuery.isLoading && <div className="panel empty-state">{t("loadingCars")}</div>}
      {!favoritesQuery.isLoading && !favorites.length && (
        <div className="panel empty-state">
          <b>{t("noFavorites")}</b>
        </div>
      )}
      <div className="cars-list">
        {favorites.map((car) => (
          <CarListRow
            key={`${car.project_id}-${car.id}`}
            car={car}
            t={t}
            openCar={openCar}
            toggleFavorite={(item) => favoriteMutation.mutate(item)}
            showProject
          />
        ))}
      </div>
    </>
  );
}

function Project({
  t,
  locale,
  projectId,
  back,
  openCar,
  refresh,
  activeScan,
  cancelScan,
  edit,
  remove,
  notify,
}: {
  t: Translate;
  locale: Locale;
  projectId: number | null;
  back: () => void;
  openCar: (car: CarRecord) => void;
  refresh: () => void;
  activeScan?: ScanRecord;
  cancelScan: (scanId: number) => void;
  edit: (project: ProjectRecord) => void;
  remove: (project: ProjectRecord) => void;
  notify: (message: string) => void;
}) {
  const client = useQueryClient();
  const [filter, setFilter] = useState<
    "all" | "favorite" | "NEW" | "UNAVAILABLE" | "SOLD"
  >("all");
  type ProjectSort = "priority" | "added_desc" | "added_asc" | "price_asc" | "price_desc";
  const [sort, setSort] = useState<ProjectSort>(() => {
    if (typeof window === "undefined") return "priority";
    const saved = window.localStorage.getItem("encar-project-sort");
    return ["priority", "added_desc", "added_asc", "price_asc", "price_desc"].includes(
      saved || "",
    )
      ? saved as ProjectSort
      : "priority";
  });
  useEffect(() => {
    window.localStorage.setItem("encar-project-sort", sort);
  }, [sort]);
  const projectQuery = useQuery<ProjectRecord>({
    queryKey: ["project", projectId],
    queryFn: () => request(`/projects/${projectId}`),
    refetchInterval: 5000,
  });
  const carsQuery = useQuery<CarRecord[]>({
    queryKey: ["project-cars", projectId],
    queryFn: () => request(`/projects/${projectId}/cars`),
    refetchInterval: 5000,
  });
  const patchRelation = useMutation({
    mutationFn: ({
      carId,
      field,
      value,
    }: {
      carId: number;
      field: "favorite" | "viewed";
      value: boolean;
    }) =>
      request(`/projects/${projectId}/cars/${carId}/${field}`, {
        method: "PATCH",
        body: JSON.stringify({ value }),
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["project-cars", projectId] }),
    onError: () => notify(t("actionFailed")),
  });
  const removeCarMutation = useMutation({
    mutationFn: (carId: number) =>
      request<void>(`/projects/${projectId}/cars/${carId}`, { method: "DELETE" }),
    onSuccess: () => {
      notify(t("carRemovedFromProject"));
      void client.invalidateQueries({ queryKey: ["project-cars", projectId] });
      void client.invalidateQueries({ queryKey: ["project", projectId] });
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["favorites"] });
    },
    onError: () => notify(t("removeCarFailed")),
  });
  const project = projectQuery.data;
  const cars = carsQuery.data || [];
  const activePagination = activeScan?.payload?.pagination?.[String(projectId || "")];
  const filtered = cars.filter((car) => {
    if (filter === "all") return true;
    if (filter === "favorite") return car.favorite;
    return filter === "UNAVAILABLE"
      ? ["UNAVAILABLE", "NOT_FOUND_IN_SEARCH"].includes(car.status)
      : car.status === filter;
  });
  const visible = [...filtered].sort((left, right) => {
    if (sort === "priority") {
      const priority = (car: CarRecord) =>
        car.status === "NEW" ? 0 : car.price_change ? 1 : 2;
      return (
        priority(left) - priority(right) ||
        new Date(right.first_seen_at || 0).getTime() -
          new Date(left.first_seen_at || 0).getTime()
      );
    }
    if (sort === "added_desc" || sort === "added_asc") {
      const difference =
        new Date(right.first_seen_at || 0).getTime() -
        new Date(left.first_seen_at || 0).getTime();
      return sort === "added_desc" ? difference : -difference;
    }
    const difference = (left.price || 0) - (right.price || 0);
    return sort === "price_asc" ? difference : -difference;
  });
  if (!project) return <div className="panel empty-state">{t("loadingProjects")}</div>;
  return (
    <>
      <button className="back" onClick={back}>
        ← {t("backProjects")}
      </button>
      <div className="title-row">
        <div>
          <h1>{project.name}</h1>
          <p>
            {project.cars} {t("cars").toLowerCase()} ·{" "}
            {t(project.scan_mode === "FAST" ? "fast" : "accurate")} ·{" "}
            {t(project.search_page_mode === "ALL_PAGES" ? "allPages" : "firstPageOnly")}
          </p>
        </div>
        <div>
          <Button onClick={() => setFilter("favorite")}>
            ★ {t("projectFavorites")} ({cars.filter((car) => car.favorite).length})
          </Button>
          {activeScan ? (
            <Button
              kind="danger"
              disabled={activeScan.status === "CANCEL_REQUESTED"}
              onClick={() => cancelScan(activeScan.id)}
            >
              {t(activeScan.status === "CANCEL_REQUESTED" ? "cancelling" : "cancelUpdate")}
            </Button>
          ) : (
            <Button kind="primary" onClick={refresh}>
              ↻ {t("refreshProject")}
            </Button>
          )}
          {project.telegram_url ? (
            <Button onClick={() => void openExternalUrl(project.telegram_url || "")}>
              <span className="telegram-icon" aria-hidden="true">✈</span> Telegram
            </Button>
          ) : null}
          <Button onClick={() => edit(project)}>{t("edit")}</Button>
          <Button
            onClick={async () => {
              await navigator.clipboard.writeText(project.search_url);
              notify(t("searchLinkCopied"));
            }}
          >
            ⧉ {t("copySearchLink")}
          </Button>
          <Button kind="danger" onClick={() => remove(project)}>
            {t("delete")}
          </Button>
        </div>
      </div>
      <div className="stats compact">
        <Stat label={t("carsFound")} value={cars.length} />
        <Stat label={t("new")} value={cars.filter((car) => car.status === "NEW").length} tone="green" />
        <Stat
          label={t("projectFavorites")}
          value={cars.filter((car) => car.favorite).length}
          tone="red"
          onClick={() => setFilter("favorite")}
        />
        <Stat
          label={t("unavailableAndSold")}
          value={cars.filter((car) =>
            ["UNAVAILABLE", "SOLD", "NOT_FOUND_IN_SEARCH"].includes(car.status),
          ).length}
          tone="red"
        />
        <Stat label={t("updated")} value={formatDate(project.updated_at, locale)} />
      </div>
      {activeScan && (
        <section className="panel progress-panel live-progress">
          <div>
            <h3>{t("projectUpdate")}</h3>
            <b>{activeScan.progress}%</b>
          </div>
          <p>
            {activePagination
              ? `${t("page")} ${activePagination.current_page} ${t("of")} ${activePagination.total_pages} · ${t("listingsFound")}: ${activePagination.found_count}`
              : t(scanStatusKey(activeScan.status))}
          </p>
          <div className="progress">
            <i style={{ width: `${activeScan.progress}%` }} />
          </div>
        </section>
      )}
      <div className="filterbar">
        {[
          ["all", t("all")],
          ["NEW", t("new")],
          ["favorite", t("projectFavorites")],
          ["UNAVAILABLE", t("unavailable")],
          ["SOLD", t("sold")],
        ].map(([value, label]) => (
          <button
            key={value}
            className={filter === value ? "active" : ""}
            onClick={() => setFilter(value as typeof filter)}
          >
            {label}
          </button>
        ))}
        <span />
        <select
          value={sort}
          aria-label={t("sortPriority")}
          onChange={(event) => setSort(event.target.value as typeof sort)}
        >
          <option value="priority">{t("sortPriority")}</option>
          <option value="added_desc">{t("sortAddedNewest")}</option>
          <option value="added_asc">{t("sortAddedOldest")}</option>
          <option value="price_asc">{t("sortPriceAsc")}</option>
          <option value="price_desc">{t("sortPriceDesc")}</option>
        </select>
      </div>
      {carsQuery.isLoading && <div className="panel empty-state">{t("loadingCars")}</div>}
      {!carsQuery.isLoading && !visible.length && (
        <div className="panel empty-state">{t("noCarsInProject")}</div>
      )}
      <div className="cars-list">
        {visible.map((car) => (
          <CarListRow
            key={car.id}
            car={car}
            t={t}
            openCar={openCar}
            toggleFavorite={(item) =>
              patchRelation.mutate({
                carId: item.id,
                field: "favorite",
                value: !item.favorite,
              })
            }
            removeFromProject={(item) => {
              if (window.confirm(t("removeCarFromProjectConfirm"))) {
                removeCarMutation.mutate(item.id);
              }
            }}
          />
        ))}
      </div>
    </>
  );
}

function Car({
  t,
  locale,
  carId,
  projectId,
  activeScan,
  cancelScan,
  back,
  notify,
}: {
  t: Translate;
  locale: Locale;
  carId: number;
  projectId: number | null;
  activeScan?: ScanRecord;
  cancelScan: (scanId: number) => void;
  back: () => void;
  notify: (message: string) => void;
}) {
  const client = useQueryClient();
  const carQuery = useQuery<CarDetails>({
    queryKey: ["car", carId, projectId],
    queryFn: () => request(`/cars/${carId}${projectId ? `?project_id=${projectId}` : ""}`),
  });
  const historyQuery = useQuery<{ price: number; at: string }[]>({
    queryKey: ["price-history", carId],
    queryFn: () => request(`/cars/${carId}/price-history`),
  });
  const eventsQuery = useQuery<{ kind: string; payload: Record<string, unknown>; created_at: string }[]>({
    queryKey: ["car-events", carId],
    queryFn: () => request(`/cars/${carId}/events`),
  });
  const [commentDraft, setCommentDraft] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number } | null>(null);
  const comment = commentDraft ?? carQuery.data?.comment ?? "";
  const saveComment = useMutation({
    mutationFn: () =>
      request(`/cars/${carId}/comment`, {
        method: "PATCH",
        body: JSON.stringify({ comment }),
      }),
    onSuccess: () => {
      notify(t("saved"));
      setCommentDraft(null);
      void client.invalidateQueries({ queryKey: ["car", carId, projectId] });
      void client.invalidateQueries({ queryKey: ["car-events", carId] });
    },
  });
  const ratingMutation = useMutation({
    mutationFn: (rating: number | null) =>
      request<{ rating: number | null }>(`/cars/${carId}/rating`, {
        method: "PATCH",
        body: JSON.stringify({ rating }),
      }),
    onSuccess: (result) => {
      client.setQueryData<CarDetails>(
        ["car", carId, projectId],
        (current) => current ? { ...current, rating: result.rating } : current,
      );
    },
    onError: () => notify(t("actionFailed")),
  });
  const favoriteMutation = useMutation({
    mutationFn: (value: boolean) => {
      if (!projectId) throw new Error("project_relation_unavailable");
      return request<{ favorite: boolean }>(`/projects/${projectId}/cars/${carId}/favorite`, {
        method: "PATCH",
        body: JSON.stringify({ value }),
      });
    },
    onSuccess: (result) => {
      client.setQueryData<CarDetails>(
        ["car", carId, projectId],
        (current) => current ? { ...current, favorite: result.favorite } : current,
      );
      void client.invalidateQueries({ queryKey: ["project-cars", projectId] });
      void client.invalidateQueries({ queryKey: ["favorites"] });
    },
    onError: () => notify(t("actionFailed")),
  });
  const excludeMutation = useMutation({
    mutationFn: () => request(`/cars/${carId}/exclude`, { method: "POST" }),
    onSuccess: () => {
      notify(t("saved"));
      void client.invalidateQueries({ queryKey: ["project-cars", projectId] });
      back();
    },
  });
  const refreshMutation = useMutation({
    mutationFn: () =>
      request(`/cars/${carId}${projectId ? `/refresh?project_id=${projectId}` : "/refresh"}`, {
        method: "POST",
      }),
    onSuccess: () => notify(t("scanQueued")),
    onError: () => notify(t("scanAlreadyActive")),
  });
  useEffect(() => {
    if (!projectId) return;
    let active = true;
    request<{ viewed: boolean }>(`/projects/${projectId}/cars/${carId}/viewed`, {
      method: "PATCH",
      body: JSON.stringify({ value: true }),
    })
      .then(() => {
        if (!active) return;
        client.setQueryData<CarDetails>(
          ["car", carId, projectId],
          (current) => current ? { ...current, viewed: true } : current,
        );
        void client.invalidateQueries({ queryKey: ["project-cars", projectId] });
        void client.invalidateQueries({ queryKey: ["car-events", carId] });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [carId, client, projectId]);
  const copyPrompt = async () => {
    const data = await request<{ prompt: string }>(`/cars/${carId}/chatgpt-prompt`);
    await navigator.clipboard.writeText(data.prompt);
    notify(t("promptCopied"));
  };
  const car = carQuery.data;
  if (!car) return <div className="panel empty-state">{t("loadingCars")}</div>;
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const details = car.details || {};
  const history = historyQuery.data || [];
  const chart = history.map((point) => ({
    date: new Intl.DateTimeFormat(locale === "uk" ? "uk-UA" : "ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(point.at)),
    price: point.price,
  }));
  const latest = history.at(-1);
  const previous = history.at(-2);
  const delta = latest && previous ? latest.price - previous.price : 0;
  const mainImage = assetUrl(car.images?.MAIN?.url);
  const screenshot = assetUrl(car.images?.SCREENSHOT?.url);
  const condition = (details.condition || {}) as Record<
    string,
    { status?: string; value?: unknown; original_evidence?: string[] }
  >;
  const conditionLabels: Record<string, string> = {
    insurance_events: l("Страховые случаи", "Страхові випадки"),
    insurance_claim_count: l("Количество страховых случаев", "Кількість страхових випадків"),
    total_insurance_amount: l("Общая сумма страховых выплат", "Загальна сума страхових виплат"),
    own_vehicle_payments: l("Выплаты по своей машине", "Виплати за власним автомобілем"),
    third_party_payments: l("Выплаты третьей стороне", "Виплати третій стороні"),
    replaced_panels: l("Заменённые панели", "Замінені панелі"),
    body_repair: l("Кузовной ремонт", "Кузовний ремонт"),
    paint_work: l("Покраска", "Фарбування"),
    structural_damage: l("Структурные повреждения", "Структурні пошкодження"),
    flood_history: l("История затопления", "Історія затоплення"),
    rental_use: l("Использование в аренде", "Використання в оренді"),
    commercial_use: l("Коммерческое использование", "Комерційне використання"),
    owner_changes: l("Смена владельцев", "Зміна власників"),
    inspection_report: l("Отчёт техосмотра", "Звіт техогляду"),
  };
  const summaryLabels: Record<string, string> = {
    NO_PROBLEMS_STATED: l("Не указаны проблемы", "Проблем не вказано"),
    INSURANCE_CLAIM: l("Есть страховой случай", "Є страховий випадок"),
    ACCIDENT_VEHICLE: l("Машина аварийная", "Автомобіль аварійний"),
    UNVERIFIED: l("Состояние не удалось проверить", "Стан не вдалося перевірити"),
  };
  const statusLabel = (status?: string) =>
    status === "CONFIRMED"
      ? t("confirmed")
      : status === "NOT_FOUND"
        ? t("notDetected")
        : t("unverified");
  const vehicleValue = (value: unknown): string | null => {
    if (value === null || value === undefined || value === "") return null;
    const normalized = String(value || "").toUpperCase();
    const values: Record<string, string> = {
      GASOLINE: l("Бензин", "Бензин"),
      DIESEL: l("Дизель", "Дизель"),
      HYBRID: l("Гибрид", "Гібрид"),
      ELECTRIC: l("Электромобиль", "Електромобіль"),
      LPG: "LPG",
      FWD: l("Передний", "Передній"),
      RWD: l("Задний", "Задній"),
      AWD: l("Полный", "Повний"),
      "4WD": l("Полный", "Повний"),
      AUTOMATIC: l("Автоматическая", "Автоматична"),
      MANUAL: l("Механическая", "Механічна"),
      UNVERIFIED: t("unverified"),
    };
    return values[normalized] || String(value);
  };
  const valueText = (key: string, value: unknown) => {
    if (value === null || value === undefined) return "";
    if (key.includes("amount") || key.includes("payments")) return formatMoney(value);
    return String(value);
  };
  const specRows = [
    [l("Комплектация", "Комплектація"), details.trim],
    [l("Дата первого обнаружения", "Дата першого виявлення"), formatDate(car.first_seen_at, locale)],
    [l("Последнее обновление", "Останнє оновлення"), formatDate(car.updated_at || String(details.checked_at || ""), locale)],
    [l("Стоимость относительно новой", "Вартість відносно нового"), details.new_car_price_percent ? `${details.new_car_price_percent}%` : null],
    [l("Дата производства", "Дата виробництва"), details.production_date || details.year_month],
    [l("Дата регистрации", "Дата реєстрації"), details.registration_date],
    [l("Пробег", "Пробіг"), details.mileage_km ? `${Number(details.mileage_km).toLocaleString(locale === "uk" ? "uk-UA" : "ru-RU")} km` : null],
    [l("Топливо", "Пальне"), vehicleValue(details.fuel)],
    [l("Привод", "Привід"), vehicleValue(details.drivetrain)],
    [l("Коробка передач", "Коробка передач"), vehicleValue(details.transmission)],
    [l("Объём двигателя", "Обʼєм двигуна"), details.engine_displacement_cc ? `${details.engine_displacement_cc} см³` : null],
    [l("Тип кузова", "Тип кузова"), details.body_type],
    [l("Цвет кузова", "Колір кузова"), details.exterior_color],
    [l("Цвет салона", "Колір салону"), details.interior_color],
    ["VIN", details.vin],
    [l("Регистрационный номер", "Реєстраційний номер"), details.registration_number],
    ["Encar ID", car.encar_id],
  ];
  const optionEntries = Object.entries((details.options || {}) as Record<string, unknown>);
  const optionLabels: Record<string, string> = {
    sunroof: l("Люк", "Люк"),
    led_headlights: l("LED-фары", "LED-фари"),
    parking_sensors: l("Парктроники", "Парктроніки"),
    rear_camera: l("Камера заднего вида", "Камера заднього виду"),
    automatic_climate: l("Климат-контроль", "Клімат-контроль"),
    smart_key: l("Бесключевой доступ", "Безключовий доступ"),
    navigation: l("Навигация", "Навігація"),
    heated_seat: l("Подогрев сидений", "Підігрів сидінь"),
    ventilated_seat: l("Вентиляция сидений", "Вентиляція сидінь"),
    leather_seat: l("Кожаные сиденья", "Шкіряні сидіння"),
    memory_seat: l("Память сидений", "Памʼять сидінь"),
    power_tailgate: l("Электропривод багажника", "Електропривід багажника"),
    reported_count: l("Всего опций по Encar", "Усього опцій за Encar"),
  };
  const eventNames: Record<string, string> = {
    NEW: l("Впервые обнаружена", "Вперше виявлено"),
    PRICE_DROP: l("Цена снизилась", "Ціна знизилася"),
    PRICE_INCREASE: l("Цена выросла", "Ціна зросла"),
    MATERIAL_UPDATE: l("Данные объявления изменились", "Дані оголошення змінилися"),
    NOT_FOUND_IN_SEARCH: l("Не найдена в поиске", "Не знайдено в пошуку"),
    UNAVAILABLE: l("Объявление временно недоступно", "Оголошення тимчасово недоступне"),
    SOLD: l("Продана", "Продано"),
    RELISTED: l("Найдена снова", "Знайдено знову"),
    INSURANCE_INFO_CHANGED: l("Изменились страховые данные", "Страхові дані змінилися"),
    ACCIDENT_INFO_CHANGED: l("Изменилась информация об авариях", "Інформація про аварії змінилася"),
    MANUAL_REFRESH: l("Обновлена вручную", "Оновлено вручну"),
    USER_VIEWED: l("Отмечена просмотренной", "Позначено переглянутим"),
    REMOVED_FROM_PROJECT: l("Удалена из проекта", "Видалено з проєкту"),
  };
  const startDrag = (event: PointerEvent<HTMLImageElement>) => {
    drag.current = { x: event.clientX - pan.x, y: event.clientY - pan.y };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const moveDrag = (event: PointerEvent<HTMLImageElement>) => {
    if (drag.current) setPan({ x: event.clientX - drag.current.x, y: event.clientY - drag.current.y });
  };
  return (
    <>
      <button className="back" onClick={back}>
        ← {projectId ? t("backProject") : t("backProjects")}
      </button>
      <div className="title-row">
        <div>
          <h1>{car.title || `${t("car")} ${car.encar_id}`}</h1>
          <p>Encar ID {car.encar_id}</p>
        </div>
        <div>
          {projectId ? (
            activeScan ? (
              <Button
                kind="danger"
                disabled={activeScan.status === "CANCEL_REQUESTED"}
                onClick={() => cancelScan(activeScan.id)}
              >
                {t(activeScan.status === "CANCEL_REQUESTED" ? "cancelling" : "cancelUpdate")}
              </Button>
            ) : (
              <Button kind="primary" onClick={() => refreshMutation.mutate()}>
                ↻ {t("refresh")}
              </Button>
            )
          ) : null}
          <Button onClick={() => void openExternalUrl(car.url)}>{t("openEncar")} ↗</Button>
        </div>
      </div>
      <section className="vehicle-hero panel">
        <button
          className="vehicle-photo"
          onClick={() => mainImage && setLightbox(mainImage)}
          disabled={!mainImage}
        >
          {mainImage ? <img src={mainImage} alt={car.title || "Encar vehicle"} /> : <span>{l("Фото появится после обновления", "Фото зʼявиться після оновлення")}</span>}
        </button>
        <div className="vehicle-main">
          <small>{t("currentPrice")}</small>
          <div className="big-price">{formatMoney(car.price)}</div>
          {details.under_contract ? (
            <span className="contract-chip">{t("underContract")}</span>
          ) : null}
          {delta ? (
            <span className={`price-delta ${delta < 0 ? "down" : "up"}`}>
              {delta < 0 ? "↓" : "↑"} {formatMoney(Math.abs(delta))}
            </span>
          ) : null}
          <div className="tags wide">
            <span className="new">{carStatusText(car.status, t)}</span>
            <span className={`condition-summary ${String(details.condition_summary || "UNVERIFIED").toLowerCase()}`}>
              {summaryLabels[String(details.condition_summary || "UNVERIFIED")]}
            </span>
          </div>
          <dl className="specs full-specs">
            {specRows.map(([label, value]) => (
              <div key={String(label)}>
                <dt>{String(label)}</dt>
                <dd>{value === null || value === undefined || value === "" ? "—" : String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>
      <div className="actionbar">
        {projectId ? (
          <>
            <Button
              disabled={favoriteMutation.isPending}
              onClick={() => favoriteMutation.mutate(!car.favorite)}
            >
              {car.favorite ? `♥ ${t("removeFavorite")}` : `♡ ${t("addFavorite")}`}
            </Button>
          </>
        ) : null}
        <Button onClick={() => void copyPrompt()}>{t("copyPrompt")}</Button>
        <Button onClick={() => void openExternalUrl(car.url)}>{t("openEncar")} ↗</Button>
        {projectId ? (
          <Button
            kind="danger"
            onClick={() => window.confirm(l("Исключить эту машину из проектов?", "Виключити цей автомобіль із проєктів?")) && excludeMutation.mutate()}
          >
            {t("excludeCar")}
          </Button>
        ) : null}
        <div className="stars">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              className={value <= (car.rating || 0) ? "lit" : ""}
              disabled={ratingMutation.isPending}
              onClick={() => ratingMutation.mutate(value)}
              key={value}
              aria-label={`${value} из 5`}
            >
              ★
            </button>
          ))}
          {car.rating ? <button disabled={ratingMutation.isPending} onClick={() => ratingMutation.mutate(null)}>{l("Сбросить", "Скинути")}</button> : null}
        </div>
      </div>
      <div className="detail-grid">
        <section className="panel chart-panel">
          <h3>{t("priceHistory")}</h3>
          {chart.length ? (
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={chart}>
                <CartesianGrid stroke="#e7edf6" />
                <XAxis dataKey="date" />
                <YAxis tickFormatter={(value) => `${Math.round(value / 1_000_000)}M`} />
                <Tooltip formatter={(value) => formatMoney(value)} />
                <Area type="monotone" dataKey="price" stroke="#2563eb" fill="#dbeafe" />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state">{t("noPriceHistory")}</div>
          )}
          {history.length ? (
            <div className="history-table">
              {[...history].reverse().map((point, index) => (
                <div key={`${point.at}-${index}`}>
                  <span>{formatDate(point.at, locale)}</span>
                  <b>{formatMoney(point.price)}</b>
                </div>
              ))}
            </div>
          ) : null}
        </section>
        <section className="panel screenshot-card">
          <div className="section-heading">
            <h3>{t("encarScreenshot")}</h3>
            <small>{formatDate(car.images?.SCREENSHOT?.stored_at, locale)}</small>
          </div>
          {screenshot ? (
            <button className="screenshot-preview" onClick={() => setLightbox(screenshot)}>
              <img src={screenshot} alt={l("Скриншот объявления Encar", "Знімок оголошення Encar")} />
              <span>{l("Открыть на весь экран", "Відкрити на весь екран")}</span>
            </button>
          ) : (
            <div className="empty-state">{l("Скриншот появится после обновления", "Знімок зʼявиться після оновлення")}</div>
          )}
        </section>
      </div>
      <section className="panel condition-panel">
        <div className="section-heading">
          <h3>{l("Состояние автомобиля", "Стан автомобіля")}</h3>
          <b>{summaryLabels[String(details.condition_summary || "UNVERIFIED")]}</b>
        </div>
        <div className="condition-list">
          {Object.entries(conditionLabels).map(([key, label]) => {
            const item = condition[key] || {};
            return (
              <article key={key}>
                <div>
                  <strong>{label}</strong>
                  {item.value !== undefined ? <b>{valueText(key, item.value)}</b> : null}
                  <span className={`check-status ${String(item.status || "UNVERIFIED").toLowerCase()}`}>
                    {statusLabel(item.status)}
                  </span>
                </div>
                {item.original_evidence?.length ? (
                  <details>
                    <summary>{l("Оригинал на корейском", "Оригінал корейською")}</summary>
                    {item.original_evidence.map((line, index) => <p key={index} lang="ko">{line}</p>)}
                  </details>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>
      <div className="detail-grid">
        <section className="panel">
          <h3>{l("Опции автомобиля", "Опції автомобіля")}</h3>
          <div className="option-grid">
            {optionEntries.map(([key, value]) => (
              <span className={value ? "available" : "muted"} key={key}>
                {value === true ? "✓" : value === false ? "—" : String(value)} {optionLabels[key] || key.replaceAll("_", " ")}
              </span>
            ))}
          </div>
        </section>
        <section className="panel">
          <h3>{l("Технические данные", "Технічні дані")}</h3>
          <dl className="technical-list">
            {specRows.slice(4).map(([label, value]) => (
              <div key={String(label)}><dt>{String(label)}</dt><dd>{value ? String(value) : "—"}</dd></div>
            ))}
          </dl>
        </section>
      </div>
      <section className="panel comment">
        <h3>{t("personalComment")}</h3>
        <textarea value={comment} onChange={(event) => setCommentDraft(event.target.value)} />
        <div>
          <span>{car.comment_updated_at ? `${t("lastChanged")}: ${formatDate(car.comment_updated_at, locale)}` : ""}</span>
          <Button onClick={() => saveComment.mutate()}>{t("saveChanges")}</Button>
        </div>
      </section>
      <section className="panel timeline">
        <h3>{t("updateHistory")}</h3>
        {(eventsQuery.data || []).map((event, index) => (
          <article key={`${event.created_at}-${index}`}>
            <i />
            <div>
              <strong>{eventNames[event.kind] || l("Событие обновления", "Подія оновлення")}</strong>
              <span>{formatDate(event.created_at, locale)}</span>
            </div>
          </article>
        ))}
      </section>
      <small className="relation-note">
        {projectId ? `${t("projectRelation")} #${projectId}` : t("removedFromSearch")}
      </small>
      {lightbox ? (
        <div className="lightbox" role="dialog" aria-modal="true">
          <div className="lightbox-controls">
            <button onClick={() => setZoom((value) => Math.min(5, value + 0.25))}>＋</button>
            <button onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>−</button>
            <button onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}>100%</button>
            <button onClick={() => setLightbox(null)}>{l("Закрыть", "Закрити")} ×</button>
          </div>
          <img
            src={lightbox}
            alt=""
            style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
            onPointerDown={startDrag}
            onPointerMove={moveDrag}
            onPointerUp={() => { drag.current = null; }}
            draggable={false}
          />
        </div>
      ) : null}
    </>
  );
}

function ScanReportSection({
  scan,
  initiallyOpen,
  t,
  locale,
  openCar,
}: {
  scan: ScanRecord;
  initiallyOpen: boolean;
  t: Translate;
  locale: Locale;
  openCar: (item: ScanReportItem) => void;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const report = scan.payload?.report || [];
  const invalidated = scan.payload?.invalidated_report_count || 0;
  const pagination = Object.values(scan.payload?.pagination || {});
  if (!report.length && !invalidated && !pagination.length) return null;
  return (
    <section className={`scan-report-inline ${open ? "open" : ""}`}>
      {!!pagination.length && (
        <p className="pagination-summary">
          {t("pagesScanned")}:{" "}
          {pagination.reduce((sum, item) => sum + item.pages_visited, 0)} ·{" "}
          {t("listingsFound")}:{" "}
          {pagination.reduce((sum, item) => sum + item.found_count, 0)}
        </p>
      )}
      {!!invalidated && (
        <p className="integrity-notice">
          {t("invalidatedHistoryHidden")}: {invalidated}
        </p>
      )}
      {!!report.length && (
        <>
          <button
            className="scan-report-toggle"
            aria-expanded={open}
            aria-label={t(open ? "collapseList" : "expandList")}
            onClick={() => setOpen((value) => !value)}
          >
            <strong>{t("updatedCars")} · {report.length}</strong>
            <span className={`report-chevron ${open ? "open" : ""}`} aria-hidden="true" />
          </button>
          {open && (
            <ReportList
              items={report}
              t={t}
              locale={locale}
              openCar={openCar}
            />
          )}
        </>
      )}
    </section>
  );
}

function Scans({
  t,
  locale,
  scans,
  openCar,
  cancelScan,
}: {
  t: Translate;
  locale: Locale;
  scans: ScanRecord[];
  openCar: (item: ScanReportItem) => void;
  cancelScan: (scanId: number) => void;
}) {
  const latestReportId = scans.find((scan) => scan.payload?.report?.length)?.id;
  return (
    <>
      <div className="title-row">
        <div>
          <h1>{t("scanHistory")}</h1>
          <p>{t("scanSubtitle")}</p>
        </div>
      </div>
      <section className="panel table">
        <div className="table-head">
          <span>{t("started")}</span>
          <span>{t("scope")}</span>
          <span>{t("mode")}</span>
          <span>{t("statusLabel")}</span>
        </div>
        {!scans.length && <div className="empty-state">{t("noScans")}</div>}
        {scans.map((scan) => (
          <div className="scan-run" key={scan.id}>
            <div className="table-row">
              <span>{formatDate(scan.created_at, locale)}</span>
              <span>{t(scan.kind === "PROJECTS" ? "projectSingular" : "auto")}</span>
              <span>{scan.kind === "PROJECTS" ? t("projects") : t("cars")}</span>
              <span className="scan-status-actions">
                <span className={`status ${scan.status.toLowerCase()}`}>
                  {t(scanStatusKey(scan.status))}
                </span>
                {["QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(scan.status) ? (
                  <button
                    className="cancel-scan"
                    disabled={scan.status === "CANCEL_REQUESTED"}
                    onClick={() => cancelScan(scan.id)}
                  >
                    {t(scan.status === "CANCEL_REQUESTED" ? "cancelling" : "cancelUpdate")}
                  </button>
                ) : null}
              </span>
            </div>
            <ScanReportSection
              scan={scan}
              initiallyOpen={scan.id === latestReportId}
              t={t}
              locale={locale}
              openCar={openCar}
            />
            <FailureList failures={scan.payload?.failures || []} t={t} />
          </div>
        ))}
      </section>
    </>
  );
}

function Settings({
  t,
  locale,
  projects,
  notify,
  isAdmin,
}: {
  t: Translate;
  locale: Locale;
  projects: ProjectRecord[];
  notify: (message: string) => void;
  isAdmin: boolean;
}) {
  const client = useQueryClient();
  const schedulerQuery = useQuery<SchedulerRecord>({
    queryKey: ["scheduler"],
    queryFn: () => request("/settings"),
  });
  const importQuery = useQuery<ImportSummary>({
    queryKey: ["legacy-import"],
    queryFn: () => request("/import/legacy"),
    enabled: isAdmin,
  });
  const desktopDataQuery = useQuery<DesktopDataStatus>({
    queryKey: ["desktop-data"],
    queryFn: () => request("/desktop/data"),
    enabled: isAdmin,
    retry: false,
  });
  const [migrationUrl, setMigrationUrl] = useState(
    "postgresql+psycopg://encar:encar@127.0.0.1:5432/encar",
  );
  const [migrationStorage, setMigrationStorage] = useState("");
  const [schedulerDraft, setSchedulerDraft] = useState<SchedulerRecord | null>(null);
  const scheduler = schedulerDraft ??
    schedulerQuery.data ?? {
      enabled: false,
      paused: false,
      catch_up_enabled: true,
      interval_minutes: 180 as const,
      project_ids: [],
    };
  const saveScheduler = useMutation({
    mutationFn: () =>
      request<SchedulerRecord>("/settings", {
        method: "PUT",
        body: JSON.stringify(scheduler),
      }),
    onSuccess: (saved) => {
      setSchedulerDraft(saved);
      notify(t("saved"));
      void client.invalidateQueries({ queryKey: ["scheduler"] });
    },
    onError: () => notify(t("actionFailed")),
  });
  const importMutation = useMutation({
    mutationFn: () =>
      request<ImportSummary>("/import/legacy/run", {
        method: "POST",
      }),
    onSuccess: (summary) => {
      notify(
        `${t("importCompleted")}: ${summary.cars_created || 0} ${t("newCarsImported")}`,
      );
      void client.invalidateQueries({ queryKey: ["projects"] });
      void client.invalidateQueries({ queryKey: ["legacy-import"] });
    },
    onError: () => notify(t("importFailed")),
  });
  const backupMutation = useMutation({
    mutationFn: () => request("/desktop/backups", { method: "POST" }),
    onSuccess: () => {
      notify(t("backupCreated"));
      void client.invalidateQueries({ queryKey: ["desktop-data"] });
    },
    onError: () => notify(t("backupFailed")),
  });
  const restoreMutation = useMutation({
    mutationFn: (name: string) =>
      request(`/desktop/backups/${encodeURIComponent(name)}/restore`, {
        method: "POST",
      }),
    onSuccess: () => {
      notify(t("restoreRestartRequired"));
      void client.invalidateQueries({ queryKey: ["desktop-data"] });
    },
    onError: () => notify(t("restoreFailed")),
  });
  const migrationMutation = useMutation({
    mutationFn: () =>
      request<DesktopMigrationReport>("/desktop/migrations", {
        method: "POST",
        body: JSON.stringify({
          source_database_url: migrationUrl,
          source_storage_path: migrationStorage,
        }),
      }),
    onSuccess: () => {
      notify(t("migrationCompleted"));
      void client.invalidateQueries({ queryKey: ["desktop-data"] });
    },
    onError: () => notify(t("migrationFailed")),
  });
  const source = importMutation.data || importQuery.data;
  return (
    <>
      <div className="title-row">
        <div>
          <h1>{t("settings")}</h1>
          <p>{t("settingsSubtitle")}</p>
        </div>
      </div>
      <section className="panel settings">
        <div className="setting-row">
          <div>
            <h3>{t("schedulerTitle")}</h3>
            <p>{t("automaticHelp")}</p>
            <div className="scheduler-state">
              <span className={`source-state ${scheduler.enabled && !scheduler.paused ? "ready" : ""}`}>
                {scheduler.paused
                  ? t("schedulerPaused")
                  : scheduler.enabled
                    ? t("schedulerEnabled")
                    : t("schedulerDisabled")}
              </span>
              <span>
                {t("nextScheduledRun")}: {scheduler.enabled
                  ? formatDate(scheduler.next_run_at, locale)
                  : "—"}
              </span>
            </div>
          </div>
          <button
            className={`switch ${scheduler.enabled ? "on" : ""}`}
            onClick={() => setSchedulerDraft({ ...scheduler, enabled: !scheduler.enabled })}
          >
            <i />
          </button>
        </div>
        <label>
          {t("interval")}
          <select
            value={scheduler.interval_minutes}
            onChange={(event) =>
              setSchedulerDraft({
                ...scheduler,
                interval_minutes: Number(event.target.value) as SchedulerRecord["interval_minutes"],
              })
            }
          >
            {[60, 180, 360, 720, 1440].map((value) => (
              <option value={value} key={value}>
                {t(({
                  60: "hour1",
                  180: "hour3",
                  360: "hour6",
                  720: "hour12",
                  1440: "hour24",
                } as Record<number, Key>)[value])}
              </option>
            ))}
          </select>
        </label>
        <fieldset>
          <legend>{t("schedulerTitle")}</legend>
          <label>
            <input
              type="checkbox"
              checked={scheduler.paused}
              disabled={!scheduler.enabled}
              onChange={(event) =>
                setSchedulerDraft({ ...scheduler, paused: event.target.checked })
              }
            />
            <span>
              <b>{t("pauseScheduler")}</b>
              <small>{t("pauseSchedulerHelp")}</small>
            </span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={scheduler.catch_up_enabled}
              onChange={(event) =>
                setSchedulerDraft({
                  ...scheduler,
                  catch_up_enabled: event.target.checked,
                })
              }
            />
            <span>
              <b>{t("catchUpScans")}</b>
              <small>{t("catchUpScansHelp")}</small>
            </span>
          </label>
        </fieldset>
        <fieldset>
          <legend>{t("scheduledProjects")}</legend>
          {projects.map((project) => (
            <label key={project.id}>
              <input
                type="checkbox"
                checked={scheduler.project_ids.includes(project.id)}
                onChange={(event) =>
                  setSchedulerDraft({
                    ...scheduler,
                    project_ids: event.target.checked
                      ? [...scheduler.project_ids, project.id]
                      : scheduler.project_ids.filter((id) => id !== project.id),
                  })
                }
              />
              <span>
                <b>{project.name}</b>
                <small>{t(project.scan_mode === "FAST" ? "fast" : "accurate")}</small>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="setting-footer">
          <span>
            {scheduler.enabled && !scheduler.project_ids.length
              ? t("chooseAtLeastOneProject")
              : t("firstScanHelp")}
          </span>
          <Button
            kind="primary"
            disabled={scheduler.enabled && !scheduler.project_ids.length}
            onClick={() => saveScheduler.mutate()}
          >
            {t("saveSettings")}
          </Button>
        </div>
      </section>
      {isAdmin ? <section className="panel settings import-panel">
        <div className="setting-row">
          <div>
            <h3>{t("legacyImport")}</h3>
            <p>{t("legacyImportHelp")}</p>
          </div>
          <span className={`source-state ${source?.available ? "ready" : ""}`}>
            {source?.available ? t("sourceReady") : t("sourceUnavailable")}
          </span>
        </div>
        {source && (
          <dl className="import-summary">
            <div>
              <dt>{t("sourceFolder")}</dt>
              <dd>{source.path}</dd>
            </div>
            <div>
              <dt>{t("searchProjects")}</dt>
              <dd>{source.searches}</dd>
            </div>
            <div>
              <dt>{t("historyFiles")}</dt>
              <dd>{source.history_files}</dd>
            </div>
            <div>
              <dt>{t("uniqueCars")}</dt>
              <dd>{source.unique_cars}</dd>
            </div>
            <div>
              <dt>{t("screenshots")}</dt>
              <dd>{source.screenshots}</dd>
            </div>
          </dl>
        )}
        <div className="setting-footer">
          <span>{t("importIdempotent")}</span>
          <Button
            kind="primary"
            disabled={!source?.available || importMutation.isPending}
            onClick={() => importMutation.mutate()}
          >
            {importMutation.isPending ? t("importing") : t("importData")}
          </Button>
        </div>
      </section> : null}
      {isAdmin && desktopDataQuery.data ? (
        <section className="panel settings import-panel desktop-data-panel">
          <div className="setting-row">
            <div>
              <h3>{t("desktopData")}</h3>
              <p>{t("desktopDataHelp")}</p>
            </div>
            <Button
              kind="primary"
              disabled={backupMutation.isPending}
              onClick={() => backupMutation.mutate()}
            >
              {backupMutation.isPending ? t("backupCreating") : t("createBackup")}
            </Button>
          </div>
          <p className="data-directory">
            {t("dataFolder")}: <code>{desktopDataQuery.data.data_directory}</code>
          </p>
          {desktopDataQuery.data.restore_result ? (
            <div className={`restore-result ${desktopDataQuery.data.restore_result.status}`}>
              {t("lastRestore")}: {desktopDataQuery.data.restore_result.status}
            </div>
          ) : null}
          <div className="backup-list">
            {desktopDataQuery.data.backups.length ? (
              desktopDataQuery.data.backups.map((backup) => (
                <div key={backup.name}>
                  <span>
                    <b>{backup.name}</b>
                    <small>
                      {(backup.bytes / 1024 / 1024).toFixed(1)} MB · {formatDate(backup.updated_at, locale)}
                    </small>
                  </span>
                  <Button
                    disabled={restoreMutation.isPending}
                    onClick={() => {
                      if (window.confirm(t("restoreConfirm"))) {
                        restoreMutation.mutate(backup.name);
                      }
                    }}
                  >
                    {t("restoreBackup")}
                  </Button>
                </div>
              ))
            ) : (
              <p>{t("noBackups")}</p>
            )}
          </div>
          <details className="migration-wizard">
            <summary>{t("migrationWizard")}</summary>
            <p>{t("migrationWizardHelp")}</p>
            <label>
              {t("sourceDatabase")}
              <input
                type="password"
                value={migrationUrl}
                onChange={(event) => setMigrationUrl(event.target.value)}
              />
            </label>
            <label>
              {t("sourceStorage")}
              <input
                placeholder={t("sourceStoragePlaceholder")}
                value={migrationStorage}
                onChange={(event) => setMigrationStorage(event.target.value)}
              />
            </label>
            <div className="setting-footer">
              <span>{t("migrationReadOnly")}</span>
              <Button
                kind="primary"
                disabled={
                  migrationMutation.isPending ||
                  !migrationUrl.trim() ||
                  !migrationStorage.trim()
                }
                onClick={() => migrationMutation.mutate()}
              >
                {migrationMutation.isPending ? t("migrationRunning") : t("startMigration")}
              </Button>
            </div>
          </details>
        </section>
      ) : null}
    </>
  );
}

function ProjectModal({
  t,
  project,
  busy,
  close,
  save,
}: {
  t: Translate;
  project?: ProjectRecord;
  busy: boolean;
  close: () => void;
  save: (form: ProjectForm) => void;
}) {
  const [form, setForm] = useState<ProjectForm>({
    name: project?.name || "",
    search_url: project?.search_url || "",
    telegram_url: project?.telegram_url || "",
    scan_mode: project?.scan_mode || "FAST",
    search_page_mode: project?.search_page_mode || "FIRST_PAGE",
    auto_update: project?.auto_update ?? true,
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(form);
  };
  return (
    <div className="modal-backdrop" role="presentation">
      <form className="modal" onSubmit={submit}>
        <div className="modal-head">
          <h2>{project ? t("editProject") : t("createProject")}</h2>
          <button type="button" onClick={close}>
            ×
          </button>
        </div>
        <label>
          {t("projectName")}
          <input
            required
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
          />
        </label>
        <label>
          {t("encarSearchUrl")}
          <textarea
            required
            value={form.search_url}
            onChange={(event) => setForm({ ...form, search_url: event.target.value })}
          />
        </label>
        <label>
          {t("telegramChannelUrl")}
          <input
            type="url"
            value={form.telegram_url}
            placeholder="https://t.me/channel"
            onChange={(event) => setForm({ ...form, telegram_url: event.target.value })}
          />
        </label>
        <label>
          {t("scanMode")}
          <select
            value={form.scan_mode}
            onChange={(event) =>
              setForm({ ...form, scan_mode: event.target.value as "FAST" | "ACCURATE" })
            }
          >
            <option value="FAST">{t("fast")}</option>
            <option value="ACCURATE">{t("accurate")}</option>
          </select>
          <small className="mode-help">
            {t(form.scan_mode === "FAST" ? "fastModeHelp" : "accurateModeHelp")}
          </small>
        </label>
        <label>
          {t("searchDepth")}
          <select
            value={form.search_page_mode}
            onChange={(event) =>
              setForm({
                ...form,
                search_page_mode: event.target.value as "FIRST_PAGE" | "ALL_PAGES",
              })
            }
          >
            <option value="FIRST_PAGE">{t("firstPageOnly")}</option>
            <option value="ALL_PAGES">{t("allPages")}</option>
          </select>
          <small className="mode-help">
            {t(
              form.search_page_mode === "ALL_PAGES"
                ? "allPagesHelp"
                : "firstPageHelp",
            )}
          </small>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={form.auto_update}
            onChange={(event) => setForm({ ...form, auto_update: event.target.checked })}
          />
          {t("automaticUpdates")}
        </label>
        <div className="modal-actions">
          <Button onClick={close}>{t("cancel")}</Button>
          <Button kind="primary" type="submit" disabled={busy}>
            {t("saveChanges")}
          </Button>
        </div>
      </form>
    </div>
  );
}

function ConfirmModal({
  t,
  project,
  busy,
  close,
  confirm,
}: {
  t: Translate;
  project: ProjectRecord;
  busy: boolean;
  close: () => void;
  confirm: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="modal confirm-modal">
        <h2>{t("deleteProject")}</h2>
        <p>
          {t("deleteProjectQuestion")} <b>{project.name}</b>?
        </p>
        <p className="warning-text">{t("deletePermanentHelp")}</p>
        <div className="modal-actions">
          <Button onClick={close}>{t("cancel")}</Button>
          <Button kind="danger" disabled={busy} onClick={confirm}>
            {t("deletePermanently")}
          </Button>
        </div>
      </section>
    </div>
  );
}

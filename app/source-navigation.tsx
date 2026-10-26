import type { Locale } from "./translations";
import { workspaceUrl, type SourceKey, type View } from "./source-workspace";
import { sourceName, sourceGroups, useSourceCatalog, type SourceCatalog } from "./source-catalog";
import { reasonText, type LaunchOptions } from "./launch-dialog";
import { activityProjects, statusLabel, type ActivityRun } from "./activity-dialog";
import { latestProjectUpdate } from "./scan-progress";

export type SearchAvailability = { can_search: boolean; reason: string | null };
export function sourceAvailability(options: LaunchOptions | undefined, source: SourceKey, failed = false): SearchAvailability {
  if (failed) return { can_search: false, reason: "AVAILABILITY_ERROR" };
  if (!options) return { can_search: false, reason: "AVAILABILITY_PENDING" };
  return options.sources.find((item) => item.source_key === source)
    || { can_search: false, reason: "SOURCE_NOT_CONNECTED" };
}

export function SourceAvailabilityNotice({ source, availability, locale, retry, settings, detailed = false }: {
  source: SourceKey; availability: SearchAvailability; locale: Locale;
  retry: () => void; settings: () => void; detailed?: boolean;
}) {
  const uk = locale === "uk";
  const catalog = useSourceCatalog();
  const name = sourceName(source, catalog);
  if (availability.can_search && !detailed) return null;
  const pending = availability.reason === "AVAILABILITY_PENDING";
  const failed = availability.reason === "AVAILABILITY_ERROR";
  return <section className="panel source-availability" role={failed ? "alert" : "status"} aria-label={`${name}: ${uk ? "доступність пошуку" : "доступность поиска"}`}>
    <div><h2>{availability.can_search ? (uk ? "Модуль пошуку підключено" : "Модуль поиска подключён")
      : availability.reason === "SOURCE_NOT_CONNECTED" ? (uk ? `Пошук ${name} ще не підключено` : `Поиск ${name} ещё не подключён`)
      : reasonText(availability.reason, locale)}</h2>
      {!pending && availability.reason !== "INVALID_SOURCE_URL" && <p>{uk ? "Стан модуля та ліцензії — не перевірка доступу до сайту в мережі." : "Состояние модуля и лицензии — не проверка доступа к сайту в сети."}</p>}
      {!availability.can_search && !pending && <p>{uk ? "Новий пошук недоступний. Збережені дані та історія не змінюються; активний запуск можна скасувати." : "Новый поиск недоступен. Сохранённые данные и история не меняются; активный запуск можно отменить."}</p>}
    </div>
    <div className="source-settings-actions">
      <button type="button" className="button" disabled={pending} onClick={retry}>{uk ? "Перевірити знову" : "Проверить снова"}</button>
      {availability.reason?.startsWith("LICENSE_") && <button type="button" className="button" onClick={settings}>{uk ? "Налаштування застосунку" : "Настройки приложения"}</button>}
    </div>
  </section>;
}

export function SourceNavigation({ source, view, locale, navigate, selectSource, options, failed, runs = [], activityFailed = false, activityPending = false, catalog: providedCatalog }: {
  source: SourceKey; view: View; locale: Locale;
  navigate: (view: View) => void; selectSource: (source: SourceKey) => void;
  options?: LaunchOptions; failed?: boolean;
  catalog?: SourceCatalog;
  runs?: ActivityRun[]; activityFailed?: boolean; activityPending?: boolean;
}) {
  const uk = locale === "uk";
  const inheritedCatalog = useSourceCatalog();
  const catalog = providedCatalog ?? inheritedCatalog;
  const sections = [
    { view: "projects" as const, label: uk ? "Проєкти" : "Проекты", path: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" },
    { view: "scans" as const, label: uk ? "Запуски" : "Запуски", path: "m9 5 10 7-10 7V5Z" },
    { view: "source-settings" as const, label: uk ? "Налаштування" : "Настройки", path: "M4 7h16M4 17h16M8 4v6M16 14v6" },
  ];
  return <nav className="source-nav" aria-label={uk ? "Робочі області сайтів" : "Рабочие области сайтов"}>
    {sourceGroups(catalog).map((group) => <div className="source-nav-group" key={group.family}>
      <p className="source-group-label">{group.family === "MARKETPLACE" ? (uk ? "Майданчики" : "Площадки") : (uk ? "Аукціони" : "Аукционы")}</p>
      {group.sites.map(({ key, name }) => { const availability = sourceAvailability(options, key, failed);
        const projects = options?.projects || [];
        const ids = [...new Set(runs.flatMap((run) => activityProjects(run, projects)).filter((p) => p.source_key === key).map((p) => p.id))];
        const statuses = ids.map((id) => latestProjectUpdate(runs, id, projects)?.status);
        // Availability can arrive before the active-run read. Preserve that
        // queued/running evidence instead of briefly claiming the source is idle.
        statuses.push(...projects.filter((p) => p.source_key === key && !ids.includes(p.id) && p.active_status).map((p) => p.active_status!));
        const state = statuses.includes("RUNNING") ? "running"
          : statuses.some((s) => ["QUEUED", "WAITING_FOR_USER", "CANCEL_REQUESTED"].includes(s || "")) ? "waiting" : "idle";
        const waitingStatus = statuses.includes("WAITING_FOR_USER") ? "WAITING_FOR_USER" : statuses.includes("CANCEL_REQUESTED") ? "CANCEL_REQUESTED" : "QUEUED";
        const uncertain = activityFailed || activityPending;
        const label = activityFailed ? (uk ? "Стан оновлення невідомий" : "Состояние обновления неизвестно")
          : state !== "idle" ? statusLabel(state === "running" ? "RUNNING" : waitingStatus, uk)
          : uncertain ? (uk ? "Стан оновлення невідомий" : "Состояние обновления неизвестно")
          : availability.can_search ? (uk ? "Немає активних оновлень" : "Нет активных обновлений") : reasonText(availability.reason, locale);
        const dotClass = activityFailed ? "" : state !== "idle" ? `source-${state}` : availability.can_search && !uncertain ? "source-ready" : "";
        return <div className="source-nav-site" key={key}>
        <button type="button" className={`source-button ${key === source ? "selected" : ""}`} onClick={() => selectSource(key)}
          aria-label={`${name}: ${label}`} aria-expanded={key === source} aria-controls={key === source ? `source-${key}-sections` : undefined}>
          <span className="source-monogram" aria-hidden="true">{name[0]}</span>
          <span className="nav-label">{name}</span>
          <span className={`source-pending ${dotClass}`} role="img" aria-label={`${name}: ${label}`} title={label}>{dotClass ? "●" : "○"}</span>
        </button>
        {key === source && <div className="source-sections" id={`source-${key}-sections`}>
          {sections.map((section) => {
            const active = view === section.view || section.view === "projects" && ["project", "car", "favorites"].includes(view);
            return <button key={section.view} type="button" className={active ? "active" : ""} aria-current={active ? "page" : undefined}
              onClick={() => navigate(section.view)}>
              <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d={section.path} /></svg>
              <span className="nav-label">{section.label}</span>
            </button>;
          })}
        </div>}
      </div>; })}
    </div>)}
    <button type="button" className={`application-settings ${view === "settings" ? "active" : ""}`} aria-current={view === "settings" ? "page" : undefined}
      onClick={() => navigate("settings")}>
      <svg className="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M4 17h16M8 4v6M16 14v6" /></svg>
      <span className="nav-label">{uk ? "Налаштування застосунку" : "Настройки приложения"}</span>
    </button>
  </nav>;
}

export function SourceContextMismatch({ source, projectId, carId, locale }: { source: SourceKey; projectId: number | null; carId?: number; locale: Locale }) {
  const uk = locale === "uk";
  const name = sourceName(source, useSourceCatalog());
  return <section className="panel source-unavailable"><h2>{uk ? "Інший сайт" : "Другой сайт"}</h2>
    <p>{uk ? `Ці дані належать ${name}. Відкрийте відповідну робочу область.` : `Эти данные относятся к ${name}. Откройте соответствующую рабочую область.`}</p>
    <a className="button" href={workspaceUrl("", { source, view: carId ? "car" : "project", projectId, carId: carId || null })}>{uk ? "Перейти до" : "Перейти к"} {name}</a>
  </section>;
}

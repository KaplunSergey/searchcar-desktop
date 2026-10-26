import { useEffect, useId, useState, type ReactNode } from "react";
import { AuctionRefresh } from "./auction-refresh";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import type { Locale } from "./translations";

type Project = SourceWorkspaceProps["projects"][number];
type ProjectSort = "updated" | "name";
const dateValue = (value?: string | null) => {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date : null;
};

export function sortedAuctionProjects(projects: Project[], sort: ProjectSort, locale: Locale) {
  return [...projects].sort((a, b) => (sort === "updated"
    ? (dateValue(b.last_refreshed_at)?.getTime() ?? -Infinity) - (dateValue(a.last_refreshed_at)?.getTime() ?? -Infinity) : 0)
    || a.name.localeCompare(b.name, locale, { numeric: true }) || a.id - b.id);
}

function storedPreference(key: string) {
  try { return typeof window === "undefined" ? null : window.localStorage.getItem(key); }
  catch { return null; }
}

export function AuctionProjects({ source, locale, projects, loading, failed, showProjects, canRefresh, read, refresh,
  open, renderStatus, renderRefresh, renderEdit, renderDelete, readState }: {
  source: string; locale: Locale; projects: Project[]; loading: boolean; failed: boolean; showProjects: boolean;
  canRefresh: boolean; read: SourceWorkspaceProps["read"]; refresh?: SourceWorkspaceProps["refreshProjects"];
  open: (id: number) => void; renderStatus: (id: number) => ReactNode; renderRefresh?: (id: number) => ReactNode; renderEdit?: (id: number) => ReactNode; renderDelete?: (id: number) => ReactNode; readState: ReactNode;
}) {
  const uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const viewKey = `${source}-auction-projects-view`, sortKey = `${source}-auction-projects-sort`;
  const [grid, setGrid] = useState(() => storedPreference(viewKey) === "grid");
  const [sort, setSort] = useState<ProjectSort>(() => storedPreference(sortKey) === "name" ? "name" : "updated");
  const [collapsed, setCollapsed] = useState(false);
  const contentId = useId(), hidden = collapsed || !showProjects;
  useEffect(() => {
    try { window.localStorage.setItem(viewKey, grid ? "grid" : "list"); } catch { /* Keep in-session controls usable. */ }
  }, [viewKey, grid]);
  useEffect(() => {
    try { window.localStorage.setItem(sortKey, sort); } catch { /* Keep in-session controls usable. */ }
  }, [sortKey, sort]);
  const empty = !loading && !failed && !projects.length && <div className="empty-state">{l("Сохранённых проектов нет", "Збережених проєктів немає")}</div>;
  const header = <>
    <strong className="projects-panel-title">{l("Проекты", "Проєкти")}: {loading || failed ? "—" : projects.length}
      {` · ${l("Авто", "Авто")}: ${loading || failed ? "—" : projects.reduce((total, project) => total + project.cars, 0)}`}</strong>
    <div className="projects-toolbar-controls" aria-hidden={hidden} inert={hidden}>
      <button type="button" className={!grid ? "chosen" : ""} aria-label={l("Список", "Список")} aria-pressed={!grid} onClick={() => setGrid(false)}>☷</button>
      <button type="button" className={grid ? "chosen" : ""} aria-label={l("Плитка", "Плитка")} aria-pressed={grid} onClick={() => setGrid(true)}>▦</button>
      <select aria-label={l("Сортировка проектов", "Сортування проєктів")} value={sort} onChange={(event) => setSort(event.target.value as ProjectSort)}>
        <option value="updated">{l("Недавно обновлены", "Нещодавно оновлені")}</option><option value="name">{l("По названию", "За назвою")}</option>
      </select>
    </div>
    {showProjects && <button type="button" className="projects-collapse-toggle" aria-expanded={!collapsed} aria-controls={contentId}
      aria-label={collapsed ? l("Развернуть проекты", "Розгорнути проєкти") : l("Свернуть проекты", "Згорнути проєкти")} onClick={() => setCollapsed(!collapsed)}>
      <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg>
    </button>}
  </>;
  return <section className={`panel projects-panel auction-projects-panel${hidden ? " collapsed" : ""}`}>
    {refresh ? <AuctionRefresh source={source} locale={locale} projects={projects} disabled={!canRefresh || loading || failed} read={read} refresh={refresh}>{header}</AuctionRefresh>
      : <div className="projects-panel-header auction-project-toolbar">{header}</div>}
    {!showProjects && <>{readState}{empty}</>}
    <div id={contentId} className="projects-collapsible-content" inert={hidden} aria-hidden={hidden}>
      <div className="projects-collapsible-inner">{showProjects && <>{readState}{empty}</>}
        <div className={grid ? "project-grid" : "project-list"}>{sortedAuctionProjects(projects, sort, locale).map((project) => {
          const date = dateValue(project.last_refreshed_at);
          return <article className="project-row auction-project" key={project.id}>
            <button type="button" className="project-name auction-project-name" onClick={() => open(project.id)}><b>{project.name}</b></button>
            <div className="count"><span>{l("Авто", "Авто")}:</span><b>{project.cars}</b></div>
            <div className="project-search-badges">{project.search_page_mode && <span className="badge project-page-badge">{project.search_page_mode === "ALL_PAGES" ? l("Все стр.", "Усі стор.") : l("1-я стр.", "1-ша стор.")}</span>}</div>
            <div className="auction-project-state">{renderStatus(project.id)}</div>
            <div className="project-updated auction-project-date"><small>{l("Обновлён", "Оновлено")}</small>{date
              ? <time dateTime={project.last_refreshed_at!}>{new Intl.DateTimeFormat(uk ? "uk-UA" : "ru-RU", { dateStyle: "short", timeStyle: "short" }).format(date)}</time>
              : <span>{l("Ещё не обновлялся", "Ще не оновлювався")}</span>}</div>
            <div className="project-actions auction-project-actions">{renderRefresh?.(project.id)}{renderEdit?.(project.id)}{renderDelete?.(project.id)}</div>
          </article>;
        })}</div>
      </div>
    </div>
  </section>;
}

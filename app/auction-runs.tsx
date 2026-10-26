import { useEffect, useRef, useState, type ReactNode } from "react";
import { skipToken, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { activityProjects, SiteVerificationNotice, statusLabel } from "./activity-dialog";
import { latestProjectUpdate, ProjectUpdateStatus, updateActive } from "./scan-progress";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import { AuctionHistoryReport } from "./auction-report";
import type { AuctionSnapshotView } from "./auction-time-client";
export { auctionReportChanges } from "./auction-report";

type Run = SourceWorkspaceProps["runs"][number];
type Project = SourceWorkspaceProps["projects"][number];
type Filters = { project: string; state: string };
type HistoryUi = { filters: Filters; expanded: Record<string, boolean>; changes: Record<string, string>; tabs: Record<string, string> };
const defaults: Filters = { project: "all", state: "all" };
const emptyHistoryUi: HistoryUi = { filters: defaults, expanded: {}, changes: {}, tabs: {} };
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
const sumKnown = (values: (number | null)[]) => values.length && values.every(value => value !== null) ? values.reduce<number>((sum, value) => sum + value!, 0) : null;

export function auctionRunSummary(run: Run, source: string, projects: Project[], projectFilter = "all") {
  const own = activityProjects(run, projects).filter(project => project.source_key === source && (projectFilter === "all" || String(project.id) === projectFilter))
    .map(project => ({ ...project, status: latestProjectUpdate([run], project.id, projects)?.status || project.status }));
  const states = own.map(project => project.status);
  const active = ["RUNNING", "WAITING_FOR_USER", "CANCEL_REQUESTED", "QUEUED", "ACTIVE"].find(status => states.includes(status));
  const status = active || (states.length ? (states.every(state => state === states[0]) ? states[0] : "PARTIAL")
    : run.source_key === source ? run.status : "UNKNOWN");
  const pages = own.map(project => run.payload?.pagination?.[String(project.id)]);
  const visited = sumKnown(pages.map(info => count(info?.pages_visited)));
  const target = sumKnown(pages.map(info => info?.mode === "FIRST_PAGE" ? 1 : count(info?.expected_pages) ?? count(info?.total_pages)));
  const ads = sumKnown(pages.map(info => count(info?.ads_seen)));
  const percent = status === "RUNNING" && visited !== null && target !== null && target > 0
    ? Math.min(100, Math.round(100 * visited / target)) : null;
  const ids = new Set(own.map(project => project.id));
  const report = (run.payload?.report || []).flatMap(item => {
    if (!own.length && run.source_key === source && item.source_key === source) return [item];
    const matching = [...new Set([item.project_id, ...(item.project_ids || [])])].filter(id => ids.has(id));
    return matching.length && (!item.source_key || item.source_key === source)
      ? [{ ...item, project_id: matching[0], project_ids: matching }] : [];
  });
  return { projects: own, status, visited, target, ads, percent, report, firstPage: pages.length > 0 && pages.every(info => info?.mode === "FIRST_PAGE") };
}

export function filterAuctionRuns(runs: Run[], source: string, projects: Project[], filters: Filters) {
  return [...runs].sort((a, b) => b.id - a.id).filter(run => {
    const summary = auctionRunSummary(run, source, projects, filters.project);
    if (!summary.projects.length && !(filters.project === "all" && run.source_key === source)) return false;
    return filters.state === "all" || (filters.state === "active" ? updateActive(summary.status)
      : filters.state === "completed" ? summary.status === "SUCCEEDED" : !updateActive(summary.status) && summary.status !== "SUCCEEDED");
  });
}

export function AuctionRuns({ source, locale, runs, projects, runsLoading, runsError, retryRuns, sourceTestMode, write, read, navigate, renderNotice, renderSnapshot }: Pick<SourceWorkspaceProps,
  "source" | "locale" | "runs" | "projects" | "runsLoading" | "runsError" | "retryRuns" | "sourceTestMode" | "write" | "read" | "navigate"> & {
    renderNotice: (run: Run, projectId: number) => ReactNode;
    renderSnapshot: (view: AuctionSnapshotView, recordedAt: string) => ReactNode;
  }) {
  const uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const client = useQueryClient();
  // Keep report state across internal remounts; sidebar entry and logout clear it.
  const uiKey = ["auction-history-ui", source];
  const { data: ui = emptyHistoryUi } = useQuery<HistoryUi>({ queryKey: uiKey, queryFn: skipToken, gcTime: Infinity, initialData: emptyHistoryUi });
  const updateUi = (patch: Partial<HistoryUi>) => client.setQueryData<HistoryUi>(uiKey, previous => ({ ...previous!, ...patch }));
  const [cancelTarget, setCancelTarget] = useState<{ id: number; source: string } | null>(null);
  const cancelId = cancelTarget?.id ?? null;
  const [message, setMessage] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const cancelOrigin = useRef<HTMLElement | null>(null);
  const projectControl = useRef<HTMLSelectElement>(null);
  const filters = ui.filters;
  const setFilters = (patch: Partial<Filters>) => updateUi({ filters: { ...filters, ...patch } });
  const visible = filterAuctionRuns(runs, source, projects, filters);
  const ownRuns = filterAuctionRuns(runs, source, projects, defaults);
  const projectOptions = new Map(ownRuns.flatMap(run => auctionRunSummary(run, source, projects).projects).map(project => [project.id, project.name]));
  const target = runs.find(run => run.id === cancelId);
  const cancellable = cancelTarget?.source === source && !!target && ["RUNNING", "QUEUED"].includes(target.status)
    && auctionRunSummary(target, source, projects).projects.some(project => ["RUNNING", "QUEUED", "WAITING_FOR_USER"].includes(project.status));
  const cancel = useMutation({ mutationFn: async () => {
    if (!write || !target || !cancellable || runsError || runsLoading) throw new Error("Cancellation unavailable");
    await write(`/scans/${target.id}/cancel`, { source_keys: [cancelTarget!.source] });
  }, onSuccess: () => {
    setCancelTarget(null); setMessage(l("Команда отмены принята. Сохранённые результаты останутся; другие сайты не затронуты.", "Команду скасування прийнято. Збережені результати залишаться; інші сайти не зміняться."));
    for (const key of [["scans"], ["launch-options"]]) void client.invalidateQueries({ queryKey: key });
    retryRuns();
  } });
  useEffect(() => {
    const modal = dialog.current, fallback = projectControl.current;
    if (cancelId !== null) modal?.showModal();
    return () => { modal?.close();
      if (cancelOrigin.current?.isConnected && !cancelOrigin.current.matches(":disabled")) cancelOrigin.current.focus();
      else fallback?.focus(); };
  }, [cancelId]);
  const reset = () => { setFilters(defaults); projectControl.current?.focus(); };
  return <section className="auction-history" aria-label={l("Журнал поиска", "Журнал пошуку")}>
    <div className="auction-history-filters"><label>{l("Проект", "Проєкт")}<select ref={projectControl} aria-label={l("Проект", "Проєкт")} value={filters.project} onChange={event => setFilters({ project: event.target.value })}>
      <option value="all">{l("Все проекты", "Усі проєкти")}</option>{[...projectOptions].map(([id, name]) => <option key={id} value={String(id)}>{name}</option>)}</select></label>
      <label>{l("Состояние поиска", "Стан пошуку")}<select aria-label={l("Состояние поиска", "Стан пошуку")} value={filters.state} onChange={event => setFilters({ state: event.target.value })}>
        <option value="all">{l("Все состояния", "Усі стани")}</option><option value="active">{l("В процессе", "У процесі")}</option>
        <option value="completed">{l("Завершено", "Завершено")}</option><option value="attention">{l("Требует внимания", "Потребує уваги")}</option></select></label>
      {(filters.project !== "all" || filters.state !== "all") && <button className="button" type="button" onClick={reset}>{l("Сбросить фильтры", "Скинути фільтри")}</button>}
      <small>{l("Запусков", "Запусків")}: {visible.length} / {ownRuns.length}</small></div>
    {message && <p className="activity-notice" role="status">{message}</p>}
    {runsError ? <section className="panel" role="alert"><p>{l("Не удалось обновить историю. Действия недоступны до повторной проверки.", "Не вдалося оновити історію. Дії недоступні до повторної перевірки.")}</p><button className="button" onClick={retryRuns}>{l("Повторить", "Повторити")}</button></section>
      : runsLoading ? <p role="status">{l("Загрузка истории…", "Завантаження історії…")}</p>
      : !ownRuns.length ? <section className="panel">{l("Сохранённых запусков нет", "Збережених запусків немає")}</section>
      : !visible.length ? <section className="panel"><p>{l("Нет запусков по выбранным фильтрам.", "Немає запусків за вибраними фільтрами.")}</p><button className="button" onClick={reset}>{l("Сбросить фильтры", "Скинути фільтри")}</button></section>
      : visible.map(run => {
        const summary = auctionRunSummary(run, source, projects, filters.project);
        const key = `${source}-${run.id}`, open = !!ui.expanded[key], tab = ui.tabs[key] || "changes";
        const tone = summary.status === "SUCCEEDED" ? "success" : updateActive(summary.status) ? "active" : summary.status === "FAILED" ? "error" : "warning";
        const date = new Date(run.created_at);
        const coverage = `${summary.visited ?? "—"} ${l("из", "із")} ${summary.target ?? "—"}${summary.firstPage ? l(" · выбранный охват", " · вибране охоплення") : l(" · страниц", " · сторінок")}`;
        return <article className={`auction-history-run ${tone}`} key={key} data-run-id={run.id}>
          <header className="auction-history-head"><span className={`auction-history-icon ${tone}`} aria-hidden="true">{summary.status === "SUCCEEDED" ? "✓" : updateActive(summary.status) ? "◷" : "!"}</span>
            <div className="auction-history-info"><h2>№ {run.id} · {Number.isFinite(date.getTime()) ? <time dateTime={run.created_at}>{date.toLocaleString(uk ? "uk-UA" : "ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</time> : l("Дата неизвестна", "Дата невідома")}</h2>
              <p>{summary.projects.map(project => project.name).join(" · ") || (run.kind === "CARS" ? l("Обновление автомобилей", "Оновлення автомобілів") : l("Проекты не указаны", "Проєкти не вказано"))} · {run.payload?.trigger === "MANUAL" ? l("Ручной поиск", "Ручний пошук") : run.payload?.trigger === "AUTOMATIC" ? l("Планировщик", "Планувальник") : l("Тип запуска не указан", "Тип запуску не вказано")}</p>
              <p>{coverage} · {l("Лотов", "Лотів")}: {summary.ads ?? "—"}</p></div>
            <div className="auction-history-phase"><span className="project-update" data-status={summary.status}>{statusLabel(summary.status, uk)}</span>
              {summary.status === "RUNNING" && <div className="auction-history-progress"><progress aria-label={l("Прогресс поиска", "Прогрес пошуку")} value={summary.percent ?? undefined} max={100} />{summary.percent !== null && <b>{summary.percent}%</b>}</div>}
              <small>Telegram: {sourceTestMode ? l("выключен", "вимкнено") : l("отчёт недоступен", "звіт недоступний")}</small></div>
            <div className="auction-history-actions">{sourceTestMode && write && run.kind === "PROJECTS" && ["RUNNING", "QUEUED"].includes(run.status)
              && summary.projects.some(project => ["RUNNING", "QUEUED", "WAITING_FOR_USER"].includes(project.status)) && <button className="button danger" onClick={event => {
              cancelOrigin.current = event.currentTarget; cancel.reset(); setCancelTarget({ id: run.id, source });
            }}>{l("Отменить поиск", "Скасувати пошук")}</button>}
              <button className="button" type="button" aria-expanded={open} aria-controls={`history-${key}`} onClick={() => updateUi({ expanded: { ...ui.expanded, [key]: !open } })}>{open ? l("Свернуть", "Згорнути") : l("Подробнее", "Докладніше")}</button></div>
          </header>
          {open && <section className="auction-history-body" id={`history-${key}`}>
            {summary.projects.map(project => <section className="auction-run-project" key={project.id}><div className="auction-history-project"><h3>{project.name}</h3><ProjectUpdateStatus projectId={project.id} status={project.status} locale={locale} />
              {projects.some(current => current.id === project.id && current.source_key === source) && <button className="button" onClick={() => navigate("project", project.id)}>{l("Открыть проект", "Відкрити проєкт")}</button>}</div>
              <SiteVerificationNotice run={run} projectId={project.id} locale={locale} />
              {renderNotice({ ...run, status: project.status }, project.id)}
              {run.payload?.failures?.some(failure => failure.project_id === project.id) && <p className="launch-error">{l("При обновлении проекта возникла ошибка. Прежние данные сохранены.", "Під час оновлення проєкту виникла помилка. Попередні дані збережено.")}</p>}
            </section>)}
            <div className="auction-tabs" role="group" aria-label={l("Разделы отчёта", "Розділи звіту")}><button aria-pressed={tab === "changes"} onClick={() => updateUi({ tabs: { ...ui.tabs, [key]: "changes" } })}>{l("Изменения", "Зміни")} ({summary.report.length})</button>
              <button aria-pressed={tab === "telegram"} onClick={() => updateUi({ tabs: { ...ui.tabs, [key]: "telegram" } })}>Telegram</button></div>
            {tab === "telegram" ? <p className="auction-history-empty">{sourceTestMode ? l("Telegram выключен в тестовом профиле. Отправка сообщений не выполнялась.", "Telegram вимкнено в тестовому профілі. Надсилання повідомлень не виконувалося.") : l("Отчёт доставки недоступен. Поиск и доставка сообщений — независимые процессы.", "Звіт доставки недоступний. Пошук і доставка повідомлень — незалежні процеси.")}</p>
              : <AuctionHistoryReport key={`${key}:${filters.project}`} source={source} scanId={run.id} read={read} renderSnapshot={renderSnapshot}
                report={summary.report} filter={ui.changes[key] || "all"} setFilter={value => updateUi({ changes: { ...ui.changes, [key]: value } })} uk={uk} />}
          </section>}
        </article>;
      })}
    {cancelId !== null && <dialog ref={dialog} className="launch-dialog auction-history-cancel" aria-labelledby="history-cancel-title" aria-describedby="history-cancel-description" onCancel={event => { event.preventDefault(); if (!cancel.isPending) setCancelTarget(null); }}>
      <h2 id="history-cancel-title">{l("Отменить поиск", "Скасувати пошук")} #{cancelId} · {cancelTarget?.source === "iaa" ? "IAA" : "Copart"}?</h2>
      <p id="history-cancel-description">{l("Отмена затронет только проекты этого сайта в выбранном запуске, в том числе скрытые фильтром. Прочитанные данные сохранятся. Другие сайты продолжат работу.", "Скасування стосується лише проєктів цього сайту у вибраному запуску, зокрема прихованих фільтром. Прочитані дані збережуться. Інші сайти продовжать роботу.")}</p>
      {!cancellable && <p role="status">{l("Состояние изменилось. Обновите историю перед отменой.", "Стан змінився. Оновіть історію перед скасуванням.")}</p>}
      {cancel.isError && <p role="alert" className="launch-error">{l("Отмена не подтверждена. Проверьте актуальный статус перед повторной попыткой.", "Скасування не підтверджено. Перевірте актуальний стан перед повторною спробою.")}</p>}
      <div className="launch-actions"><button className="button" autoFocus disabled={cancel.isPending} onClick={() => setCancelTarget(null)}>{l("Не отменять", "Не скасовувати")}</button>
        <button className="button danger" disabled={!cancellable || runsError || runsLoading || cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? l("Отправляем…", "Надсилаємо…") : l("Подтвердить отмену", "Підтвердити скасування")}</button></div>
    </dialog>}
  </section>;
}

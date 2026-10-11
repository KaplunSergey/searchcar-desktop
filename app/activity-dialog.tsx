import { useEffect, useRef, useState, type RefObject } from "react";
import type { Locale } from "./translations";
import { SITE_NAMES, type SourceKey } from "./source-workspace";

export type ActivityRun = {
  id: number; kind: string; status: string; progress: number;
  payload?: {
    project_ids?: number[]; project_id?: number; car_ids?: number[];
    current_project_id?: number | null; cancelled_project_ids?: number[];
    project_statuses?: Record<string, string>;
    project_plans?: { project_id: number; source_key: string; name?: string }[];
  };
};
export type ActivityProject = { id: number; name: string; source_key: string };
export type CancellationTarget = { scanId: number } & (
  { scope: "RUN" } | { scope: "SITE"; sourceKey: string } | { scope: "PROJECT"; projectId: number }
);
const active = (status: string) => status === "RUNNING" || status === "QUEUED";
const siteName = (key: string) => SITE_NAMES[key as SourceKey] || key;

export function cancellationBody(target: CancellationTarget) {
  if (target.scope === "PROJECT") return { project_ids: [target.projectId] };
  if (target.scope === "SITE") return { source_keys: [target.sourceKey] };
  return { all_projects: true };
}

export function activityProjects(run: ActivityRun, projects: ActivityProject[]) {
  const payload = run.payload || {};
  return (payload.project_ids || (payload.project_id ? [payload.project_id] : [])).map((id) => {
    const plan = payload.project_plans?.find((item) => item.project_id === id);
    const project = projects.find((item) => item.id === id);
    let status = payload.project_statuses?.[String(id)]
      || (run.status === "RUNNING" && payload.current_project_id === id ? "RUNNING" : "QUEUED");
    if (payload.cancelled_project_ids?.includes(id) && status !== "CANCEL_REQUESTED") status = "CANCELLED";
    if (active(status) && (!active(run.status))) status = run.status;
    return { id, name: plan?.name || project?.name || `#${id}`, source_key: plan?.source_key || project?.source_key || "", status };
  });
}

function statusLabel(status: string, uk: boolean) {
  const labels: Record<string, string> = {
    QUEUED: uk ? "У черзі" : "В очереди", RUNNING: uk ? "Оновлюється" : "Обновляется",
    CANCEL_REQUESTED: uk ? "Скасування запитано" : "Отмена запрошена", CANCELLED: uk ? "Скасовано" : "Отменено",
    SUCCEEDED: uk ? "Завершено" : "Завершено", PARTIAL: uk ? "Частковий результат" : "Частичный результат",
    FAILED: uk ? "Помилка" : "Ошибка", INTERRUPTED: uk ? "Перервано" : "Прервано",
    INTERRUPTED_SLEEP: uk ? "Перервано через сон" : "Прервано из-за сна",
  };
  return labels[status] || (uk ? "Стан невідомий" : "Состояние неизвестно");
}

export function ActivityDialog({ locale, runs, projects, loading, failed, retry, cancel, close, returnFocus }: {
  locale: Locale; runs: ActivityRun[]; projects: ActivityProject[]; loading: boolean; failed: boolean;
  retry: () => void; cancel: (target: CancellationTarget) => Promise<unknown>; close: () => void;
  returnFocus?: RefObject<HTMLButtonElement | null>;
}) {
  const uk = locale === "uk";
  const dialog = useRef<HTMLDialogElement>(null);
  const notice = useRef<HTMLParagraphElement>(null);
  const confirmationHeading = useRef<HTMLHeadingElement>(null);
  const actionOrigin = useRef<HTMLElement | null>(null);
  const busy = useRef(false);
  const [pending, setPending] = useState(false);
  const [confirmation, setConfirmation] = useState<{ target: CancellationTarget; label: string } | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => { dialog.current?.showModal(); return () => { dialog.current?.close(); returnFocus?.current?.focus(); }; }, [returnFocus]);
  useEffect(() => { if (message) notice.current?.focus(); }, [message]);
  useEffect(() => { if (confirmation) confirmationHeading.current?.focus(); }, [confirmation]);
  const targetRun = runs.find((run) => run.id === confirmation?.target.scanId);
  const targetProjects = targetRun ? activityProjects(targetRun, projects) : [];
  const target = confirmation?.target;
  const cancellable = !!targetRun && active(targetRun.status) && (target?.scope === "RUN"
    || target?.scope === "PROJECT" && targetProjects.some((project) => project.id === target.projectId && active(project.status))
    || target?.scope === "SITE" && targetProjects.some((project) => project.source_key === target.sourceKey && active(project.status)));
  const confirm = async () => {
    if (!confirmation || !cancellable || failed || loading || busy.current) return;
    busy.current = true; setPending(true); setMessage(""); setError(false);
    try {
      await cancel(confirmation.target);
      setConfirmation(null);
      setMessage(uk ? "Команду скасування прийнято. Пошук, що працює, завершується без втрати збережених результатів." : "Команда отмены принята. Работающий поиск завершается без потери сохранённых результатов.");
    } catch {
      setError(true);
      setMessage(uk ? "Не вдалося скасувати. Оновіть чергу та перевірте стан; команда не повторюється автоматично." : "Не удалось отменить. Обновите очередь и проверьте состояние; команда не повторяется автоматически.");
    } finally { busy.current = false; setPending(false); }
  };
  const choose = (target: CancellationTarget, label: string) => {
    actionOrigin.current = document.activeElement as HTMLElement;
    setConfirmation({ target, label }); setMessage("");
  };
  const dismissConfirmation = () => {
    setConfirmation(null);
    if (actionOrigin.current?.isConnected && !actionOrigin.current.matches(":disabled")) actionOrigin.current.focus();
    else dialog.current?.querySelector<HTMLElement>("button:not(:disabled)")?.focus();
  };
  return <dialog ref={dialog} className="launch-dialog activity-dialog" aria-labelledby="activity-title" aria-describedby="activity-description"
    onCancel={(event) => { event.preventDefault(); if (!pending) close(); }}
    onKeyDown={(event) => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled)"));
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}>
    <div className="modal-head"><h2 id="activity-title">{uk ? "Активні запуски" : "Активные запуски"}</h2>
      <button type="button" disabled={pending} aria-label={uk ? "Закрити" : "Закрыть"} onClick={close}>×</button></div>
    <p id="activity-description">{uk ? "Спільна черга всіх сайтів. Перехід між сайтами не зупиняє пошук." : "Общая очередь всех сайтов. Переход между сайтами не останавливает поиск."}</p>
    <div className="launch-content">
    {message && <p ref={notice} tabIndex={-1} role={error ? "alert" : "status"} className={error ? "launch-error" : "activity-notice"}>{message}</p>}
    {confirmation && <section className="activity-confirmation" aria-label={uk ? "Підтвердження скасування" : "Подтверждение отмены"}>
      <h3 ref={confirmationHeading} tabIndex={-1}>{uk ? "Скасувати" : "Отменить"}: {confirmation.label}</h3>
      <p>{uk ? "Уже збережені результати залишаться. Інші запуски не зміняться." : "Уже сохранённые результаты останутся. Другие запуски не изменятся."}</p>
      {!cancellable && <p role="status">{uk ? "Стан змінився. Оновіть чергу й оберіть актуальну дію." : "Состояние изменилось. Обновите очередь и выберите актуальное действие."}</p>}
      <div className="activity-controls"><button className="button" disabled={pending} onClick={dismissConfirmation}>{uk ? "Не скасовувати" : "Не отменять"}</button>
        <button className="button danger" disabled={pending || failed || loading || !cancellable} onClick={() => void confirm()}>{pending ? (uk ? "Надсилаємо…" : "Отправляем…") : (uk ? "Підтвердити скасування" : "Подтвердить отмену")}</button></div>
    </section>}
      {loading && <p role="status">{uk ? "Завантаження черги…" : "Загрузка очереди…"}</p>}
      {failed ? <p className="launch-error" role="alert">{uk ? "Не вдалося прочитати актуальну чергу. Скасування недоступне." : "Не удалось прочитать актуальную очередь. Отмена недоступна."}</p>
        : !loading && !runs.length ? <p role="status">{uk ? "Активних запусків немає" : "Активных запусков нет"}</p> : runs.map((run) => {
          const portions = activityProjects(run, projects);
          const sites = [...new Set(portions.map((project) => project.source_key).filter(Boolean))];
          return <section className="activity-run" key={run.id}>
            <div className="activity-run-heading"><h3>{uk ? "Запуск" : "Запуск"} #{run.id}</h3><span className="badge outline">{statusLabel(run.status, uk)}</span></div>
            <p>{run.kind === "CARS" ? (uk ? `Оновлення автомобілів: ${run.payload?.car_ids?.length || 0}` : `Обновление автомобилей: ${run.payload?.car_ids?.length || 0}`) : (uk ? `Проєктів: ${portions.length}` : `Проектов: ${portions.length}`)} · {uk ? "Загальний прогрес" : "Общий прогресс"}: {run.progress}%</p>
            {sites.map((sourceKey) => <section className="activity-site" key={sourceKey}><div className="activity-run-heading"><b>{siteName(sourceKey)}</b>
              {run.kind === "PROJECTS" && <button className="button" disabled={pending || failed || !active(run.status) || !portions.some((project) => project.source_key === sourceKey && active(project.status))}
                onClick={() => choose({ scanId: run.id, scope: "SITE", sourceKey }, `${siteName(sourceKey)} · ${uk ? "запуск" : "запуск"} #${run.id}`)}>{uk ? "Скасувати сайт" : "Отменить сайт"}</button>}</div>
              {portions.filter((project) => project.source_key === sourceKey).map((project) => <div className="activity-project" key={project.id}>
                <span><b>{project.name}</b><small>{statusLabel(project.status, uk)}</small></span>
                {run.kind === "PROJECTS" && <button className="button" disabled={pending || failed || !active(run.status) || !active(project.status)}
                  onClick={() => choose({ scanId: run.id, scope: "PROJECT", projectId: project.id }, `${siteName(sourceKey)} · ${project.name} · #${run.id}`)}>{uk ? "Скасувати проєкт" : "Отменить проект"}</button>}
              </div>)}
            </section>)}
            {portions.filter((project) => !project.source_key).map((project) => <p key={project.id}>{project.name} · {statusLabel(project.status, uk)} · {uk ? "Сайт невідомий" : "Сайт неизвестен"}</p>)}
            <button className="button danger" disabled={pending || failed || !active(run.status)} onClick={() => choose({ scanId: run.id, scope: "RUN" }, `${uk ? "весь запуск" : "весь запуск"} #${run.id}`)}>{uk ? "Скасувати весь запуск" : "Отменить весь запуск"} #{run.id}</button>
          </section>;
        })}
    </div>
    <div className="launch-actions"><button className="button" disabled={pending || loading} onClick={retry}>{uk ? "Оновити чергу" : "Обновить очередь"}</button>
      <button className="button" disabled={pending} onClick={close}>{uk ? "Закрити" : "Закрыть"}</button></div>
  </dialog>;
}

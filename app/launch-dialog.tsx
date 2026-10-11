import { useEffect, useRef, useState, type RefObject } from "react";
import type { Locale } from "./translations";
import { SITE_NAMES, type SourceKey } from "./source-workspace";

export type LaunchProject = {
  id: number; name: string; source_key: string; can_search: boolean;
  reason: string | null; active_status: string | null; scan_run_id: number | null;
};
export type LaunchOptions = {
  sources: { source_key: string; family: string; can_search: boolean; reason: string | null }[];
  projects: LaunchProject[];
};
export type LaunchResult = {
  id: number; status: string; created: boolean;
  projects: { project_id: number; status: "QUEUED" | "ALREADY_ACTIVE"; scan_run_id: number }[];
};

export function launchableProjects(options?: LaunchOptions) {
  return options?.projects.filter((project) => project.can_search && !project.active_status
    && Object.hasOwn(SITE_NAMES, project.source_key)) || [];
}

export function launchSummary(result: LaunchResult, locale: Locale) {
  const queued = result.projects.filter((project) => project.status === "QUEUED").length;
  const active = result.projects.filter((project) => project.status === "ALREADY_ACTIVE").length;
  return locale === "uk" ? `У черзі: ${queued} · Уже активні: ${active}` : `В очереди: ${queued} · Уже активны: ${active}`;
}

export function reasonText(reason: string | null | undefined, locale: Locale) {
  const uk = locale === "uk";
  if (reason?.startsWith("LICENSE_")) return reason === "LICENSE_SOURCE_NOT_ALLOWED"
    ? (uk ? "Сайт недоступний за ліцензією" : "Сайт недоступен по лицензии")
    : (uk ? "Пошук недоступний за ліцензією" : "Поиск недоступен по лицензии");
  switch (reason) {
    case "AVAILABILITY_PENDING": return uk ? "Перевіряємо доступність" : "Проверяем доступность";
    case "AVAILABILITY_ERROR": return uk ? "Не вдалося перевірити доступність" : "Не удалось проверить доступность";
    case "SOURCE_UNAVAILABLE": return uk ? "Не вдалося завантажити модуль" : "Не удалось загрузить модуль";
    case "SOURCE_SEARCH_UNSUPPORTED": return uk ? "Модуль не підтримує пошук" : "Модуль не поддерживает поиск";
    case "DESKTOP_MAINTENANCE_ACTIVE": return uk ? "Обслуговування даних — спробуйте пізніше" : "Обслуживание данных — повторите позже";
    case "INVALID_SOURCE_URL": return uk ? "Перевірте посилання в налаштуваннях проєкту" : "Проверьте ссылку в настройках проекта";
    default: return uk ? "Пошук ще не підключено" : "Поиск ещё не подключён";
  }
}

export function LaunchDialog({ locale, options, loading, failed, retry, launch, close, errorMessage, returnFocus }: {
  locale: Locale; options?: LaunchOptions; loading: boolean; failed: boolean;
  retry: () => void; launch: (ids: number[]) => Promise<LaunchResult>; close: () => void;
  errorMessage: (error: unknown) => string;
  returnFocus?: RefObject<HTMLButtonElement | null>;
}) {
  const uk = locale === "uk";
  const dialog = useRef<HTMLDialogElement>(null);
  const resultHeading = useRef<HTMLHeadingElement>(null);
  const errorNotice = useRef<HTMLParagraphElement>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<LaunchResult | null>(null);
  const [submitted, setSubmitted] = useState<LaunchProject[]>([]);
  useEffect(() => {
    if (result) resultHeading.current?.focus();
    else if (error) errorNotice.current?.focus();
  }, [result, error]);
  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    return () => { node?.close(); returnFocus?.current?.focus(); };
  }, [returnFocus]);
  const ready = launchableProjects(options);
  const chosen = ready.filter((project) => selected.includes(project.id));
  const toggle = (ids: number[], checked: boolean) => setSelected((previous) => checked
    ? [...new Set([...previous, ...ids])] : previous.filter((id) => !ids.includes(id)));
  const submit = async () => {
    if (!chosen.length || pending || result || failed || loading) return;
    setPending(true);
    setError("");
    setSubmitted(chosen);
    try { setResult(await launch(chosen.map((project) => project.id))); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setPending(false); }
  };
  return <dialog ref={dialog} className="launch-dialog" aria-labelledby="launch-title" aria-describedby="launch-description"
    onKeyDown={(event) => {
      if (event.key !== "Tab") return;
      const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)"));
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}
    onCancel={(event) => { event.preventDefault(); if (!pending) close(); }}>
    <div className="modal-head"><h2 id="launch-title">{uk ? "Оновити дані" : "Обновить данные"}</h2>
      <button type="button" aria-label={uk ? "Закрити" : "Закрыть"} disabled={pending} onClick={close}>×</button></div>
    <p id="launch-description">{uk ? "Оберіть проєкти одного або кількох сайтів. Запуск — лише після підтвердження." : "Выберите проекты одного или нескольких сайтов. Запуск — только после подтверждения."}</p>
    {error && <p ref={errorNotice} tabIndex={-1} className="launch-error" role="alert">{error}</p>}
    <div className="launch-content">
    {result ? <section className="launch-result" role="status">
      <h3 ref={resultHeading} tabIndex={-1}>{launchSummary(result, locale)}</h3>
      <p>{uk ? "Повторно активні проєкти не додаються." : "Уже активные проекты повторно не добавляются."}</p>
      <ul>{result.projects.map((item) => <li key={item.project_id}>
        <span>{(() => { const project = submitted.find((row) => row.id === item.project_id); return project
          ? `${SITE_NAMES[project.source_key as SourceKey]} · ${project.name}` : `#${item.project_id}`; })()}</span>
        <b>{item.status === "QUEUED" ? (uk ? "У черзі" : "В очереди") : (uk ? "Уже активний" : "Уже активен")}</b>
      </li>)}</ul>
    </section> : <>
      {loading && <p role="status">{uk ? "Завантаження доступних проєктів…" : "Загрузка доступных проектов…"}</p>}
      {failed ? <div role="alert" className="launch-error"><p>{uk ? "Не вдалося перевірити доступність. Запуск недоступний." : "Не удалось проверить доступность. Запуск недоступен."}</p>
        <button className="button" type="button" disabled={loading || pending} onClick={retry}>{uk ? "Повторити" : "Повторить"}</button></div> : options && <>
        <div className="launch-selection-actions">
          <button type="button" className="button" disabled={!ready.length || pending} onClick={() => setSelected(ready.map((project) => project.id))}>{uk ? "Обрати всі доступні" : "Выбрать все доступные"}</button>
          <button type="button" className="button" disabled={!selected.length || pending} onClick={() => setSelected([])}>{uk ? "Скинути вибір" : "Сбросить выбор"}</button>
        </div>
        {([{ title: uk ? "Майданчики" : "Площадки", keys: ["encar"] },
          { title: uk ? "Аукціони" : "Аукционы", keys: ["copart", "iaa"] }] as {title: string; keys: SourceKey[]}[]).map((family) =>
          <section className="launch-family" key={family.keys[0]}><h3>{family.title}</h3>
            {family.keys.map((key) => {
              const site = options.sources.find((item) => item.source_key === key);
              const projects = options.projects.filter((project) => project.source_key === key);
              const available = ready.filter((project) => project.source_key === key).map((project) => project.id);
              const count = available.filter((id) => selected.includes(id)).length;
              return <fieldset key={key} className="launch-site" disabled={pending}>
                <legend>{SITE_NAMES[key]}</legend>
                <label className="launch-site-toggle"><input type="checkbox" checked={available.length > 0 && count === available.length}
                  ref={(node) => { if (node) node.indeterminate = count > 0 && count < available.length; }}
                  disabled={!available.length} onChange={(event) => toggle(available, event.target.checked)}
                  aria-label={`${uk ? "Обрати всі проєкти" : "Выбрать все проекты"} ${SITE_NAMES[key]}`} />
                  <b>{uk ? "Усі доступні проєкти сайту" : "Все доступные проекты сайта"}</b><small>{count} / {available.length}</small></label>
                {!site?.can_search && <p className="launch-site-reason">{reasonText(site?.reason, locale)}</p>}
                {!projects.length && <p className="launch-empty">{uk ? "Проєктів поки немає" : "Проектов пока нет"}</p>}
                {projects.map((project) => <label key={project.id} className="launch-project">
                  <input type="checkbox" checked={selected.includes(project.id)} disabled={!project.can_search || !!project.active_status}
                    onChange={(event) => toggle([project.id], event.target.checked)} />
                  <span><b>{project.name}</b><small>{!project.can_search ? reasonText(project.reason, locale)
                    : project.active_status === "QUEUED" ? (uk ? "Уже в черзі" : "Уже в очереди")
                    : project.active_status === "RUNNING" ? (uk ? "Оновлюється" : "Обновляется")
                    : project.active_status === "CANCEL_REQUESTED" ? (uk ? "Скасування запитано" : "Отмена запрошена")
                    : project.active_status ? (uk ? "Уже бере участь у запуску" : "Уже участвует в запуске")
                    : (uk ? "Готовий до запуску" : "Готов к запуску")}</small></span>
                </label>)}
              </fieldset>;
            })}
          </section>)}
        <section className="launch-confirmation" aria-live="polite"><h3>{uk ? "До запуску" : "К запуску"}: {chosen.length}</h3>
          {chosen.length ? <ul>{chosen.map((project) => <li key={project.id}>{SITE_NAMES[project.source_key as SourceKey]} · {project.name}</li>)}</ul>
            : <p>{uk ? "Оберіть хоча б один доступний проєкт." : "Выберите хотя бы один доступный проект."}</p>}
          {selected.length > chosen.length && <p>{uk ? "Частина вибору стала недоступною або вже активна й не буде запущена." : "Часть выбора стала недоступна или уже активна и не будет запущена."}</p>}
        </section>
      </>}
    </>}
    </div>
    <div className="launch-actions"><button type="button" className="button" disabled={pending} onClick={close}>{result ? "Готово" : (uk ? "Скасувати" : "Отмена")}</button>
      {!result && <button type="button" className="button primary" disabled={!chosen.length || loading || failed || pending} onClick={() => void submit()}>
        {pending ? (uk ? "Додаємо до черги…" : "Добавляем в очередь…") : `${uk ? "Запустити оновлення" : "Запустить обновление"} (${chosen.length})`}</button>}</div>
  </dialog>;
}

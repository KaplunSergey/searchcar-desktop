import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import type { TelegramBatch } from "./telegram-delivery";
import { computerTimeZone, watchAuctionCountdown } from "./auction-time-client";
import { AuctionTelegramPreview } from "./auction-telegram-preview";

type Props = Pick<SourceWorkspaceProps, "source" | "locale" | "read" | "write"> & { projectId: number };
type AutomaticStatus = { project_id: number; source_key: string; enabled: boolean; recipient: { project_id: number; title: string; chat_id: number; binding_id: string } | null;
  batch: TelegramBatch | null; issue: string | null; max_result_age_minutes: number };

export function checkedAutomaticStatus(value: AutomaticStatus, source: string, projectId: number) {
  if (!value || value.project_id !== projectId || value.source_key !== source || typeof value.enabled !== "boolean"
    || value.recipient && value.recipient.project_id !== projectId
    || value.batch && (value.batch.project_id !== projectId || value.batch.source_key !== source || !Array.isArray(value.batch.jobs))) throw new Error("telegram_context_changed");
  return value;
}

// A local clock update, not a poll to Cloudflare. Works across all app sections.
export function TelegramAutomaticClock({ enabled, write }: { enabled: boolean; write: NonNullable<Props["write"]> }) {
  useEffect(() => {
    if (!enabled) return;
    let stopped = false, pending = false;
    const refresh = async () => {
      const zone = computerTimeZone();
      if (stopped || pending || !zone) return;
      pending = true;
      try { await write("/telegram/automatic-clock", { computer_timezone: zone }); }
      catch { /* The dispatcher fails closed without a fresh computer clock. */ }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 60000);
    window.addEventListener("focus", refresh); window.addEventListener("pageshow", refresh);
    return () => { stopped = true; window.clearInterval(timer); window.removeEventListener("focus", refresh); window.removeEventListener("pageshow", refresh); };
  }, [enabled, write]);
  return null;
}

function issueText(issue: string | null, locale: Locale) {
  const uk = locale === "uk";
  switch (issue) {
    case "RUN_INCOMPLETE": return uk ? "Пошук завершився не повністю. Автонадсилання пропущене для всього запуску." : "Поиск завершился не полностью. Автоотправка пропущена для всего запуска.";
    case "SEARCH_CHANGED": return uk ? "Умови змінилися або перевірено не всі сторінки. Оновіть проєкт повністю." : "Условия изменились или проверены не все страницы. Обновите проект полностью.";
    case "RESULT_EXPIRED": return uk ? "Дані застаріли. Потрібно оновити проєкт." : "Данные устарели. Нужно обновить проект.";
    case "CLOCK_REQUIRED": return uk ? "Автонадсилання призупинене: очікуємо поточний часовий пояс комп’ютера." : "Автоотправка приостановлена: ожидаем текущий часовой пояс компьютера.";
    case "RECIPIENT_CHANGED": return uk ? "Одержувач змінився. Перевірте його та ввімкніть опцію знову." : "Получатель изменился. Проверьте его и включите опцию заново.";
    default: return null;
  }
}

export function TelegramAutomatic({ source, projectId, locale, read, write, projectName, compact = false, imageUrl = path => path }: Props & { projectName?: string; compact?: boolean; imageUrl?: SourceWorkspaceProps["imageUrl"] }) {
  const client = useQueryClient(), [confirm, setConfirm] = useState(false), [pending, setPending] = useState(false), [error, setError] = useState(false);
  const [preview, setPreview] = useState(false), origin = useRef<HTMLButtonElement>(null), heading = useRef<HTMLHeadingElement>(null);
  const [nowMs, setNowMs] = useState(Date.now);
  useEffect(() => preview ? watchAuctionCountdown(setNowMs) : undefined, [preview]);
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const key = ["telegram-automatic", source, projectId], path = `/projects/${projectId}/telegram-automatic?source_key=${source}`;
  const query = useQuery<AutomaticStatus>({ queryKey: key, queryFn: async () => checkedAutomaticStatus(await read(path), source, projectId), retry: false, refetchInterval: 5000 });
  const data = query.data, jobs = data?.batch?.jobs || [];
  const active = jobs.some(job => job.state === "QUEUED" || job.state === "IN_FLIGHT");
  const change = async (enabled: boolean) => {
    const zone = computerTimeZone();
    if (!write || pending || !zone) { setError(true); return; }
    setPending(true); setError(false);
    try {
      client.setQueryData(key, checkedAutomaticStatus(await write(path, { enabled, computer_timezone: zone, binding_id: data?.recipient?.binding_id || null }), source, projectId)); setConfirm(false);
    } catch { setError(true); void query.refetch(); }
    finally { setPending(false); }
  };
  const cancel = async () => {
    if (!write || !data?.batch || pending) return;
    setPending(true); setError(false);
    try { await write(`/projects/${projectId}/telegram-deliveries/${data.batch.id}/cancel?source_key=${source}`, {}); await query.refetch(); }
    catch { setError(true); }
    finally { setPending(false); }
  };
  if (compact && !data?.enabled && !data?.batch && !data?.issue) return null;
  return <section className={compact ? "telegram-auto-progress" : "telegram-automatic-settings"} aria-busy={pending} aria-label={l("Автоотправка в Telegram", "Автонадсилання в Telegram")}>
    {pending && <p role="status">{l("Сохраняем изменение…", "Зберігаємо зміну…")}</p>}
    {!compact && <><h3 ref={heading}>{l("Автоотправка «Сегодня»", "Автонадсилання «Сьогодні»")}</h3>
      <p>{l("После полного успешного поиска отправлять машины с началом аукциона сегодня. При ошибке или отмене любого проекта в общем запуске отправки не будет.", "Після повного успішного пошуку надсилати авто з початком аукціону сьогодні. За помилки чи скасування будь-якого проєкту в спільному запуску надсилання не буде.")}</p>
      <label className="telegram-auto-toggle"><input type="checkbox" checked={Boolean(data?.enabled || confirm)} disabled={!write || pending || !data || !data.recipient || query.isError}
        onChange={event => event.target.checked ? setConfirm(true) : data?.enabled ? void change(false) : setConfirm(false)} />{l("Отправлять автоматически после поиска", "Надсилати автоматично після пошуку")}</label>
      {query.isPending && <p role="status">{l("Читаем настройку…", "Читаємо налаштування…")}</p>}
      {query.isError && <p role="alert">{l("Не удалось прочитать автоотправку. Ничего не включено этим экраном.", "Не вдалося прочитати автонадсилання. Цей екран нічого не ввімкнув.")} <button type="button" className="button" onClick={() => void query.refetch()}>{l("Повторить", "Повторити")}</button></p>}
      {data && !data.recipient && <p>{l("Сначала подключите получателя выше.", "Спочатку підключіть одержувача вище.")}</p>}
      {data?.recipient && <p>{l("Получатель", "Одержувач")}: <b>{data.recipient.title}</b> · {data.recipient.chat_id}</p>}
      <p className="auction-snapshot">{l("По умолчанию выключено. Включение не отправляет сохранённые машины: нужен следующий полный поиск. Один раунд в одном чате повторно не публикуется; вручную повторить можно. Очередь работает, пока открыто приложение.", "Типово вимкнено. Увімкнення не надсилає збережені авто: потрібен наступний повний пошук. Один раунд в одному чаті повторно не публікується; вручну повторити можна. Черга працює, доки відкрито застосунок.")}</p>
      {data && <p className="auction-snapshot">{l("Защита от устаревших данных", "Захист від застарілих даних")}: {data.max_result_age_minutes} {l("минут после завершения поиска. Если очередь задержится дольше — нужно обновить проект.", "хвилин після завершення пошуку. Якщо черга затримається довше — потрібно оновити проєкт.")}</p>}
      {confirm && !data?.enabled && <div className="telegram-project-actions"><button type="button" className="button telegram-button" disabled={pending || !data?.recipient} onClick={() => void change(true)}>{l("Включить для следующего поиска", "Увімкнути для наступного пошуку")}</button><button type="button" className="button" disabled={pending} onClick={() => setConfirm(false)}>{l("Отмена", "Скасувати")}</button></div>}
      {data && <button ref={origin} type="button" className="button" onClick={() => setPreview(true)}>{l("Посмотреть сообщения «Сегодня»", "Переглянути повідомлення «Сьогодні»")}</button>}
      {data && <p className="auction-snapshot">{l("Предпросмотр показывает сохранённые машины. Состав автоотправки определится после нового поиска; неподтверждённые дата и время не используются.", "Попередній перегляд показує збережені авто. Склад автонадсилання визначиться після нового пошуку; непідтверджені дата й час не використовуються.")}</p>}
      {preview && <AuctionTelegramPreview source={source} projectId={projectId} projectName={projectName || `#${projectId}`} locale={locale} read={read} write={write} imageUrl={imageUrl} openCalendar={write ? url => write("/desktop/open-external", { url }) : undefined} nowMs={nowMs} returnFocus={origin} fallbackFocus={heading} close={() => setPreview(false)} />}
    </>}
    {data?.enabled && <p>{l("Автоотправка «Сегодня» включена", "Автонадсилання «Сьогодні» ввімкнено")}{!data.batch && ` · ${l("ждём следующего полного поиска", "чекаємо наступного повного пошуку")}`}</p>}
    {compact && query.isError && data && <p role="alert">{l("Не удалось обновить состояние автоотправки.", "Не вдалося оновити стан автонадсилання.")} <button type="button" className="button" onClick={() => void query.refetch()}>{l("Повторить", "Повторити")}</button></p>}
    {data?.issue && <p role="status">{issueText(data.issue, locale)}</p>}
    {data?.batch && <><p role="status">{l("Автоотправка", "Автонадсилання")}: {jobs.filter(job => job.state === "SENT").length} / {jobs.length} · {l("в очереди", "у черзі")}: {jobs.filter(job => job.state === "QUEUED").length}
      {jobs.some(job => job.state === "IN_FLIGHT") && ` · ${l("отправляется", "надсилається")}`}</p>
      {!jobs.length && !data.issue && <p>{l("Нет новых подходящих машин «Сегодня». Ранее отправленные раунды пропущены.", "Немає нових відповідних авто «Сьогодні». Раніше надіслані раунди пропущені.")}</p>}
      {jobs.some(job => job.state === "UNKNOWN") && <p role="alert">{l("Результат части отправок неизвестен. Автоматического повтора не будет.", "Результат частини надсилань невідомий. Автоматичного повтору не буде.")}</p>}
      {jobs.some(job => job.state === "FAILED" || job.state === "CANCELLED") && <p>{l("Часть отправок не выполнена или отменена: данные, доступ или условия изменились. Проверьте проект; ручная отправка доступна.", "Частину надсилань не виконано чи скасовано: дані, доступ або умови змінилися. Перевірте проєкт; ручне надсилання доступне.")}</p>}
      {active && <button type="button" className="button" disabled={pending} onClick={() => void cancel()}>{l("Отменить оставшиеся автоотправки", "Скасувати решту автонадсилань")}</button>}
    </>}
    {error && <p role="alert">{l("Не удалось подтвердить изменение. Проверьте состояние; не повторяйте отправку вслепую.", "Не вдалося підтвердити зміну. Перевірте стан; не повторюйте надсилання наосліп.")}</p>}
  </section>;
}

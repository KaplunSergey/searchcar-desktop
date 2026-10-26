import { useState, type MouseEvent } from "react";
import { auctionCalendar, type CalendarIssue } from "./auction-calendar";
import { AUCTION_CALENDAR_PAGE_URL } from "./auction-calendar-link";
import type { Locale } from "./translations";

export function AuctionCalendarActions({ calendar, pageUrl, locale, open, nowMs }: {
  calendar: ReturnType<typeof auctionCalendar>; locale: Locale; open?: (url: string) => Promise<unknown> | void; nowMs: number;
  pageUrl?: string | null;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const [copied, setCopied] = useState<string | null>(null), [copyFailed, setCopyFailed] = useState(false);
  const [opening, setOpening] = useState(false), [openFailed, setOpenFailed] = useState(false);
  const reasons: Record<CalendarIssue, string> = {
    NO_START: l("Для календаря нужно подтверждённое точное время начала, не окончания.", "Для календаря потрібен підтверджений точний час початку, не завершення."),
    NO_LINK: l("Безопасная ссылка на лот недоступна.", "Безпечне посилання на лот недоступне."),
    UNAVAILABLE: l("Лот недоступен или торги уже начались/завершились.", "Лот недоступний або торги вже почалися/завершилися."),
    PAST: l("Время начала уже прошло. Обновите сведения о лоте.", "Час початку вже минув. Оновіть відомості про лот."),
  };
  if (!calendar.available) return <p className="auction-snapshot">{reasons[calendar.issue]}</p>;
  if (!Number.isFinite(nowMs) || Date.parse(calendar.start_utc) <= nowMs) return <p className="auction-snapshot">{reasons.PAST}</p>;
  const google = calendar.google_url;
  const page = pageUrl?.startsWith(`${AUCTION_CALENDAR_PAGE_URL}#`) && pageUrl.length <= 2048 ? pageUrl : null;
  const target = page || google;
  async function openInBrowser(event: MouseEvent<HTMLAnchorElement>, url: string) {
    if (!open) return;
    event.preventDefault(); if (opening) return;
    setOpening(true); setOpenFailed(false);
    try { await open(url); } catch { setOpenFailed(true); } finally { setOpening(false); }
  }
  return <div className="auction-calendar-actions">
    <div className="auction-calendar-buttons">
      <a className="button" href={target} target="_blank" rel="noopener noreferrer" aria-disabled={opening} onClick={event => openInBrowser(event, target)}>
        {page ? l("Добавить в календарь", "Додати до календаря") : l("Добавить в Google Calendar", "Додати до Google Calendar")}</a>
      <button className="button" type="button" onClick={async () => {
        try { await navigator.clipboard.writeText(target); setCopied(target); setCopyFailed(false); }
        catch { setCopyFailed(true); }
      }}>{l("Скопировать ссылку", "Скопіювати посилання")}</button>
      {page && <a className="button" href={google} target="_blank" rel="noopener noreferrer" aria-disabled={opening} onClick={event => openInBrowser(event, google)}>
        {l("Google Calendar напрямую", "Google Calendar напряму")}</a>}
      <a className="button" download={calendar.filename} href={`data:text/calendar;charset=utf-8,${encodeURIComponent(calendar.ics)}`}>{l("Календарный файл (.ics)", "Файл календаря (.ics)")}</a>
    </div>
    {page && <p className="auction-snapshot">{l("Кнопка открывает страницу выбора календаря с данными этого авто. Эту же ссылку можно открыть из сообщения на телефоне. Импорт Apple/Outlook на телефоне ещё требует проверки.", "Кнопка відкриває сторінку вибору календаря з даними цього авто. Це саме посилання можна відкрити з повідомлення на телефоні. Імпорт Apple/Outlook на телефоні ще потребує перевірки.")}</p>}
    <p className="auction-snapshot">{l("Предпросмотр действий сообщения. В Google проверьте напоминание за 15 минут перед сохранением. Файл .ics — локальный экспорт, не ссылка для другого телефона. Переносы аукциона автоматически не обновляются.", "Попередній перегляд дій повідомлення. У Google перевірте нагадування за 15 хвилин перед збереженням. Файл .ics — локальний експорт, не посилання для іншого телефона. Перенесення аукціону автоматично не оновлюються.")}</p>
    {openFailed && <p role="alert">{l("Не удалось открыть браузер. Повторите или скопируйте ссылку и откройте её вручную.", "Не вдалося відкрити браузер. Повторіть або скопіюйте посилання та відкрийте його вручну.")}</p>}
    {calendar.google_shortened && <p className="auction-snapshot">{l("В Google название дополнительно сокращено из-за длины ссылки. В .ics этого сокращения нет.", "У Google назву додатково скорочено через довжину посилання. У .ics цього скорочення немає.")}</p>}
    {Date.parse(calendar.start_utc) - nowMs < 900000 && <p role="status">{l("До начала меньше 15 минут: время такого напоминания уже прошло.", "До початку менше 15 хвилин: час такого нагадування вже минув.")}</p>}
    {copied === target && !copyFailed && <p role="status">{l("Ссылка скопирована. Можно вставить её в сообщение и проверить на телефоне.", "Посилання скопійовано. Можна вставити його в повідомлення та перевірити на телефоні.")}</p>}
    {copyFailed && <label>{l("Не удалось скопировать автоматически. Выделите ссылку и скопируйте вручную:", "Не вдалося скопіювати автоматично. Виділіть посилання та скопіюйте вручну:")}
      <input type="text" readOnly value={target} onFocus={event => event.currentTarget.select()} /></label>}
  </div>;
}

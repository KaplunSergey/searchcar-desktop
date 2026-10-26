import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import { useQuery } from "@tanstack/react-query";
import { AUCTION_QUERY_KEY, auctionTimeAt, type AuctionClockContext, type AuctionLot, type AuctionCollection, type ProjectAuctionView } from "./auction-time-client";
import { TelegramDeliveryProgress } from "./telegram-send";
import { useTelegramDelivery, useTelegramHistory } from "./telegram-delivery";
import { SOURCE_CATALOG, sourceDefinition, sourceName, useSourceCatalog, type SourceCatalog } from "./source-catalog";
import { sourceListingLink } from "./source-listing-links";
import { PhotoPreview } from "./photo-preview";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import type { Locale } from "./translations";
import { auctionCalendar } from "./auction-calendar";
import { AuctionCalendarActions } from "./auction-calendar-actions";
import { AUCTION_CALENDAR_PAGE_URL, calendarPageLink } from "./auction-calendar-link";

function clip(value: string, limit: number) {
  return value.length <= limit ? value : value.slice(0, limit - 1).replace(/[\uD800-\uDBFF]$/, "") + "…";
}
const clean = (value: unknown, limit = 120) => typeof value === "string"
  ? clip(value.replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, " ").replace(/\s+/g, " ").trim(), limit) : "";
type MessageEntity = { type: "bold"; offset: number; length: number } | { type: "text_link"; offset: number; length: number; url: string };

export function auctionTelegramMessage(lot: AuctionLot, context: AuctionClockContext, locale: Locale, catalog: SourceCatalog = SOURCE_CATALOG, calendarPageUrl = AUCTION_CALENDAR_PAGE_URL, manual = false) {
  const unavailable = lot.project_search_status === "NOT_FOUND_IN_SEARCH";
  if (!Number.isFinite(Date.parse(context.generated_at))
    || !manual && (unavailable || lot.time.is_today !== true || lot.time.local_date !== context.local_date)) return null;
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const time = auctionTimeAt(lot, Date.parse(context.generated_at));
  const mileage = lot.details.mileage as Record<string, unknown> | undefined;
  const distance = typeof mileage?.value === "number" && Number.isFinite(mileage.value) && mileage.value >= 0 && ["mi", "km"].includes(String(mileage.unit))
    ? `${new Intl.NumberFormat(locale === "uk" ? "uk-UA" : "ru-RU").format(mileage.value).replace(/[\u00a0\u202f]/g, " ")} ${mileage.unit}${clean(mileage.accuracy, 40) ? ` · ${clean(mileage.accuracy, 40)}` : ""}` : l("нет данных", "немає даних");
  let link = sourceListingLink(lot, catalog);
  if (link) {
    const url = new URL(link); url.search = ""; url.hash = "";
    link = url.href.length <= 512 ? url.href : null;
  }
  let photo: string | null = null;
  if (typeof lot.image === "string" && !/[\u0000-\u001f\u007f]/.test(lot.image)) {
    if (lot.image.startsWith("/storage/") && !lot.image.includes("..") && !/[\\%?#]/.test(lot.image)) photo = lot.image;
    else try {
      const url = new URL(lot.image);
      if (url.protocol === "https:" && !url.username && !url.password && !url.port
        && sourceDefinition(lot.source_key, catalog)?.image_domains?.includes(url.hostname)) photo = url.href;
    } catch { /* An unsafe saved image becomes text-only. */ }
  }
  const name = sourceName(lot.source_key, catalog);
  const listingLabel = link ? l(`Открыть автомобиль на ${name}`, `Відкрити автомобіль на ${name}`) : l("Ссылка на лот недоступна", "Посилання на лот недоступне");
  const limit = photo ? 1024 : 4096;
  const calendar = auctionCalendar(lot, Date.parse(context.generated_at), locale, catalog);
  const page = calendarPageUrl ? calendarPageLink(lot, Date.parse(context.generated_at), locale, calendarPageUrl) : null;
  const calendarLink = calendar.available ? { text: page ? l("Добавить в календарь", "Додати до календаря")
    : l("Добавить в Google Calendar", "Додати до Google Calendar"), url: page || calendar.google_url } : null;
  let text = "", entities: MessageEntity[] = [];
  const add = (value: string, type?: "bold" | "text_link", url?: string) => {
    const offset = text.length; text += value;
    if (type === "bold") entities.push({ type, offset, length: value.length });
    if (type === "text_link" && url) entities.push({ type, offset, length: value.length, url });
  };
  const field = (icon: string, label: string, value: string) => { add(icon + " "); add(label + ":", "bold"); add(" " + value); };
  add("🚙 "); add(clean(lot.title, 160) || l("Без названия", "Без назви"), "bold"); add("\n\n");
  if (unavailable) add(l("Не найден в последнем полном поиске. Последние известные сведения, не подтверждение продажи.", "Не знайдений в останньому повному пошуку. Останні відомі дані, не підтвердження продажу.") + "\n\n");
  field("🏷", l("Аукцион", "Аукціон"), `${name} · № ${clean(lot.source_listing_id, 50)}`); add("\n");
  field("🛠", l("Повреждения", "Пошкодження"), clean(lot.details.primary_damage) || l("нет данных", "немає даних")); add("\n");
  field("🛣", "Одометр", distance); add("\n\n");
  if (time.local_date) {
    const months = l("января февраля марта апреля мая июня июля августа сентября октября ноября декабря", "січня лютого березня квітня травня червня липня серпня вересня жовтня листопада грудня").split(" ");
    const date = `${Number(time.local_date.slice(8, 10))} ${months[Number(time.local_date.slice(5, 7)) - 1]} ${time.local_date.slice(0, 4)}`;
    const event = time.event_kind === "START" ? l("Начало", "Початок") : time.event_kind === "END" ? l("Окончание", "Завершення") : l("Событие", "Подія");
    field("🗓", event, (time.event_kind === "UNKNOWN" ? l("тип не подтверждён · ", "тип не підтверджений · ") : "") + date
      + (time.local_datetime ? `, ${time.local_datetime.slice(11, 16)}` : ` · ${l("точное время не указано", "точний час не вказано")}`));
  } else add("🗓 " + l("Дата не назначена или не подтверждена", "Дата не призначена або не підтверджена"));
  add("\n"); field("🌍", l("Часовой пояс", "Часовий пояс"), context.computer_timezone);
  if (!unavailable && time.precision === "EXACT" && time.event_kind !== "UNKNOWN" && time.remaining_seconds !== null) {
    const minutes = Math.ceil(time.remaining_seconds / 60), days = Math.floor(minutes / 1440);
    add("\n");
    if (minutes > 0) field("⏳", time.event_kind === "END" ? l("До окончания", "До завершення") : l("До начала", "До початку"),
      `${days ? `${days} ${l("д", "дн")} ` : ""}${Math.floor(minutes / 60) % 24} ${l("ч", "год")} ${minutes % 60} ${l("мин", "хв")}`);
    else add("⏳ " + l("Запланированное время прошло — результат не проверен", "Запланований час минув — результат не перевірено"));
  }
  const footer = `🔗 ${listingLabel}${calendarLink ? `\n📅 ${calendarLink.text}` : ""}`;
  const body = clip(text, limit - footer.length - 2), end = body.length - (body.length < text.length ? 1 : 0);
  // Keep entity ranges in UTF-16 and preserve complete links even when the body is clipped.
  entities = entities.filter(entity => entity.offset < end).map(entity => ({ ...entity, length: Math.min(entity.length, end - entity.offset) }));
  text = body + "\n\n"; add("🔗 "); add(listingLabel, link ? "text_link" : undefined, link || undefined);
  if (calendarLink) { add("\n📅 "); add(calendarLink.text, "text_link", calendarLink.url); }
  const reply_markup = calendarLink ? { inline_keyboard: [[calendarLink]] } : undefined;
  return { car_id: lot.id, text, photo, link, limit, calendar, calendar_page_url: page, reply_markup,
    caption_entities: photo ? entities : undefined, entities: photo ? undefined : entities };
}

function TelegramMessageText({ message, open, nowMs, locale }: {
  message: NonNullable<ReturnType<typeof auctionTelegramMessage>>; nowMs: number; locale: Locale;
  open?: (url: string) => Promise<unknown> | void;
}) {
  const [opening, setOpening] = useState(false), [failed, setFailed] = useState(false);
  const entities = message.caption_entities || message.entities || [];
  const active = message.calendar.available && Number.isFinite(nowMs) && Date.parse(message.calendar.start_utc) > nowMs;
  const last = entities.at(-1), end = last ? last.offset + last.length : 0;
  return <p className="telegram-preview-text">{entities.map((entity, index) => {
    const previous = entities[index - 1];
    const before = message.text.slice(previous ? previous.offset + previous.length : 0, entity.offset), label = message.text.slice(entity.offset, entity.offset + entity.length);
    if (entity.type === "bold") return <span key={entity.offset}>{before}<strong>{label}</strong></span>;
    const calendar = entity.url === message.calendar_page_url || message.calendar.available && entity.url === message.calendar.google_url;
    return <span key={entity.offset}>{before}{!calendar || active
    ? <a className={calendar ? "telegram-message-calendar" : "telegram-message-listing"} href={entity.url} target="_blank" rel="noopener noreferrer" aria-disabled={opening}
      onClick={async event => {
        if (!open) return;
        event.preventDefault(); if (opening) return;
        setOpening(true); setFailed(false);
        try { await open(entity.url); } catch { setFailed(true); } finally { setOpening(false); }
      }}>{label}</a>
    : <span aria-disabled="true">{label}</span>}</span>;
  })}{message.text.slice(end)}
    {failed && <span role="alert" className="warning-text">{"\n"}{locale === "uk"
      ? "Не вдалося відкрити посилання. Повторіть або скористайтеся кнопками нижче."
      : "Не удалось открыть ссылку. Повторите или воспользуйтесь кнопками ниже."}</span>}
  </p>;
}

export function AuctionTelegramPreview({ source, projectId, projectName, locale, read, write, imageUrl, openCalendar, nowMs, close, returnFocus, fallbackFocus, collection = "today", settings }: {
  source: string; projectId: number; projectName: string; locale: Locale; read: SourceWorkspaceProps["read"]; imageUrl: SourceWorkspaceProps["imageUrl"];
  close: () => void; returnFocus: RefObject<HTMLButtonElement | null>; fallbackFocus: RefObject<HTMLHeadingElement | null>;
  openCalendar?: (url: string) => Promise<unknown> | void;
  nowMs: number;
  collection?: AuctionCollection; settings?: () => void;
  write?: SourceWorkspaceProps["write"];
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const catalog = useSourceCatalog(), dialog = useRef<HTMLDialogElement>(null), titleId = useId(), helpId = useId();
  const [visible, setVisible] = useState(20), nextFocus = useRef<number | null>(null);
  const [selection, setSelection] = useState<{ data?: ProjectAuctionView; ids: number[] }>({ ids: [] });
  const sending = useTelegramDelivery(source, projectId, `collection:${collection}`, locale, read, write);
  const history = useTelegramHistory(source, projectId, read);
  const labels = { active: l("Все сохранённые", "Усі збережені"), today: l("Сегодня", "Сьогодні"), future: l("Будущие", "Майбутні"), undated: l("Без даты", "Без дати"), archive: l("Архив", "Архів") };
  const query = useQuery<ProjectAuctionView>({ queryKey: [...AUCTION_QUERY_KEY, "telegram-preview", source, projectId, collection], retry: false,
    refetchOnWindowFocus: false, refetchOnReconnect: false, staleTime: 0,
    queryFn: async () => {
      const data = await read<ProjectAuctionView>(`/projects/${projectId}/auction-lots?collection=${collection}`);
      if (data.project_id !== projectId || data.source_key !== source || data.collection && data.collection !== collection
        || !Array.isArray(data.lots) || data.lots.some(lot => lot.source_key !== source) || new Set(data.lots.map(lot => lot.id)).size !== data.lots.length)
        throw new Error("Preview context mismatch");
      return data;
    } });
  const messages = useMemo(() => query.data?.lots.flatMap(lot => {
    const message = auctionTelegramMessage(lot, query.data!, locale, catalog, undefined, true);
    return message ? [message] : [];
  }) || [], [query.data, locale, catalog]);
  const selected = selection.data === query.data ? selection.ids : [];
  const sendable = messages.filter(message => message.link);
  useEffect(() => {
    const node = dialog.current, origin = returnFocus.current, fallback = fallbackFocus.current;
    node?.showModal();
    return () => { node?.close(); if (origin?.isConnected) origin.focus(); else fallback?.focus(); };
  }, [returnFocus, fallbackFocus]);
  useEffect(() => {
    if (nextFocus.current === null) return;
    dialog.current?.querySelectorAll<HTMLElement>("[data-preview-message]")[nextFocus.current]?.focus();
    nextFocus.current = null;
  }, [visible]);
  return <dialog ref={dialog} className="launch-dialog auction-telegram-preview" aria-labelledby={titleId} aria-describedby={helpId}
    onCancel={event => { if (event.target === event.currentTarget) { event.preventDefault(); close(); } }}>
    <h2 id={titleId}>Telegram · {projectName} · {labels[collection]}</h2>
    <p id={helpId}>{l("Выберите автомобили для отправки получателю этого проекта.", "Виберіть автомобілі для надсилання одержувачу цього проєкту.")}</p>
    <div className="launch-content">
      {query.isPending ? <p role="status">{l("Читаем сохранённые лоты…", "Читаємо збережені лоти…")}</p> : query.isError
        ? <p role="alert">{l("Не удалось подготовить предпросмотр.", "Не вдалося підготувати попередній перегляд.")} <button className="button" type="button" onClick={() => { void query.refetch(); }}>{l("Повторить", "Повторити")}</button></p>
        : <><p className="auction-snapshot">{l("Рассчитано на", "Розраховано на")}: {query.data?.generated_at} · {query.data?.computer_timezone}. {l("Сообщений", "Повідомлень")}: {messages.length}</p>
          {!messages.length && <p>{l("В этом разделе пока нет автомобилей. Обновите данные проекта или выберите другой раздел. Подключать Telegram повторно не нужно.", "У цьому розділі поки немає автомобілів. Оновіть дані проєкту або виберіть інший розділ. Підключати Telegram повторно не потрібно.")}</p>}
          {!!messages.length && <label className="telegram-select-all"><input type="checkbox" disabled={sending.selectionLocked || query.isFetching || !sendable.length}
            checked={!!sendable.length && selected.length === sendable.length} onChange={event => setSelection({ data: query.data, ids: event.target.checked ? sendable.map(message => message.car_id) : [] })} />{l("Выбрать все в разделе", "Вибрати всі в розділі")} ({sendable.length})</label>}
          {messages.slice(0, visible).map(message => <article key={message.car_id} className="telegram-preview-message" data-preview-message tabIndex={-1}>
            <label className="telegram-select-car"><input type="checkbox" checked={selected.includes(message.car_id)} disabled={sending.selectionLocked || query.isFetching || !message.link}
              onChange={event => setSelection({ data: query.data, ids: event.target.checked ? [...selected, message.car_id] : selected.filter(id => id !== message.car_id) })} />
              {query.data?.lots.find(lot => lot.id === message.car_id)?.title || `#${message.car_id}`}</label>
            {history.data?.sent_car_ids.includes(message.car_id) && <p className="telegram-sent-before">{l("Ранее отправлено · при выборе отправим ещё раз", "Раніше надіслано · за вибору надішлемо ще раз")}</p>}
            {message.photo ? <><PhotoPreview src={imageUrl(message.photo)} title={l("Фото лота", "Фото лота")} locale={locale} /><p className="auction-snapshot">{l("Фото по сохранённой ссылке; доступность для Telegram не проверена.", "Фото за збереженим посиланням; доступність для Telegram не перевірена.")}</p></>
              : <p className="auction-snapshot">{l("Без фото · текстовое сообщение", "Без фото · текстове повідомлення")}</p>}
            <TelegramMessageText message={message} open={openCalendar} nowMs={nowMs} locale={locale} />
            <AuctionCalendarActions calendar={message.calendar} pageUrl={message.calendar_page_url} locale={locale} open={openCalendar} nowMs={nowMs} />
          </article>)}
          {visible < messages.length && <button type="button" className="button" onClick={() => { nextFocus.current = visible; setVisible(count => count + 20); }}>{l("Показать ещё", "Показати ще")}</button>}
        </>}
    </div>
    <TelegramDeliveryProgress state={sending} locale={locale} />
    {sending.unresolved && <p role="status">{l("Постановка не подтверждена. Повтор запроса использует тот же идентификатор и не создаёт вторую копию очереди.", "Постановка не підтверджена. Повтор запиту використовує той самий ідентифікатор і не створює другу копію черги.")}</p>}
    {sending.error && <p role="alert">{sending.error} {settings && <button type="button" className="button" onClick={settings}>{l("Настройки", "Налаштування")}</button>}</p>}
    {selected.length > 1000 && <p role="alert">{l("Выберите до 1000 автомобилей за одну отправку.", "Виберіть до 1000 автомобілів за одне надсилання.")}</p>}
    <div className="launch-actions"><button type="button" className="button telegram-button" disabled={!sending.unresolved && !selected.length || selected.length > 1000 || query.isFetching || query.isError || sending.blocked || !write} onClick={() => void sending.send(selected, collection)}>{sending.unresolved ? l("Повторить запрос", "Повторити запит") : l("Отправить выбранные", "Надіслати вибрані")} ({sending.unresolved ? sending.intentCount : selected.length})</button>
      <button type="button" className="button" disabled={query.isFetching || sending.selectionLocked} onClick={() => { setSelection({ ids: [] }); void query.refetch(); void history.refetch(); }}>{l("Обновить предпросмотр", "Оновити попередній перегляд")}</button>
      <button type="button" className="button" autoFocus onClick={close}>{l("Закрыть", "Закрити")}</button></div>
  </dialog>;
}

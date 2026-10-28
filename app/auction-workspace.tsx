import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { AuctionRemovalDialog, type AuctionRemovalTarget } from "./auction-removal";
import { AuctionProjects } from "./auction-projects";
import { AuctionProjectEdit } from "./auction-project-edit";
import { AuctionTelegramPreview } from "./auction-telegram-preview";
import { TelegramLogo } from "./telegram-logo";
import { TelegramCarAction } from "./telegram-send";
import { TelegramProject } from "./telegram-project";
import { TelegramSettings } from "./telegram-settings";
import { TelegramAutomatic } from "./telegram-automatic";
import { TelegramReschedules } from "./telegram-reschedules";
import { AuctionRuns } from "./auction-runs";
import type { Locale } from "./translations";
import { AUCTION_QUERY_KEY, auctionTimeAt, watchAuctionCountdown, type AuctionLot, type AuctionCollection, type AuctionClockContext, type ProjectAuctionView, type SingleAuctionView } from "./auction-time-client";
import { latestProjectUpdate, ProjectUpdateStatus, updateActive } from "./scan-progress";
import { statusLabel } from "./activity-dialog";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import { SOURCE_CATALOG, sourceName, useSourceCatalog, type SourceCatalog } from "./source-catalog";
import { SourceContextMismatch } from "./source-navigation";
import { sourceListingLink, LISTING_VALIDATORS, type ListingValidators } from "./source-listing-links";
import { SiteVerificationNotice } from "./activity-dialog";
import type { ActivityRun } from "./activity-dialog";
import { PhotoPreview } from "./photo-preview";
import { SearchSelector, type SearchSelection } from "./search-selection";

export function AuctionSearchNotice({ run, projectId, locale }: { run: Pick<ActivityRun, "status" | "payload">; projectId: number; locale: Locale }) {
  const info = run.payload?.pagination?.[String(projectId)];
  if (!info) return null;
  const uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : "—";
  const partial = info.complete !== true && !updateActive(run.status);
  const reasons: Record<string, string> = {
    SEARCH_PAGE_LIMIT: l("Достигнут лимит страниц", "Досягнуто ліміту сторінок"),
    SEARCH_RESULTS_CHANGED: l("Выдача изменилась во время обхода. Запустите поиск повторно.", "Видача змінилася під час обходу. Запустіть пошук повторно."),
    SEARCH_INCOMPLETE: l("Конец выдачи не подтверждён", "Кінець видачі не підтверджено"),
    SEARCH_PAGE_NOT_READY: info.pages_visited === 0
      ? l("Не удалось прочитать первую страницу результатов", "Не вдалося прочитати першу сторінку результатів")
      : l("Следующая страница не загрузилась", "Наступна сторінка не завантажилася"),
    SEARCH_RESTORE_FAILED: l("Не удалось восстановить сохранённые фильтры. Общий поиск не запускался.", "Не вдалося відновити збережені фільтри. Загальний пошук не запускався."),
    SEARCH_RESTORE_UNSUPPORTED: l("Сайт не позволяет восстановить эти настройки поиска. Выберите фильтры повторно.", "Сайт не дозволяє відновити ці налаштування пошуку. Виберіть фільтри повторно."),
    SEARCH_FILTERS_UNSUPPORTED: l("Формат сохранённых фильтров не распознан. Поиск остановлен без замены условий.", "Формат збережених фільтрів не розпізнано. Пошук зупинено без заміни умов."),
    SOURCE_SEARCH_SCOPE_CHANGED: l("Применённые фильтры не совпали с сохранёнными. Поиск остановлен; прежние авто сохранены.", "Застосовані фільтри не збіглися зі збереженими. Пошук зупинено; попередні авто збережено."),
    SOURCE_ACCESS_DENIED: l("Сайт не предоставил доступ к выдаче", "Сайт не надав доступу до видачі"),
    SOURCE_BROWSER_UNAVAILABLE: l("Не удалось запустить фоновый браузер сайта", "Не вдалося запустити фоновий браузер сайту"),
    SOURCE_VISIBLE_BROWSER_UNAVAILABLE: l("Не удалось открыть окно подтверждения. Проверьте установку Chrome/Edge", "Не вдалося відкрити вікно підтвердження. Перевірте встановлення Chrome/Edge"),
    SOURCE_BACKGROUND_BROWSER_UNAVAILABLE: l("Не удалось свернуть окно сайта после проверки. Поиск остановлен; повторите обновление.", "Не вдалося згорнути вікно сайту після перевірки. Пошук зупинено; повторіть оновлення."),
    SEARCH_VERIFICATION_RESTART_REQUIRED: l("Проверка сайта завершена. Повторите поиск для полного результата", "Перевірку сайту завершено. Повторіть пошук для повного результату"),
    SOURCE_RATE_LIMITED: l("Сайт ограничил частоту запросов. Повторите позже.", "Сайт обмежив частоту запитів. Повторіть пізніше."),
    SOURCE_VERIFICATION_TIMEOUT: l("Истекло время ручной проверки или выбора cookies", "Сплив час ручної перевірки або вибору cookies"),
    SOURCE_BROWSER_CLOSED: l("Окно сайта было закрыто", "Вікно сайту було закрито"),
    CAPTCHA: l("Сайт повторно запросил проверку", "Сайт повторно запросив перевірку"),
  };
  const summary = info.read_summary;
  return <section className="activity-notice" aria-label={l("Результат обхода", "Результат обходу")}>
    <b>{partial ? l("Неполный поиск", "Неповний пошук") : info.complete ? l("Выбранный охват проверен", "Вибране охоплення перевірено") : l("Обход выдачи", "Обхід видачі")}</b>
    <p>{info.mode === "FIRST_PAGE" ? l("Первая страница", "Перша сторінка") : `${l("Страниц", "Сторінок")}: ${count(info.pages_visited)} / ${count(info.total_pages)}`} · {l("Прочитано лотов", "Прочитано лотів")}: {count(info.ads_seen)}</p>
    {summary && <p>{l("С датой", "З датою")}: {count(summary.dated)} · {l("С точным временем", "З точним часом")}: {count(summary.exact_time)} · {l("Без назначенной даты", "Без призначеної дати")}: {count(summary.without_date)} · {l("Расписание не подтверждено", "Розклад не підтверджено")}: {count(summary.unrecognized_schedule)} · {l("Вне рынка USA", "Поза ринком USA")}: {count(summary.unsupported_market)}</p>}
    {summary && Number.isSafeInteger(summary.without_date) && summary.without_date > 0 && <p>{l(
      "Новые лоты без назначенной даты торгов не сохраняются. Они появятся в проекте после назначения даты и повторного поиска.",
      "Нові лоти без призначеної дати торгів не зберігаються. Вони з’являться в проєкті після призначення дати й повторного пошуку.")}</p>}
    {info.stop_reason && <p>{reasons[info.stop_reason] || l("Обход остановлен. Подробности — в ошибке запуска.", "Обхід зупинено. Подробиці — у помилці запуску.")}</p>}
    {run.payload?.failures?.filter(failure => failure.project_id === projectId).map((failure, index) =>
      <details className="auction-search-error" key={index}><summary>{l("Подробности ошибки", "Подробиці помилки")} · {failure.code}</summary>
        <pre>{failure.technical || failure.code}</pre></details>)}
    {info.browser_notice === 'MANUAL_MINIMIZE' && <p role="status">{l("Окно сайта можно свернуть вручную: автоматическое скрытие недоступно. Это не останавливает поиск в проверенной сессии.", "Вікно сайту можна згорнути вручну: автоматичне приховування недоступне. Це не зупиняє пошук у перевіреній сесії.")}</p>}
    {partial && <small>{l("Прочитанные данные сохранены; прежние лоты не исключаются из-за неполного обхода.", "Прочитані дані збережено; попередні лоти не виключаються через неповний обхід.")}</small>}
  </section>;
}

const text = (value: unknown) => typeof value === "string" || typeof value === "number" ? String(value) : null;
const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const labels: Record<string, [string, string]> = {
  "FRONT END": ["Передняя часть", "Передня частина"], "REAR END": ["Задняя часть", "Задня частина"], REAR: ["Задняя часть", "Задня частина"],
  SIDE: ["Боковая часть", "Бічна частина"], "ALL OVER": ["По всему кузову", "По всьому кузову"], MECHANICAL: ["Механические", "Механічні"],
  "WATER/FLOOD": ["Вода / затопление", "Вода / затоплення"], FLOOD: ["Затопление", "Затоплення"], HAIL: ["Град", "Град"],
  "NORMAL WEAR": ["Обычный износ", "Звичайний знос"], "NORMAL WEAR & TEAR": ["Обычный износ", "Звичайний знос"],
  "NOT ACTUAL": ["Не фактический", "Не фактичний"], ACTUAL: ["Фактический", "Фактичний"],
  "EXCEEDS MECHANICAL LIMITS": ["Превышен предел одометра", "Перевищено межу одометра"],
  "INOPERABLE DIGITAL DASH": ["Цифровая панель не работает", "Цифрова панель не працює"], EXEMPT: ["Освобождён от подтверждения", "Звільнений від підтвердження"],
  STARTS: ["Двигатель запускается", "Двигун запускається"], STATIONARY: ["Без движения (Stationary)", "Без руху (Stationary)"],
  ENHANCED: ["Подготовка автомобиля", "Підготовка автомобіля"], "ENGINE START": ["Engine Start Program", "Engine Start Program"],
};
export function auctionValue(raw: unknown, locale: Locale): string {
  const value = text(raw);
  if (!value) return locale === "uk" ? "Немає даних" : "Нет данных";
  return labels[value.toUpperCase().replaceAll("_", " ")]?.[locale === "uk" ? 1 : 0] || value;
}

/** A saved search URL is not a link to a vehicle. Never infer a detail URL. */
export const auctionListingLink = sourceListingLink;

export function auctionDate(lot: AuctionLot, context: AuctionClockContext, locale: Locale): string {
  const value = lot.time.local_date;
  if (!value) return locale === "uk" ? "Дата невідома" : "Дата неизвестна";
  const parsed = new Date(`${value}T12:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return value;
  const parts = new Intl.DateTimeFormat(locale === "uk" ? "uk-UA" : "ru-RU", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" }).formatToParts(parsed);
  const part = (key: string) => parts.find((item) => item.type === key)?.value || "";
  return `${lot.time.is_today === true ? (locale === "uk" ? "Сьогодні · " : "Сегодня · ") : ""}${part("day")} ${part("month")}${value.slice(0, 4) === context.local_date.slice(0, 4) ? "" : ` ${part("year")}`}`;
}

export function AuctionCard({ lot, context, locale, open, imageUrl, openListing, nowMs, onRemove, historicalAt, historicalRoundId, telegramAction, catalog = SOURCE_CATALOG, listingValidators = LISTING_VALIDATORS }: {
  lot: AuctionLot; context: AuctionClockContext; locale: Locale; open?: () => void; imageUrl: (path: string) => string; openListing?: (url: string) => void;
  catalog?: SourceCatalog;
  listingValidators?: ListingValidators;
  nowMs?: number;
  historicalAt?: string;
  historicalRoundId?: string | null;
  telegramAction?: ReactNode;
  onRemove?: (trigger: HTMLButtonElement) => void;
}) {
  const uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const name = sourceName(lot.source_key, catalog);
  const value = (raw: unknown) => auctionValue(raw, locale);
  const format = (raw: number) => new Intl.NumberFormat(uk ? "uk-UA" : "ru-RU").format(raw);
  const mileage = lot.details.mileage as Record<string, unknown> | undefined;
  const odo = mileage && typeof mileage === "object" ? number(mileage.value) : null;
  const unit = mileage && ["mi", "km"].includes(String(mileage.unit)) ? String(mileage.unit) : null;
  const link = auctionListingLink(lot, catalog, listingValidators);
  const unavailable = lot.project_search_status === "NOT_FOUND_IN_SEARCH";
  const frozen = !!historicalAt || unavailable;
  const time = auctionTimeAt(lot, nowMs ?? Date.parse(context.generated_at));
  const upcoming = time.time_state === "UPCOMING_START" || time.time_state === "UPCOMING_END";
  const passed = time.time_state === "SCHEDULED_START_PASSED" || time.time_state === "SCHEDULED_END_PASSED";
  const phase = text(lot.details.auction_phase);
  const phaseLabel = phase === "LIVE" ? l("Аукцион идёт", "Аукціон триває") : phase === "CLOSED" ? l("Торги завершены", "Торги завершені") : phase === "CANCELLED" ? l("Аукцион отменён", "Аукціон скасовано") : null;
  const event = time.event_kind === "START" ? l("Начало аукциона", "Початок аукціону") : time.event_kind === "END" ? l("Окончание торгов", "Завершення торгів") : l("Время события · тип неизвестен", "Час події · тип невідомий");
  const clock = time.precision === "EXACT" && time.local_datetime ? time.local_datetime.slice(11, 16) : "—";
  const minutes = upcoming && number(time.remaining_seconds) !== null ? Math.ceil(time.remaining_seconds! / 60) : null;
  return <article className="auction-card" aria-label={lot.title || `${name} № ${lot.source_listing_id}`}>
    {lot.image ? <PhotoPreview src={imageUrl(lot.image)} title={lot.title || l("Автомобиль", "Автомобіль")} locale={locale} /> : <div className="auction-photo"><span>{l("Нет фото", "Немає фото")}</span></div>}
    <div className="auction-info"><small>{name} № {lot.source_listing_id}</small>
      <h2>{open && !frozen ? <button type="button" onClick={open}>{lot.title || l("Без названия", "Без назви")}</button> : lot.title || l("Без названия", "Без назви")}</h2>
      {unavailable && <p className="auction-snapshot">{l("Недоступен в этом проекте: не найден при полном поиске. Это не подтверждение продажи. Ниже — последние известные сведения.", "Недоступний у цьому проєкті: не знайдений під час повного пошуку. Це не підтвердження продажу. Нижче — останні відомі дані.")}</p>}
      <div className="auction-facts"><div><span>{l("Показание одометра", "Показання одометра")}</span><strong>{odo !== null ? format(odo) : "—"} <small>{unit || ""}</small></strong>
        <p>{value(mileage?.accuracy)}</p>{odo !== null && unit === "mi" && text(mileage?.accuracy)?.toUpperCase() === "ACTUAL" && <p>≈ {format(Math.round(odo * 1.609344))} км</p>}</div>
        <div><span>{l("Повреждения", "Пошкодження")}</span><strong>{value(lot.details.primary_damage)}</strong>{lot.details.secondary_damage != null && <p>{l("Доп.", "Дод.")}: {value(lot.details.secondary_damage)}</p>}</div></div>
      <div className="auction-condition"><b>{value(lot.details.engine_status)}</b><span>{lot.details.keys === true ? l("Ключи есть", "Ключі є") : lot.details.keys === false ? l("Ключей нет", "Ключів немає") : l("Ключи: нет данных", "Ключі: немає даних")}</span>
        {lot.details.title != null && <span>{l("Документ", "Документ")}: {value(lot.details.title)}</span>}</div>
      <p className="auction-snapshot">{l("Сохранённые сведения — не гарантия состояния машины.", "Збережені відомості — не гарантія стану автомобіля.")}</p>
      <p className="auction-snapshot">{historicalAt ? l("Сведения на момент проверки", "Відомості на момент перевірки") : l("Последняя проверка источника", "Остання перевірка джерела")}: {historicalAt || lot.round?.last_observed_at || lot.schedule.observed_at || "—"}</p>
      {frozen && lot.image && <p className="auction-snapshot">{l("Фото по сохранённой ссылке; изображение не архивируется и может измениться на сайте.", "Фото за збереженим посиланням; зображення не архівується й може змінитися на сайті.")}</p>}
    </div>
    <div className="auction-time"><span>{l("Аукцион", "Аукціон")}</span><b>{unavailable ? l("Последнее известное расписание", "Останній відомий розклад") : historicalAt ? (phaseLabel ? `${l("На момент проверки", "На момент перевірки")}: ${phaseLabel}` : event) : phaseLabel || (passed ? l("Время по расписанию наступило", "Час за розкладом настав") : event)}</b>
      <div className="auction-date" title={time.local_date || ""}>{auctionDate(frozen ? {...lot,time:{...lot.time,is_today:false}} : lot, context, locale)}</div><strong className="auction-clock">{clock}</strong>
      {!frozen && !phaseLabel && minutes !== null && <div className="auction-countdown">{time.event_kind === "END" ? l("До окончания", "До завершення") : l("Через", "Через")} {Math.floor(minutes / 60)} {l("ч", "год")} {minutes % 60} {l("мин", "хв")}</div>}
      {!frozen && !phaseLabel && passed && <p>{l("Текущий этап не подтверждён", "Поточний етап не підтверджено")}</p>}
      {time.precision !== "EXACT" && <p>{time.precision === "DATE_ONLY" ? l("Точное время не указано", "Точний час не вказано") : l("Расписание не подтверждено", "Розклад не підтверджено")}</p>}
      {time.time_state === "LOCAL_DAY_UNCERTAIN" && <p>{l("Локальный день не определён", "Локальний день не визначено")}</p>}
      {!time.local_date && lot.schedule.local_date && <p>{l("Дата источника", "Дата джерела")}: {lot.schedule.local_date}{lot.schedule.source_timezone ? ` · ${lot.schedule.source_timezone}` : ""}</p>}
      <p>{l("Время компьютера", "Час комп’ютера")}<br />{context.computer_timezone}</p>
      {lot.schedule.change_kind === "RESCHEDULED" && <p>{l("Расписание перенесено", "Розклад перенесено")}</p>}
      {lot.schedule.raw && <p>{l("Метка источника", "Мітка джерела")}: {lot.schedule.raw}{lot.schedule.raw_timezone ? ` · ${lot.schedule.raw_timezone}` : ""}</p>}
      {phase && !phaseLabel && <p>{l("Этап по источнику", "Етап за джерелом")}: {phase}</p>}
      {historicalAt && lot.money_values.some(money => money.meaning === "CURRENT_BID" && (!historicalRoundId || money.auction_round !== historicalRoundId)) && <p>{l("Ставка не показана: связь с раундом этих торгов не подтверждена.", "Ставку не показано: зв’язок із раундом цих торгів не підтверджено.")}</p>}
      {lot.money_values.filter((money) => historicalAt ? money.meaning !== "CURRENT_BID" || !!historicalRoundId && money.auction_round === historicalRoundId
        : !money.auction_round || money.auction_round === lot.round?.id).map((money, index) => <div className="auction-money" key={index}>
        <span>{money.meaning === "CURRENT_BID" ? (frozen ? l("Ставка на момент проверки", "Ставка на момент перевірки") : l("Текущая ставка · снимок", "Поточна ставка · знімок")) : money.meaning === "BUY_NOW" ? "Buy It Now" : money.meaning === "ESTIMATED_RETAIL" ? l("Оценочная стоимость, не ставка", "Оціночна вартість, не ставка") : money.meaning === "SALE_TOTAL" ? l("Цена продажи", "Ціна продажу") : money.meaning}</span>
        <b>{money.state === "KNOWN" && money.amount_minor !== null ? `${format(money.currency === "USD" ? money.amount_minor / 100 : money.amount_minor)} ${money.currency}` : money.state === "ON_REQUEST" ? l("По запросу", "За запитом") : l("Нет данных", "Немає даних")}</b></div>)}
      {!historicalAt && <div className="auction-car-actions">
        {!frozen && (link ? <a className="button auction-action-icon auction-listing-button" href={link} target="_blank" rel="noopener noreferrer"
          aria-label={`${l("Объявление", "Оголошення")}: ${lot.title || `${name} № ${lot.source_listing_id}`}`} title={l("Объявление", "Оголошення")}
          onClick={openListing ? (event) => { event.preventDefault(); openListing(link); } : undefined}>
          <svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18M5 6.5h14M5 17.5h14" /></svg>
        </a> : <p>{l("Ссылка на лот недоступна", "Посилання на лот недоступне")}</p>)}
        {telegramAction}
        {!frozen && onRemove && <button type="button" className="button auction-action-icon auction-remove-button" aria-haspopup="dialog"
          aria-label={`${l("Удалить из проекта", "Видалити з проєкту")}: ${lot.title || `${name} № ${lot.source_listing_id}`}`} title={l("Удалить из проекта", "Видалити з проєкту")}
          onClick={(event) => onRemove(event.currentTarget)}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg></button>}
      </div>}
    </div>
  </article>;
}

function ReadState({ error, loading, retry, locale }: { error: boolean; loading: boolean; retry: () => void; locale: Locale }) {
  if (error) return <section className="panel auction-read-error" role="alert"><p>{locale === "uk" ? "Не вдалося прочитати збережені дані. Показані раніше отримані відомості можуть бути застарілими." : "Не удалось прочитать сохранённые данные. Показанные ранее сведения могут быть устаревшими."}</p><button type="button" className="button" onClick={retry}>{locale === "uk" ? "Повторити" : "Повторить"}</button></section>;
  return loading ? <p role="status">{locale === "uk" ? "Завантаження збережених даних…" : "Загрузка сохранённых данных…"}</p> : null;
}

export function AuctionWorkspace({ source, view, projectId, carId, locale, projects, projectsLoading, projectsError, retryProjects, runs, runsLoading, runsError, retryRuns, read, navigate, imageUrl, openListing, sourceTestMode=false, canSearch=false, busyProjects=[], write, refreshProjects, removeProjectCar, requestProjectDeletion, listingValidators = LISTING_VALIDATORS }: SourceWorkspaceProps & {
  listingValidators?: ListingValidators;
}) {
  const uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const catalog = useSourceCatalog();
  const name = sourceName(source, catalog);
  const searchExample = catalog.find((item) => item.key === source)?.search_url_example;
  const selectionHelp = catalog.find((item) => item.key === source)?.search_selection_help?.[locale];
  const [collection, setCollection] = useState<AuctionCollection>("active");
  const collectionKey = collection === "active" ? false : collection === "today" ? true : collection;
  const [nowMs, setNowMs] = useState(Date.now);
  useEffect(() => watchAuctionCountdown(setNowMs), []);
  const [showCreate,setShowCreate] = useState(false);
  const [createdProject, setCreatedProject] = useState<number | null>(null);
  const [recipientBusy, setRecipientBusy] = useState(false), [creationOpening, setCreationOpening] = useState(false);
  const [creationClosing, setCreationClosing] = useState(false);
  const createDialog = useRef<HTMLDialogElement>(null), createOrigin = useRef<HTMLButtonElement>(null), createSubmitting = useRef(false);
  const [draftId,setDraftId] = useState("");
  const [draftName,setDraftName] = useState("");
  const [selection,setSelection] = useState<SearchSelection|null>(null);
  const draftUrl = selection?.source_key === source && selection.can_use && selection.status === 'CLOSED' ? selection.snapshot?.creation_url || '' : '';
  const [pageMode,setPageMode] = useState("ALL_PAGES");
  const [refreshing,setRefreshing] = useState<number[]>([]);
  const [commandError,setCommandError] = useState<string|null>(null);
  const [removal, setRemoval] = useState<AuctionRemovalTarget | null>(null);
  const [editing, setEditing] = useState<number | null>(null);
  const [showTelegramPreview, setShowTelegramPreview] = useState(false);
  const [telegramSettingsProject, setTelegramSettingsProject] = useState<number | null>(null);
  const telegramOrigin = useRef<HTMLButtonElement>(null);
  const editOrigin = useRef<HTMLButtonElement>(null);
  const removalOrigin = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!showCreate) return;
    const node = createDialog.current, origin = createOrigin.current, fallback = headingRef.current;
    node?.showModal();
    return () => { node?.close(); if (origin?.isConnected) origin.focus(); else fallback?.focus(); };
  }, [showCreate]);
  const client=useQueryClient();
  const remove = useMutation({
    mutationFn: async (target: AuctionRemovalTarget) => {
      if (!removeProjectCar) throw new Error("Project removal unavailable");
      await removeProjectCar(target.projectId, target.carId);
    },
    onSuccess: (_, target) => {
      // Only remove the selected relation, after the server confirms it.
      client.setQueriesData<ProjectAuctionView>({ queryKey: [...AUCTION_QUERY_KEY, "project", source, target.projectId] },
        (data) => data && { ...data, lots: data.lots.filter((lot) => lot.id !== target.carId) });
      for (const key of [AUCTION_QUERY_KEY, ["projects"], ["project", target.projectId], ["project-cars", target.projectId], ["favorites"], ["scans"]]) {
        void client.invalidateQueries({ queryKey: key });
      }
      setRemoval(null);
    },
  });
  const chooseRemoval = (id: number, lot: AuctionLot, trigger: HTMLButtonElement) => {
    removalOrigin.current = trigger;
    remove.reset();
    setRemoval({ projectId: id, carId: lot.id, title: lot.title || `${name} № ${lot.source_listing_id}`,
      projectName: projects.find((project) => project.id === id)?.name || `#${id}` });
  };
  const create=useMutation({mutationFn:async()=>{
    if(!sourceTestMode||!write||!draftUrl) throw new Error("Confirmed search selection required");
    const project=await write<{id:number}>("/projects",{source_key:source,name:draftName.trim(),search_url:draftUrl.trim(),search_selection_id:selection?.id,search_draft_id:draftId,search_page_mode:pageMode,auto_update:false});
    return project;
  },onSuccess:async(project)=>{
    void client.invalidateQueries({queryKey:["projects"]});
    void client.invalidateQueries({queryKey:["launch-options"]});
    setCreatedProject(project.id);
  },onError:(error)=>{
    const code=(error as {code?:string})?.code;
    setCommandError(code==='invalid_source_search_url'
      ? `${l("Не удалось создать проект: используйте ссылку поиска с применёнными фильтрами, а не страницу каталога или отдельного автомобиля.","Не вдалося створити проєкт: використайте посилання пошуку із застосованими фільтрами, а не сторінку каталогу чи окремого автомобіля.")}${searchExample?` ${l("Пример","Приклад")}: ${searchExample}`:''}`
      : code==='search_selection_changed' ? l("Условия изменились или выбор не завершён. Повторно выберите и подтвердите фильтры.","Умови змінилися або вибір не завершено. Повторно виберіть та підтвердьте фільтри.")
      : code==='project_name_or_url_exists' ? l("Проект с таким названием или ссылкой уже существует на этом сайте.","Проєкт із такою назвою або посиланням уже існує на цьому сайті.")
      : code==='project_limit_reached' ? l("Достигнут лимит проектов для вашей учётной записи.","Досягнуто ліміту проєктів для вашого облікового запису.")
      : l("Не удалось создать проект. Проверьте название и выбранные условия поиска.","Не вдалося створити проєкт. Перевірте назву та вибрані умови пошуку."));
  },onSettled:()=>{createSubmitting.current=false;}});
  const finishCreation = (startSearch: boolean) => {
    if (createdProject === null || recipientBusy) return;
    const id = createdProject;
    setShowCreate(false);setCreatedProject(null);setDraftName("");setSelection(null);
    navigate("project", id);
    if (startSearch && canSearch && refreshProjects) void refresh([id], 'created');
    else setCommandError(startSearch ? l("Проект создан. Поиск недоступен: проверьте доступность сайта и лицензии.","Проєкт створено. Пошук недоступний: перевірте доступність сайту та ліцензії.")
      : l("Проект сохранён. Поиск пока не запущен; его можно запустить кнопкой «Обновить данные».","Проєкт збережено. Пошук поки не запущено; його можна запустити кнопкою «Оновити дані»."));
  };
  const dismissCreation = async () => {
    if (createSubmitting.current || create.isPending || recipientBusy || creationOpening || creationClosing) return;
    if (createdProject !== null) { finishCreation(false); return; }
    setCreationClosing(true);
    try {
      if (write && selection && ['SELECTING','READY'].includes(selection.status))
        await write(`/source-search/${source}/selection`, {action:'CANCEL',draft_id:draftId,selection_id:selection.id});
      setShowCreate(false);setSelection(null);
    } catch { setCommandError(l("Не удалось закрыть выбор. Закройте окно сайта и повторите отмену.","Не вдалося закрити вибір. Закрийте вікно сайту й повторіть скасування.")); }
    finally { setCreationClosing(false); }
  };
  const refresh=async(ids:number[],context?:'created'|'edited')=>{
    if(!sourceTestMode||!canSearch||!refreshProjects) return;
    setCommandError(null);setRefreshing(ids);
    try{await refreshProjects(ids);}catch{setCommandError(context==='edited'
      ? l("Фильтры сохранены, но запуск поиска не подтверждён. Нажмите «Обновить данные», чтобы повторить.","Фільтри збережено, але запуск пошуку не підтверджений. Натисніть «Оновити дані», щоб повторити.")
      : context==='created'
      ? l("Проект создан, но поиск не запущен. Нажмите «Обновить данные», чтобы повторить.","Проєкт створено, але пошук не запущено. Натисніть «Оновити дані», щоб повторити.")
      : l("Не удалось запустить обновление. Проверьте доступность сайта и лицензии.","Не вдалося запустити оновлення. Перевірте доступність сайту та ліцензії."));}finally{setRefreshing([]);}
  };
  const projectStatus=(id:number)=>latestProjectUpdate(runs,id,projects)?.status || (busyProjects.includes(id)?"ACTIVE":null);
  const refreshButton=(id:number,compact=false)=>{
    const status=projectStatus(id), busy=!!status&&updateActive(status);
    const label=refreshing.includes(id)?l("Добавляем в очередь…","Додаємо до черги…"):busy?statusLabel(status!,uk):l("Обновить данные","Оновити дані");
    return <button type="button" className={compact?"icon-button auction-project-refresh":"button auction-project-icon auction-project-refresh"} aria-label={compact?`${l("Обновить данные","Оновити дані")}: ${projects.find(project=>project.id===id)?.name || `#${id}`}`:l("Обновить данные","Оновити дані")} title={label} aria-busy={refreshing.includes(id)||busy} disabled={!canSearch||busy||busyProjects.includes(id)||refreshing.includes(id)} onClick={()=>{void refresh([id]);}}><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M20 4v6h-6M19.5 10a8 8 0 1 0-.5 6" /></svg></button>;
  };
  const updateStatus=(id:number,showEmpty=false)=>{
    const status=projectStatus(id);
    return <>{status&&status!=="SUCCEEDED"?<ProjectUpdateStatus projectId={id} status={status} locale={locale} />:!status&&showEmpty&&<span className="auction-project-status-empty">{runsLoading||runsError?l("Статус уточняется","Стан уточнюється"):l("Нет данных о запуске","Немає даних про запуск")}</span>}{runsError&&<small role="status">{l("Не удалось обновить состояние запуска","Не вдалося оновити стан запуску")}</small>}</>;
  };
  const ownProjects = projects.filter((project) => project.source_key === source);
  const editButton=(id:number,compact=false)=><button type="button" className={compact?'icon-button auction-project-edit-button':'button auction-project-icon auction-project-edit-button'}
    aria-label={`${l('Изменить фильтры','Змінити фільтри')}: ${ownProjects.find(project=>project.id===id)?.name || `#${id}`}`}
    title={l('Изменить фильтры','Змінити фільтри')} disabled={!canSearch || busyProjects.includes(id) || updateActive(projectStatus(id) || '')}
    onClick={event=>{ editOrigin.current=event.currentTarget;setEditing(id); }}>
    <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9.6 3h4.8l.5 2.4 1.6.9 2.3-.7 2.4 4.2-1.8 1.6v1.8l1.8 1.6-2.4 4.2-2.3-.7-1.6.9-.5 2.4H9.6l-.5-2.4-1.6-.9-2.3.7-2.4-4.2 1.8-1.6v-1.8L2.8 9.8l2.4-4.2 2.3.7 1.6-.9L9.6 3Z" /><circle cx="12" cy="12" r="3" /></svg></button>;
  const editor=editing!==null && sourceTestMode && write && <AuctionProjectEdit key={`${source}:${editing}`} projectId={editing} source={source} locale={locale}
    canSearch={canSearch} busy={busyProjects.includes(editing) || updateActive(projectStatus(editing) || '')} read={read} write={write}
    returnFocus={editOrigin} fallbackFocus={headingRef} close={()=>setEditing(null)} saved={()=>{
      const id=editing;setEditing(null);void refresh([id],'edited');
    }} />;
  const collectionPath = (id: number | null) => `/projects/${id}/auction-lots?today_only=${collection === "today"}&collection=${collection}`;
  const projectQuery = useQuery<ProjectAuctionView>({ queryKey: [...AUCTION_QUERY_KEY, "project", source, projectId, collectionKey],
    queryFn: () => read(collectionPath(projectId)), enabled: view === "project" && projectId !== null, retry: false, refetchOnMount: "always" });
  const carQuery = useQuery<SingleAuctionView>({ queryKey: [...AUCTION_QUERY_KEY, "car", source, projectId, carId],
    queryFn: () => read(`/cars/${carId}/auction${projectId ? `?project_id=${projectId}` : ""}`), enabled: view === "car" && carId !== null, retry: false, refetchOnMount: "always" });
  const collectionQueries = useQueries({ queries: view === "projects" && collection !== "active" ? ownProjects.map((project) => ({
    queryKey: [...AUCTION_QUERY_KEY, "project", source, project.id, collectionKey], queryFn: () => read<ProjectAuctionView>(collectionPath(project.id)), retry: false,
  })) : [] });
  const collectionLabels: Record<AuctionCollection, string> = {
    active: view === "project" ? l("Все сохранённые", "Усі збережені") : l("Проекты", "Проєкти"),
    today: l("Сегодня", "Сьогодні"), future: l("Будущие", "Майбутні"), undated: l("Без даты", "Без дати"), archive: l("Архив", "Архів"),
  };
  const emptyCollection = collection === "today" ? l("Сегодня сохранённых аукционов нет", "Сьогодні збережених аукціонів немає")
    : collection === "future" ? l("Будущих сохранённых аукционов нет", "Майбутніх збережених аукціонів немає")
    : collection === "undated" ? l("Лотов без подтверждённой даты нет", "Лотів без підтвердженої дати немає")
    : collection === "archive" ? l("В архиве этого проекта лотов нет", "В архіві цього проєкту лотів немає")
    : l("Сохранённых лотов нет", "Збережених лотів немає");
  const collectionNotice = collection !== "active" && <p className="auction-snapshot">{collection === "archive"
    ? l("Архив этого проекта: лоты, не найденные при полном поиске. Продажа или завершение торгов не подтверждены; показаны последние известные сведения.", "Архів цього проєкту: лоти, не знайдені під час повного пошуку. Продаж або завершення торгів не підтверджені; показано останні відомі дані.")
    : collection === "undated" ? l("Подтверждённой даты нет. Исходная метка источника сохранена; такие лоты не входят в «Сегодня».", "Підтвердженої дати немає. Початкову мітку джерела збережено; такі лоти не входять до «Сьогодні».")
    : l("Локальная подборка сохранённых данных по часовому поясу компьютера. Переключение не запускает поиск на сайте.", "Локальна добірка збережених даних за часовим поясом комп’ютера. Перемикання не запускає пошук на сайті.")}</p>;
  const tabs = <div className="auction-tabs" aria-label={l("Выборка лотов", "Вибірка лотів")}>{(Object.keys(collectionLabels) as AuctionCollection[]).map(key =>
    <button key={key} type="button" className={collection === key ? "active" : ""} aria-pressed={collection === key} onClick={() => setCollection(key)}>{collectionLabels[key]}</button>)}</div>;
  const openTelegramSettings = (id: number) => {
    setTelegramSettingsProject(id); setShowTelegramPreview(false); navigate("source-settings");
  };
  const telegramAction = (id: number, lot: AuctionLot) => <TelegramCarAction key={`${source}:${id}:${lot.id}`} source={source} projectId={id} carId={lot.id} collection={lot.project_search_status === "NOT_FOUND_IN_SEARCH" ? "archive" : view === "project" ? collection : "active"} title={lot.title || `#${lot.source_listing_id}`} locale={locale} read={read} write={write} settings={() => openTelegramSettings(id)} />;
  const cards = (data: ProjectAuctionView) => data.lots.filter((lot) => lot.source_key === source).map((lot) => <AuctionCard key={lot.id} lot={lot} context={data} nowMs={nowMs} locale={locale} catalog={catalog} listingValidators={listingValidators} imageUrl={imageUrl} openListing={openListing} open={() => navigate("car", data.project_id, lot.id)} telegramAction={telegramAction(data.project_id, lot)} onRemove={removeProjectCar ? (trigger) => chooseRemoval(data.project_id, lot, trigger) : undefined} />);
  const heading = (title: string, statusProjectId?: number) => <><div className="title-row source-title"><div className="auction-heading-main"><div className="auction-heading-line"><h1 ref={headingRef} tabIndex={-1}>{title}</h1>{statusProjectId != null && <div className="auction-heading-status">{updateStatus(statusProjectId)}</div>}</div><p>{sourceTestMode?l("Тестовый поиск · фоновое обновление · выбор фильтров в окне сайта","Тестовий пошук · фонове оновлення · вибір фільтрів у вікні сайту"):l("Сохранённые данные · поиск не запускается", "Збережені дані · пошук не запускається")}</p></div></div>{removal && <AuctionRemovalDialog target={removal} locale={locale} pending={remove.isPending} failed={remove.isError} returnFocus={removalOrigin} fallbackFocus={headingRef} confirm={() => { if (!remove.isPending) remove.mutate(removal); }} close={() => { if (!remove.isPending) setRemoval(null); }} />}</>;
  const errorNotice=<>{editor}{commandError&&!showCreate&&<p className="warning-text" role="alert">{commandError}</p>}</>;
  if (view === "source-settings") return <>{heading(`${l("Настройки сайта", "Налаштування сайту")} · ${name}`)}<section className="panel"><p>{sourceTestMode?l("Фильтры выбираются в окне сайта и сохраняются в черновик. Создание проекта доступно только при поддержанном восстановлении поиска. Охват выбирается при создании. Автоматические обновления отключены. Автоотправка Telegram включается отдельно для каждого проекта.","Фільтри вибираються у вікні сайту та зберігаються в чернетці. Створення проєкту доступне лише за підтриманого відновлення пошуку. Охоплення вибирається під час створення. Автоматичні оновлення вимкнені. Автонадсилання Telegram вмикається окремо для кожного проєкту."):l("Доступен просмотр сохранённых данных. Настройки сбора будут подключены вместе с модулем поиска.", "Доступний перегляд збережених даних. Налаштування збору буде підключено разом із модулем пошуку.")}</p></section>
    {write && <TelegramSettings key={`${source}:${telegramSettingsProject || ""}`} source={source} initialProjectId={telegramSettingsProject} locale={locale} projects={ownProjects} projectsLoading={projectsLoading} projectsError={projectsError} retryProjects={retryProjects} read={read} write={write} imageUrl={imageUrl} />}</>;
  if (view === "scans") return <>{heading(`${l("История запусков", "Історія запусків")} · ${name}`)}<AuctionRuns source={source} locale={locale} runs={runs} projects={projects}
    runsLoading={runsLoading} runsError={runsError} retryRuns={retryRuns} sourceTestMode={sourceTestMode} write={write} read={read} navigate={navigate}
    renderSnapshot={(view, recordedAt) => <AuctionCard lot={view.lot} context={view} historicalAt={recordedAt} historicalRoundId={view.captured_round_id} locale={locale} imageUrl={imageUrl} />}
    renderNotice={(run, id) => <AuctionSearchNotice run={run} projectId={id} locale={locale} />} /></>;
  if (view === "car") {
    const lot = carQuery.data?.lot;
    if (lot && lot.source_key !== source) return <SourceContextMismatch source={lot.source_key} projectId={projectId} carId={carId || undefined} locale={locale} />;
    return <><button type="button" className="back" onClick={() => navigate(projectId ? "project" : "projects", projectId)}>{l("← Назад к проекту", "← Назад до проєкту")}</button>{heading(l("Сохранённые сведения", "Збережені відомості"))}<ReadState error={carQuery.isError} loading={carQuery.isPending} retry={() => { void carQuery.refetch(); }} locale={locale} />
      {lot && <><AuctionCard lot={lot} nowMs={nowMs} context={carQuery.data!} locale={locale} catalog={catalog} listingValidators={listingValidators} imageUrl={imageUrl} openListing={openListing} telegramAction={projectId !== null ? telegramAction(projectId, lot) : undefined} /><section className="panel auction-raw"><h2>{l("Сведения источника", "Відомості джерела")}</h2><dl>{Object.entries(lot.details).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value == null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></section></>}</>;
  }
  if (view === "project") {
    const data = projectQuery.data;
    if (data && data.source_key !== source) return <SourceContextMismatch source={data.source_key} projectId={projectId} locale={locale} />;
    return <><button type="button" className="back" onClick={() => navigate("projects")}>{l("← Назад к проектам", "← Назад до проєктів")}</button>{heading(ownProjects.find((project) => project.id === projectId)?.name || `${l("Проект", "Проєкт")} № ${projectId}`, projectId ?? undefined)}{errorNotice}{projectId!==null&&runs.map(run=><SiteVerificationNotice key={run.id} run={run} projectId={projectId} locale={locale} />)}{projectId!==null&&runs.filter(run=>run.payload?.pagination?.[String(projectId)]).slice(0,1).map(run=><AuctionSearchNotice key={run.id} run={run} projectId={projectId} locale={locale} />)}<div className="auction-project-toolbar">{tabs}
      {projectId !== null && <div className="project-actions auction-project-controls">
        {sourceTestMode && write && editButton(projectId)}
        {sourceTestMode && refreshButton(projectId)}
        <button type="button" className="button telegram-button telegram-icon-button" aria-label="Telegram" title="Telegram" aria-haspopup="dialog" onClick={event => { telegramOrigin.current = event.currentTarget; setShowTelegramPreview(true); }}><TelegramLogo /></button>
      </div>}</div>
      {showTelegramPreview && projectId !== null && <AuctionTelegramPreview key={`telegram-preview:${source}:${projectId}:${collection}`} collection={collection} settings={() => openTelegramSettings(projectId)} source={source} projectId={projectId} projectName={ownProjects.find(project => project.id === projectId)?.name || `#${projectId}`}
        locale={locale} read={read} write={write} imageUrl={imageUrl} openCalendar={write ? url => write("/desktop/open-external", {url}) : undefined} nowMs={nowMs} returnFocus={telegramOrigin} fallbackFocus={headingRef} close={() => setShowTelegramPreview(false)} />}
      {write && projectId !== null && <TelegramAutomatic compact source={source} projectId={projectId} locale={locale} read={read} write={write} />}
      {write && projectId !== null && <TelegramReschedules compact source={source} projectId={projectId} locale={locale} read={read} write={write} />}
      {collectionNotice}<ReadState error={projectQuery.isError} loading={projectQuery.isPending} retry={() => { void projectQuery.refetch(); }} locale={locale} />{data && <><p className="auction-snapshot">{l("День и время компьютера", "День і час комп’ютера")}: {data.local_date} · {data.computer_timezone} · {l("Лотов", "Лотів")}: {data.lots.length}</p><div className="auction-list">{cards(data)}</div>{!projectQuery.isError && !data.lots.length && <section className="panel">{emptyCollection}</section>}</>}</>;
  }
  return <>{heading(`${l("Проекты", "Проєкти")} · ${name}`)}{sourceTestMode&&write&&<button type="button" className="button" onClick={event=>{createOrigin.current=event.currentTarget;setCommandError(null);setDraftId(crypto.randomUUID().replaceAll('-',''));setSelection(null);setDraftName("");setCreatedProject(null);setRecipientBusy(false);setPageMode("ALL_PAGES");setShowCreate(true);}}>{l("Создать проект","Створити проєкт")}</button>}{errorNotice}
    {sourceTestMode&&showCreate&&<dialog ref={createDialog} className="launch-dialog auction-create-dialog" aria-label={l("Создать проект","Створити проєкт")} onCancel={event=>{event.preventDefault();void dismissCreation();}}>
      <h2>{l("Создать проект","Створити проєкт")} · {name}</h2>
      <p>{createdProject === null ? l("Шаг 1 из 2: название и фильтры. На следующем шаге выберите получателя Telegram или продолжите без него.","Крок 1 з 2: назва та фільтри. На наступному кроці виберіть одержувача Telegram або продовжте без нього.")
        : l("Шаг 2 из 2: проект уже сохранён, поиск пока не запущен. Подключите получателя или продолжите без Telegram. Изменить выбор можно в настройках аукциона.","Крок 2 з 2: проєкт уже збережено, пошук поки не запущено. Підключіть одержувача або продовжте без Telegram. Змінити вибір можна в налаштуваннях аукціону.")}</p>
      <div className="launch-content">{createdProject === null ? <form id={`create-${draftId}`} className="auction-project-form" onSubmit={(event)=>{event.preventDefault();if(createSubmitting.current||create.isPending||creationOpening||creationClosing)return;createSubmitting.current=true;setCommandError(null);create.mutate();}}>
      <label>{l("Название проекта","Назва проєкту")}<input aria-label={`${l("Название проекта","Назва проєкту")} ${name}`} required maxLength={140} value={draftName} onChange={(event)=>setDraftName(event.target.value)} /></label>
      {write&&<SearchSelector key={`${source}:${draftId}`} source={source} draftId={draftId} locale={locale} read={read} write={write} canSearch={canSearch} disabled={create.isPending||creationClosing} help={selectionHelp} onChange={setSelection} onOpeningChange={setCreationOpening} />}
      <label>{l("Страницы","Сторінки")}<select aria-label={l("Страницы поиска","Сторінки пошуку")} value={pageMode} onChange={(event)=>setPageMode(event.target.value)}><option value="ALL_PAGES">{l("Все страницы","Усі сторінки")}</option><option value="FIRST_PAGE">{l("Первая страница","Перша сторінка")}</option></select></label>
      </form> : <TelegramProject key={`${source}:${createdProject}`} source={source} projectId={createdProject} locale={locale} read={read} write={write} initialExpanded onBusyChange={setRecipientBusy} />}
      {commandError&&<p className="warning-text" role="alert">{commandError}</p>}</div>
      <div className="launch-actions"><button type="button" className="button" disabled={create.isPending||recipientBusy||creationOpening||creationClosing} onClick={()=>{void dismissCreation();}}>{createdProject===null?l("Отмена","Скасувати"):l("Закрыть без запуска","Закрити без запуску")}</button>
        {createdProject===null ? <button form={`create-${draftId}`} className="button primary" type="submit" disabled={create.isPending||!draftUrl||creationOpening||creationClosing}>{create.isPending?l("Создание…","Створення…"):l("Создать и настроить Telegram","Створити й налаштувати Telegram")}</button>
          : <button type="button" className="button primary" disabled={recipientBusy} onClick={()=>finishCreation(true)}>{l("Готово — начать поиск","Готово — почати пошук")}</button>}</div>
    </dialog>}{tabs}<AuctionProjects key={source} source={source} locale={locale} projects={ownProjects} loading={projectsLoading} failed={projectsError}
      showProjects={collection === "active"} canRefresh={canSearch} read={read} refresh={sourceTestMode?refreshProjects:undefined} open={(id)=>navigate("project",id)}
      renderStatus={(id)=>updateStatus(id,true)} renderRefresh={sourceTestMode?(id)=>refreshButton(id,true):undefined}
      renderEdit={sourceTestMode&&write?(id)=>editButton(id,true):undefined}
      renderDelete={requestProjectDeletion ? (id)=><button type="button" className="icon-button danger auction-project-delete"
        aria-label={`${l("Удалить проект", "Видалити проєкт")}: ${ownProjects.find(project=>project.id===id)?.name || `#${id}`}`}
        title={l("Удалить проект", "Видалити проєкт")} onClick={()=>requestProjectDeletion(id)}>
        <svg aria-hidden="true" viewBox="0 0 24 24"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></svg>
      </button> : undefined}
      readState={<ReadState error={projectsError} loading={projectsLoading} retry={retryProjects} locale={locale} />} />
    {collectionNotice}{collection !== "active" && collectionQueries.map((query, index) => <section key={ownProjects[index].id}><h2>{ownProjects[index].name}</h2>{updateStatus(ownProjects[index].id)}<ReadState error={query.isError} loading={query.isPending} retry={() => { void query.refetch(); }} locale={locale} />{query.data?.source_key === source && <><p className="auction-snapshot">{query.data.local_date} · {query.data.computer_timezone}</p><div className="auction-list">{cards(query.data)}</div>{!query.isError && !query.data.lots.length && <p>{emptyCollection}</p>}</>}</section>)}
  </>;
}

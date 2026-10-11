import type { Locale } from "./translations";
import { SITE_NAMES, workspaceUrl, type SourceKey, type View } from "./source-workspace";
import { reasonText, type LaunchOptions } from "./launch-dialog";

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
  if (availability.can_search && !detailed) return null;
  const pending = availability.reason === "AVAILABILITY_PENDING";
  const failed = availability.reason === "AVAILABILITY_ERROR";
  return <section className="panel source-availability" role={failed ? "alert" : "status"} aria-label={`${SITE_NAMES[source]}: ${uk ? "доступність пошуку" : "доступность поиска"}`}>
    <div><h2>{availability.can_search ? (uk ? "Модуль пошуку підключено" : "Модуль поиска подключён")
      : availability.reason === "SOURCE_NOT_CONNECTED" ? (uk ? `Пошук ${SITE_NAMES[source]} ще не підключено` : `Поиск ${SITE_NAMES[source]} ещё не подключён`)
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

export function SourceNavigation({ source, view, locale, navigate, selectSource, options, failed }: {
  source: SourceKey; view: View; locale: Locale;
  navigate: (view: View) => void; selectSource: (source: SourceKey) => void;
  options?: LaunchOptions; failed?: boolean;
}) {
  const uk = locale === "uk";
  const sections = [
    { view: "projects" as const, label: uk ? "Проєкти" : "Проекты", path: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" },
    { view: "scans" as const, label: uk ? "Запуски" : "Запуски", path: "m9 5 10 7-10 7V5Z" },
    { view: "source-settings" as const, label: uk ? "Налаштування" : "Настройки", path: "M4 7h16M4 17h16M8 4v6M16 14v6" },
  ];
  return <nav className="source-nav" aria-label={uk ? "Робочі області сайтів" : "Рабочие области сайтов"}>
    {([
      { label: uk ? "Майданчики" : "Площадки", sites: ["encar"] },
      { label: "Аукционы", sites: ["copart", "iaa"] },
    ] as { label: string; sites: SourceKey[] }[]).map((group) => <div className="source-nav-group" key={group.sites[0]}>
      <p className="source-group-label">{uk && group.sites[0] === "copart" ? "Аукціони" : group.label}</p>
      {group.sites.map((key) => { const availability = sourceAvailability(options, key, failed);
        const label = availability.can_search ? (uk ? "Модуль пошуку підключено" : "Модуль поиска подключён") : reasonText(availability.reason, locale);
        return <div className="source-nav-site" key={key}>
        <button type="button" className={`source-button ${key === source ? "selected" : ""}`} onClick={() => selectSource(key)}
          aria-label={`${SITE_NAMES[key]}: ${label}`} aria-expanded={key === source} aria-controls={key === source ? `source-${key}-sections` : undefined}>
          <span className="source-monogram" aria-hidden="true">{key === "iaa" ? "I" : SITE_NAMES[key][0]}</span>
          <span className="nav-label">{SITE_NAMES[key]}</span>
          <span className={`source-pending ${availability.can_search ? "source-ready" : ""}`} role="img" aria-label={`${SITE_NAMES[key]}: ${label}`} title={label}>{availability.can_search ? "●" : "○"}</span>
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

export function SourceUnavailable({ source, view, locale, availability, retry, settings }: {
  source: SourceKey; view: View; locale: Locale; availability: SearchAvailability; retry: () => void; settings: () => void;
}) {
  const uk = locale === "uk";
  return <>
    <div className="title-row source-title"><div><h1>{view === "scans" ? (uk ? "Історія запусків" : "История запусков") : view === "source-settings" ? (uk ? "Налаштування сайту" : "Настройки сайта") : (uk ? "Проєкти" : "Проекты")} · {SITE_NAMES[source]}</h1>
      <p>{uk ? "Аукціони · окрема робоча область" : "Аукционы · отдельная рабочая область"}</p></div></div>
    <SourceAvailabilityNotice source={source} availability={availability} locale={locale} retry={retry} settings={settings} detailed />
    <section className="panel source-unavailable">
      <h2>{uk ? "Інтерфейс аукціонів у розробці" : "Интерфейс аукционов в разработке"}</h2>
      <p>{uk ? "Оболонка готова. Збір лотів, картки аукціонів і публікації в Telegram підключимо наступними етапами. Тут немає демонстраційних результатів або активних пошуків." : "Оболочка готова. Сбор лотов, карточки аукционов и публикации в Telegram подключим следующими этапами. Здесь нет демонстрационных результатов или активных поисков."}</p>
      <button type="button" className="button" disabled>{uk ? "Оновити дані" : "Обновить данные"}</button>
      <p className="muted">{uk ? "Стан одного сайту не змінює дані інших. Перехід між сайтами не зупиняє фоновий пошук." : "Состояние одного сайта не изменяет данные других. Переход между сайтами не останавливает фоновый поиск."}</p>
    </section>
  </>;
}

export function SourceContextMismatch({ source, projectId, carId, locale }: { source: SourceKey; projectId: number | null; carId?: number; locale: Locale }) {
  const uk = locale === "uk";
  return <section className="panel source-unavailable"><h2>{uk ? "Інший сайт" : "Другой сайт"}</h2>
    <p>{uk ? `Ці дані належать ${SITE_NAMES[source]}. Відкрийте відповідну робочу область.` : `Эти данные относятся к ${SITE_NAMES[source]}. Откройте соответствующую рабочую область.`}</p>
    <a className="button" href={workspaceUrl("", { source, view: carId ? "car" : "project", projectId, carId: carId || null })}>{uk ? "Перейти до" : "Перейти к"} {SITE_NAMES[source]}</a>
  </section>;
}

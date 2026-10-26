import { useState, type MouseEvent } from "react";
import { TelegramLogo } from "./telegram-logo";
import { TelegramLinkActions } from "./telegram-link-actions";
import type { Locale } from "./translations";

const SHARED_BOT_URL = "https://t.me/search_car_app_bot";

export function TelegramSharedSetup({ locale, open }: {
  locale: Locale; open?: (url: string) => Promise<unknown>;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const [opening, setOpening] = useState(false), [openFailed, setOpenFailed] = useState(false);
  async function openBot(event: MouseEvent<HTMLAnchorElement>) {
    if (!open) return;
    event.preventDefault(); if (opening) return;
    setOpening(true); setOpenFailed(false);
    try { await open(SHARED_BOT_URL); } catch { setOpenFailed(true); } finally { setOpening(false); }
  }
  // Pairing is project-scoped; the public bot link alone cannot authorize it.
  return <section className="panel settings telegram-connection" aria-labelledby="shared-telegram-title">
    <h3 id="shared-telegram-title"><TelegramLogo /> {l("Общий бот SearchCar", "Спільний бот SearchCar")}</h3>
    <p>{l("Наш бот — @search_car_app_bot. Создавать своего бота не нужно.",
      "Наш бот — @search_car_app_bot. Створювати власного бота не потрібно.")}</p>
    <p className="telegram-shared-notice">{l("Общий бот используется всеми пользователями. При высокой нагрузке отправка может задерживаться.",
      "Спільного бота використовують усі користувачі. За високого навантаження надсилання може затримуватися.")}</p>
    <TelegramLinkActions url={SHARED_BOT_URL} locale={locale} pending={opening} onOpen={openBot}
      openLabel={`${l("Открыть", "Відкрити")} @search_car_app_bot`} />
    {openFailed && <p role="alert">{l("Не удалось открыть Telegram. Повторите или откройте вручную: ",
      "Не вдалося відкрити Telegram. Повторіть або відкрийте вручну: ")}{SHARED_BOT_URL}</p>}
    <p>{l("Это обычная ссылка на бота. Для подключения получателя создайте ссылку в выбранном проекте ниже.",
      "Це звичайне посилання на бота. Для підключення одержувача створіть посилання у вибраному проєкті нижче.")}</p>
    <p>{l("Выберите проект ниже. Получатель сохраняется за каждым проектом отдельно.",
      "Виберіть проєкт нижче. Одержувач зберігається за кожним проєктом окремо.")}</p>
    <h4>{l("Личные сообщения", "Особисті повідомлення")}</h4>
    <p>{l("Откройте ссылку подключения из проекта или перешлите её нужному человеку. Получатель нажимает Start («Запустить»). Вернитесь сюда и дождитесь «Подключено» — чат сохранится автоматически.",
      "Відкрийте посилання підключення з проєкту або перешліть його потрібній людині. Одержувач натискає Start («Розпочати»). Поверніться сюди й дочекайтеся «Підключено» — чат збережеться автоматично.")}</p>
    <h4>{l("Группы и каналы", "Групи та канали")}</h4>
    <p>{l("Выберите тип получателя в проекте. По ссылке подключения администратор выбирает чат в Telegram и разрешает доступ боту. Если тип недоступен, подключение групп и каналов ещё не включено.",
      "Виберіть тип одержувача в проєкті. За посиланням підключення адміністратор вибирає чат у Telegram і дозволяє доступ боту. Якщо тип недоступний, підключення груп і каналів ще не ввімкнено.")}</p>
    <p className="auction-snapshot">{l("Сообщения отправляются только по нажатию кнопки Telegram, не после поиска автоматически.",
      "Повідомлення надсилаються лише після натискання кнопки Telegram, не після пошуку автоматично.")}</p>
  </section>;
}

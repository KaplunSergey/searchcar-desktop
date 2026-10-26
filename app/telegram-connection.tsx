import { useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { TelegramLogo } from "./telegram-logo";
import type { Locale } from "./translations";

export type TelegramConnectionStatus = {
  connected: boolean; bot: { id: number; username: string } | null;
  channels: { chat_id: number; title: string; checked_at: string }[];
  delivery_enabled: false;
};
export const TELEGRAM_CONNECTION_QUERY = ["telegram-connection"] as const;

export function telegramConnectionError(error: unknown, locale: Locale) {
  const code = error && typeof error === "object" && "code" in error ? String(error.code) : "";
  const messages: Record<string, [string, string]> = {
    telegram_token_invalid: ["Проверьте формат токена от @BotFather.", "Перевірте формат токена від @BotFather."],
    telegram_token_rejected: ["Telegram отклонил токен. Получите актуальный токен у @BotFather.", "Telegram відхилив токен. Отримайте актуальний токен у @BotFather."],
    telegram_secure_storage_unavailable: ["Защищённое хранилище недоступно. Токен не будет записан в обычный файл.", "Захищене сховище недоступне. Токен не буде записано у звичайний файл."],
    telegram_channel_invalid: ["Введите @имя, https://t.me/имя или числовой ID канала. Пригласительная ссылка не подходит.", "Введіть @ім’я, https://t.me/ім’я або числовий ID каналу. Запрошувальне посилання не підходить."],
    telegram_channel_required: ["Нужен канал, не группа или личный чат.", "Потрібен канал, не група чи особистий чат."],
    telegram_post_permission_required: ["Добавьте бота администратором канала с правом публикации сообщений.", "Додайте бота адміністратором каналу з правом публікації повідомлень."],
    telegram_channel_access_denied: ["Нет доступа к каналу. Проверьте членство и права бота.", "Немає доступу до каналу. Перевірте членство та права бота."],
    telegram_channel_unavailable: ["Канал не найден или недоступен боту. Проверьте имя/ID и права.", "Канал не знайдено або недоступний боту. Перевірте ім’я/ID та права."],
    telegram_bot_required: ["Сначала подключите бота.", "Спочатку підключіть бота."],
    telegram_rate_limited: ["Telegram ограничил частоту проверок. Повторите позже.", "Telegram обмежив частоту перевірок. Повторіть пізніше."],
  };
  return (messages[code] || ["Подключение не подтверждено. Проверьте состояние и повторите позже.",
    "Підключення не підтверджено. Перевірте стан і повторіть пізніше."])[locale === "uk" ? 1 : 0];
}

export function TelegramConnection({ locale, request }: {
  locale: Locale; request: <T>(path: string, options?: RequestInit) => Promise<T>;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const client = useQueryClient();
  const query = useQuery<TelegramConnectionStatus>({ queryKey: TELEGRAM_CONNECTION_QUERY,
    queryFn: () => request("/desktop/telegram"), retry: false, refetchOnWindowFocus: false });
  const [pending, setPending] = useState(false), [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null), [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const tokenInput = useRef<HTMLInputElement>(null);
  const status = query.data;
  async function change(path: string, options: RequestInit, success: string) {
    if (pending) return;
    setPending(true); setError(null); setNotice(null);
    try {
      const value = await request<TelegramConnectionStatus>(path, options);
      client.setQueryData(TELEGRAM_CONNECTION_QUERY, value);
      setNotice(success); setConfirmDisconnect(false);
    } catch (failure) { setError(telegramConnectionError(failure, locale)); }
    finally { setPending(false); }
  }
  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = tokenInput.current?.value || "";
    if (tokenInput.current) tokenInput.current.value = "";
    // No secret in React state, query/mutation caches or browser storage.
    void change("/desktop/telegram/connect", { method: "POST", body: JSON.stringify({ token }) },
      l("Бот проверен. Токен сохранён в защищённом хранилище.", "Бота перевірено. Токен збережено в захищеному сховищі."));
  }
  function verify(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const channel = String(new FormData(event.currentTarget).get("channel") || "");
    void change("/desktop/telegram/channels/verify", { method: "POST", body: JSON.stringify({ channel }) },
      l("Право публикации проверено. Сообщения не отправлялись.", "Право публікації перевірено. Повідомлення не надсилалися."));
  }
  return <section className="panel settings telegram-connection" aria-labelledby="telegram-connection-title" aria-busy={pending}>
    <h3 id="telegram-connection-title"><TelegramLogo /> Telegram</h3>
    <p>{l("Подключите собственного бота от @BotFather. Проверки ничего не публикуют; автоматическая отправка пока выключена.", "Підключіть власного бота від @BotFather. Перевірки нічого не публікують; автоматичне надсилання поки вимкнено.")}</p>
    {query.isPending && <p role="status">{l("Читаем состояние подключения…", "Читаємо стан підключення…")}</p>}
    {query.isError && <div role="alert"><p>{telegramConnectionError(query.error, locale)}</p><button className="button" type="button" disabled={query.isFetching || pending} onClick={() => void query.refetch()}>{l("Проверить снова", "Перевірити знову")}</button></div>}
    {status && <>
      <p>{status.connected ? l("Подключён бот", "Підключено бота") + ` @${status.bot?.username}` : l("Бот не подключён", "Бота не підключено")}</p>
      <form onSubmit={connect} autoComplete="off">
        <label htmlFor="telegram-token">{l("Токен бота", "Токен бота")}</label>
        <input id="telegram-token" name="telegram-bot-token" type="password" ref={tokenInput} required maxLength={160} autoComplete="new-password" spellCheck={false} autoCapitalize="none" disabled={pending} aria-describedby="telegram-secret-help" />
        <p id="telegram-secret-help">{l("Токен остаётся на этом компьютере: Keychain на macOS, DPAPI на Windows. Не входит в резервную копию. На другом компьютере подключите бота заново.", "Токен залишається на цьому комп’ютері: Keychain на macOS, DPAPI на Windows. Не входить до резервної копії. На іншому комп’ютері підключіть бота знову.")}</p>
        {status.connected && <p>{l("Замена бота сбросит сохранённые проверки каналов.", "Заміна бота скине збережені перевірки каналів.")}</p>}
        <button className="button primary" type="submit" disabled={pending}>{pending ? l("Проверяем…", "Перевіряємо…") : status.connected ? l("Заменить бота", "Замінити бота") : l("Подключить бота", "Підключити бота")}</button>
      </form>
      {status.connected && <>
        <form onSubmit={verify}>
          <label htmlFor="telegram-channel">{l("Канал Telegram", "Канал Telegram")}</label>
          <input id="telegram-channel" name="channel" type="text" required maxLength={160} placeholder="@channel / https://t.me/channel / -100…" disabled={pending} aria-describedby="telegram-channel-help" />
          <p id="telegram-channel-help">{l("Добавьте бота администратором с правом «Публикация сообщений». Для приватного канала нужен числовой ID: пригласительная ссылка не подходит.", "Додайте бота адміністратором із правом «Публікація повідомлень». Для приватного каналу потрібен числовий ID: запрошувальне посилання не підходить.")}</p>
          <button className="button" type="submit" disabled={pending}>{l("Проверить канал", "Перевірити канал")}</button>
        </form>
        {status.channels.length > 0 && <div><h4>{l("Сохранённые проверки каналов", "Збережені перевірки каналів")}</h4><p>{l("Это результат последней проверки, не гарантия текущих прав и не включение рассылки.", "Це результат останньої перевірки, не гарантія поточних прав і не ввімкнення розсилки.")}</p>
          <ul>{status.channels.map(channel => <li key={channel.chat_id}><strong>{channel.title}</strong> · ID: {channel.chat_id}<br /><time dateTime={channel.checked_at}>{new Date(channel.checked_at).toLocaleString(locale === "uk" ? "uk-UA" : "ru-RU")}</time></li>)}</ul></div>}
        {confirmDisconnect ? <div className="telegram-disconnect" role="group" aria-label={l("Подтвердите отключение", "Підтвердьте відключення")}>
          <p>{l("Удалить токен и проверки каналов с этого компьютера? Сам бот и сообщения в Telegram останутся.", "Видалити токен і перевірки каналів із цього комп’ютера? Сам бот і повідомлення в Telegram залишаться.")}</p>
          <button className="button danger" disabled={pending} type="button" onClick={() => void change("/desktop/telegram", { method: "DELETE" }, l("Подключение удалено.", "Підключення видалено."))}>{l("Отключить", "Відключити")}</button>
          <button className="button" type="button" disabled={pending} onClick={() => setConfirmDisconnect(false)}>{l("Отмена", "Скасувати")}</button>
        </div> : <button className="button" type="button" disabled={pending} onClick={() => setConfirmDisconnect(true)}>{l("Отключить бота", "Відключити бота")}</button>}
      </>}
    </>}
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
  </section>;
}

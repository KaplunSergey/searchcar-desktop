import { useEffect, useRef, useState, type MouseEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { TelegramRecipientChoice } from "./telegram-recipient-choice";
import { TelegramLogo } from "./telegram-logo";
import { TelegramLinkActions } from "./telegram-link-actions";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import { watchAuctionCountdown } from "./auction-time-client";

type RecipientKind = "private" | "group" | "channel";
type Pairing = {
  pairing_id: string; project_id: number; status: "WAITING_START" | "AWAITING_CONFIRMATION" | "CONNECTED" | "EXPIRED" | "REVOKED";
  expires_at_ms: number; start_url?: string; target_kind?: RecipientKind;
  recipient: { title: string; chat_id: number; chat_type: "private" | "group" | "supergroup" | "channel" } | null;
};
type Status = {
  available: boolean; issue: string | null; delivery_enabled: boolean; chat_pairing_enabled?: boolean; session: Pairing | null;
  recipient: { project_id: number; title: string; chat_id: number; chat_type: string; binding_id: string } | null;
};

export function telegramPairingRefreshInterval(status: Status | undefined, projectId: number, now: number, failed = false): number | false {
  const session = status?.session;
  return !failed && status?.available && session?.project_id === projectId
    && (!status.recipient || status.recipient.project_id === projectId)
    && ["WAITING_START", "AWAITING_CONFIRMATION"].includes(session.status) && session.expires_at_ms > now ? 5000 : false;
}

export async function refreshTelegramPairing({ current, projectId, path, write, signal, now = Date.now }: {
  current: Status; projectId: number; path: string; write: NonNullable<SourceWorkspaceProps["write"]>;
  signal: AbortSignal; now?: () => number;
}): Promise<Status> {
  signal.throwIfAborted();
  const previous = current.session;
  if (!previous || !telegramPairingRefreshInterval(current, projectId, now())) return current;
  const changed = () => Object.assign(new Error("Pairing changed"), { code: "telegram_pairing_changed" });
  const sameScope = (value: Status) => value.session?.project_id === projectId && value.session.pairing_id === previous.pairing_id
    && (value.session.target_kind || "private") === (previous.target_kind || "private")
    && (!value.recipient || value.recipient.project_id === projectId);
  const checked = await write<Status>(path, { action: "CHECK", pairing_id: previous.pairing_id });
  signal.throwIfAborted();
  if (!sameScope(checked)) throw changed();
  const selected = checked.session!;
  if (!checked.available || selected.status !== "AWAITING_CONFIRMATION" || selected.expires_at_ms <= now() || !selected.recipient) return checked;
  const connected = await write<Status>(path, { action: "CONFIRM", pairing_id: selected.pairing_id, chat_id: selected.recipient.chat_id });
  signal.throwIfAborted();
  if (!sameScope(connected) || connected.session?.status !== "CONNECTED" || connected.recipient?.chat_id !== selected.recipient.chat_id) throw changed();
  return connected;
}

export function TelegramProject({ source, projectId, locale, read, write, nowMs, initialExpanded = false, onBusyChange }: {
  source: string; projectId: number; locale: Locale; nowMs?: number; read: SourceWorkspaceProps["read"]; write?: SourceWorkspaceProps["write"];
  initialExpanded?: boolean;
  onBusyChange?: (busy: boolean) => void;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const [clock, setClock] = useState(Date.now);
  useEffect(() => { if (nowMs === undefined) return watchAuctionCountdown(setClock); }, [nowMs]);
  const [expanded, setExpanded] = useState(initialExpanded);
  const [saved, setSaved] = useState("");
  const [kind, setKind] = useState<RecipientKind | null>(null);
  const client = useQueryClient();
  const [pending, setPending] = useState(false), [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null), [disconnect, setDisconnect] = useState(false);
  const running = useRef(false);
  const query = useQuery<Status>({ queryKey: ["project-telegram", source, projectId],
    queryFn: ({ signal }) => {
      const current = client.getQueryData<Status>(["project-telegram", source, projectId]);
      const path = `/projects/${projectId}/telegram?source_key=${encodeURIComponent(source)}`;
      return write && !running.current && current && telegramPairingRefreshInterval(current, projectId, nowMs ?? Date.now())
        ? refreshTelegramPairing({ current, projectId, path, write, signal, now: () => nowMs ?? Date.now() }) : read<Status>(path);
    },
    enabled: expanded, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    refetchInterval: state => expanded && !!write && !running.current
      ? telegramPairingRefreshInterval(state.state.data, projectId, nowMs ?? Date.now(), state.state.status === "error") : false,
    refetchIntervalInBackground: false });
  useEffect(() => { onBusyChange?.(pending || query.isFetching); }, [pending, query.isFetching, onBusyChange]);
  const status = query.data;
  const session = status?.session;
  const scoped = (!session || session.project_id === projectId) && (!status?.recipient || status.recipient.project_id === projectId);
  const expired = !!session && session.status !== "CONNECTED" && session.expires_at_ms <= (nowMs ?? clock);
  const candidate = session?.status === "AWAITING_CONFIRMATION" ? session.recipient : null;
  const liveLink = !!session && !expired && ["WAITING_START", "AWAITING_CONFIRMATION"].includes(session.status);
  const selectedKind: RecipientKind = liveLink ? session.target_kind || "private" : kind || session?.target_kind
    || (status?.recipient?.chat_type === "channel" ? "channel" : ["group", "supergroup"].includes(status?.recipient?.chat_type || "") ? "group" : "private");
  const kindLabel = (type: string) => type === "channel" ? l("Канал", "Канал") : type === "private" ? l("Личные сообщения", "Особисті повідомлення") : l("Группа", "Група");
  const actionable = scoped && !!status?.available && !!write && !pending && !query.isFetching;
  const automaticallyChecking = expanded && !!write && !!telegramPairingRefreshInterval(status, projectId, nowMs ?? clock, query.isError);
  if (!liveLink && link) setLink(null);
  useEffect(() => {
    if (session?.status === "CONNECTED") void client.invalidateQueries({ queryKey: ["telegram-recipients"] });
  }, [client, session?.pairing_id, session?.status]);
  useEffect(() => {
    if (!expanded) { void client.cancelQueries({ queryKey: ["project-telegram", source, projectId], exact: true }); return; }
    if (!write) return;
    const resume = () => {
      if (document.visibilityState === "hidden") {
        void client.cancelQueries({ queryKey: ["project-telegram", source, projectId], exact: true }); return;
      }
      const latest = client.getQueryData<Status>(["project-telegram", source, projectId]);
      if (!running.current && !query.isError
          && telegramPairingRefreshInterval(latest, projectId, nowMs ?? Date.now())) void query.refetch();
    };
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => { window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume); };
  }, [client, expanded, write, source, projectId, nowMs, query.isError, query.refetch]);
  function failure(code: string | null) {
    if (code === "telegram_pairing_unavailable") return l("Нет связи с сервисом Telegram. Проверьте интернет и повторите проверку.", "Немає зв’язку із сервісом Telegram. Перевірте інтернет і повторіть перевірку.");
    if (code === "telegram_chat_pairing_disabled") return l("Подключение групп и каналов ещё не включено. Личные сообщения доступны.", "Підключення груп і каналів ще не ввімкнено. Особисті повідомлення доступні.");
    if (code === "telegram_chat_permission_required") return l("Вы и бот должны быть администраторами выбранного чата. В канале разрешите боту публикацию сообщений, затем проверьте снова.", "Ви й бот мають бути адміністраторами вибраного чату. У каналі дозвольте боту публікацію повідомлень, потім перевірте знову.");
    if (code === "telegram_chat_verification_unavailable") return l("Не удалось проверить права в Telegram. Повторите проверку позже.", "Не вдалося перевірити права в Telegram. Повторіть перевірку пізніше.");
    if (code === "telegram_pairing_changed") return l("Выбор изменился или срок ссылки истёк. Проверьте состояние; при необходимости создайте новую ссылку.", "Вибір змінився або строк посилання минув. Перевірте стан; за потреби створіть нове посилання.");
    if (code === "telegram_device_binding_required" || code === "telegram_device_not_authorized") return l("Сначала проверьте активацию приложения и доступ устройства по лицензии.", "Спочатку перевірте активацію застосунку та доступ пристрою за ліцензією.");
    if (code === "telegram_pairing_rate_limited") return l("Слишком много проверок. Подождите минуту и повторите.", "Забагато перевірок. Зачекайте хвилину та повторіть.");
    if (code === "telegram_recipient_busy") return l("Дождитесь завершения текущей отправки перед сменой получателя.", "Дочекайтеся завершення поточного надсилання перед зміною одержувача.");
    return l("Действие не подтверждено. Проверьте состояние перед повтором. Ранее сохранённый получатель не считается заново проверенным.", "Дію не підтверджено. Перевірте стан перед повтором. Раніше збережений одержувач не вважається перевіреним заново.");
  }
  async function change(action: string, extra: Record<string, unknown> = {}) {
    if (!write || !actionable || running.current) return;
    running.current = true; setPending(true); setError(null);
    try {
      const next = await write<Status>(`/projects/${projectId}/telegram?source_key=${encodeURIComponent(source)}`, { action, ...extra });
      const target = next.session?.start_url;
      // Deep links remain only in this component's transient state, not query caches/storage.
      if (target && /^https:\/\/t\.me\/search_car_app_bot\?start=p_[A-Za-z0-9_-]{32}$/.test(target)) setLink(target);
      else if (["CANCEL", "DISCONNECT"].includes(action) || ["CONNECTED", "EXPIRED", "REVOKED"].includes(next.session?.status || "")) setLink(null);
      const safe = { ...next, session: next.session ? { ...next.session, start_url: undefined } : null };
      client.setQueryData(["project-telegram", source, projectId], safe);
      void client.invalidateQueries({ queryKey: ["telegram-recipients"] });
      setSaved("");
      setDisconnect(false);
    } catch (cause) {
      const code = cause && typeof cause === "object" && "code" in cause ? String(cause.code) : null;
      setError(failure(code));
    } finally { running.current = false; setPending(false); }
  }
  async function open(event: MouseEvent<HTMLAnchorElement>) {
    if (!write || !link) return;
    event.preventDefault(); if (running.current) return;
    running.current = true; setPending(true); setError(null);
    try { await write("/desktop/open-external", { url: link }); }
    catch { setError(l("Не удалось открыть Telegram. Повторите открытие или скопируйте адрес ссылки и откройте вручную.", "Не вдалося відкрити Telegram. Повторіть відкриття або скопіюйте адресу посилання та відкрийте вручну.")); }
    finally { running.current = false; setPending(false); }
  }
  return <details className="panel telegram-project" open={expanded} onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary><svg className="telegram-section-chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" /></svg><TelegramLogo />{l("Telegram · получатель проекта", "Telegram · одержувач проєкту")}</summary>
    {session?.status !== "CONNECTED" && (!saved || saved === "new") && <ol className="telegram-pairing-steps">
      <li>{l("Создайте ссылку. Откройте её сами или скопируйте и отправьте нужному человеку.", "Створіть посилання. Відкрийте його самі або скопіюйте й надішліть потрібній людині.")}</li>
      <li>{selectedKind === "private" ? l("Получатель нажимает Start («Запустить») в Telegram.", "Одержувач натискає Start («Розпочати») у Telegram.")
        : l("Администратор нажимает Start, затем выбирает нужный чат кнопкой бота и разрешает доступ.", "Адміністратор натискає Start, потім вибирає потрібний чат кнопкою бота й дозволяє доступ.")}</li>
      <li>{l("Вернитесь в SearchCar: выбранный чат проверится и сохранится автоматически. Дождитесь «Подключено».", "Поверніться до SearchCar: вибраний чат перевіриться і збережеться автоматично. Дочекайтеся «Підключено».")}</li>
    </ol>}
    <p className="auction-snapshot">{status?.chat_pairing_enabled ? l("Группы и каналы: нужны права администратора у вас и у бота. Для канала — право публикации сообщений.", "Групи та канали: потрібні права адміністратора у вас і в бота. Для каналу — право публікації повідомлень.")
      : l("Сейчас доступны личные сообщения. Группы и каналы ещё не включены.", "Зараз доступні особисті повідомлення. Групи та канали ще не ввімкнено.")}</p>
    <p className="auction-snapshot">{status?.delivery_enabled ? l("Telegram у автомобиля или выбранной группы отправляет сообщения вручную. Автоотправка «Сегодня» включается отдельно в настройках проекта; подключение получателя само её не включает.", "Telegram біля автомобіля або вибраної групи надсилає повідомлення вручну. Автонадсилання «Сьогодні» вмикається окремо в налаштуваннях проєкту; підключення одержувача саме його не вмикає.")
      : l("Отправка сообщений ещё не подключена. Привязка не запускает поиск или рассылку.", "Надсилання повідомлень ще не підключено. Прив’язка не запускає пошук або розсилку.")}</p>
    {query.isPending && !status && <p role="status">{l("Чтение настроек…", "Читання налаштувань…")}</p>}
    {query.isError && !status && <p role="alert">{l("Не удалось прочитать настройки.", "Не вдалося прочитати налаштування.")} <button type="button" className="button" onClick={() => { void query.refetch(); }}>{l("Повторить", "Повторити")}</button></p>}
    {!scoped && <p role="alert">{l("Ответ относится к другому проекту. Закройте и откройте проект заново.", "Відповідь стосується іншого проєкту. Закрийте й відкрийте проєкт знову.")}</p>}
    {status && scoped && <>
      {!status.available && <p role="status">{status.issue === "telegram_device_binding_required" ? failure(status.issue) : l("Сервис общего бота ещё не настроен. Подключение пока недоступно.", "Сервіс спільного бота ще не налаштовано. Підключення поки недоступне.")}</p>}
      {status.recipient && <p className="telegram-recipient"><b>{l("Сохранённый получатель", "Збережений одержувач")}: {status.recipient.title}</b><br />{kindLabel(status.recipient.chat_type)} · ID: {status.recipient.chat_id}<br />{l("Перед отправкой SearchCar автоматически проверит доступ.", "Перед надсиланням SearchCar автоматично перевірить доступ.")}</p>}
      <TelegramRecipientChoice locale={locale} read={read} value={saved} onChange={value => {
        setSaved(value); setError(null);
        if (value && value !== "new") void change("USE_SAVED", { recipient_id: value });
      }} enabled={expanded} disabled={!actionable}
        emptyLabel={l("Выберите для подключения или замены…", "Виберіть для підключення або заміни…")} />
      {saved && saved !== "new" && <>{pending && <p role="status">{l("Проверяем и подключаем выбранный чат…", "Перевіряємо й підключаємо вибраний чат…")}</p>}
        {error && <button type="button" className="button" disabled={!actionable} onClick={() => void change("USE_SAVED", { recipient_id: saved })}>{l("Повторить подключение", "Повторити підключення")}</button>}</>}
      <label className="telegram-kind-choice">{l("Тип получателя", "Тип одержувача")}
        <select aria-label={l("Тип получателя", "Тип одержувача")} value={selectedKind} disabled={!actionable || liveLink} onChange={event => setKind(event.target.value as RecipientKind)}>
          <option value="private">{kindLabel("private")}</option>
          <option value="group" disabled={!status.chat_pairing_enabled}>{kindLabel("group")}</option>
          <option value="channel" disabled={!status.chat_pairing_enabled}>{kindLabel("channel")}</option>
        </select>
      </label>
      <div className="telegram-project-actions"><button type="button" className="button" disabled={!actionable}
        onClick={() => void change("START", selectedKind === "private" ? {} : { target_kind: selectedKind })}>{status.recipient ? l("Изменить получателя", "Змінити одержувача") : l("Создать ссылку подключения", "Створити посилання підключення")}</button>
        {(session || status.recipient) && (!liveLink || query.isError || error) && <button type="button" className="button" disabled={!actionable} onClick={() => void change("CHECK", session ? { pairing_id: session.pairing_id } : {})}>{session?.status === "CONNECTED" ? l("Проверить доступ", "Перевірити доступ") : l("Проверить состояние", "Перевірити стан")}</button>}</div>
      {session?.status === "WAITING_START" && !expired && <p role="status">{selectedKind === "private"
        ? l("Ожидаем Start в Telegram. После него подключение завершится автоматически.", "Очікуємо Start у Telegram. Після нього підключення завершиться автоматично.")
        : l("Ожидаем выбор чата в Telegram. Нажмите Start, выберите чат кнопкой бота и подтвердите права. Остальное SearchCar сделает автоматически.", "Очікуємо вибір чату в Telegram. Натисніть Start, оберіть чат кнопкою бота й підтвердьте права. Решту SearchCar зробить автоматично.")}</p>}
      {link && session && liveLink && <TelegramLinkActions key={link} url={link} locale={locale} pending={pending} onOpen={open}
        openLabel={l("Открыть бота для привязки", "Відкрити бота для прив’язки")}>
        {["WAITING_START", "AWAITING_CONFIRMATION"].includes(session.status) && <button type="button" className="button" disabled={!actionable}
          onClick={() => { setLink(null); void change("CANCEL", { pairing_id: session.pairing_id }); }}>{l("Отменить новый выбор", "Скасувати новий вибір")}</button>}
      </TelegramLinkActions>}
      {session && ["WAITING_START", "AWAITING_CONFIRMATION"].includes(session.status) && !link && !expired && <p>{l("Одноразовая ссылка не сохраняется после закрытия страницы. Если Start ещё не нажат, создайте новую ссылку.", "Одноразове посилання не зберігається після закриття сторінки. Якщо Start ще не натиснуто, створіть нове посилання.")}</p>}
      {candidate && !expired && <section className="telegram-candidate" aria-label={l("Выбранный получатель", "Вибраний одержувач")}><b>{candidate.title}</b><p>{kindLabel(candidate.chat_type)} · ID: {candidate.chat_id}</p>
        <p>{l("Чат выбран. Проверяем доступ и сохраняем подключение…", "Чат вибрано. Перевіряємо доступ і зберігаємо підключення…")}</p></section>}
      {session?.status === "CONNECTED" && <p role="status"><b>{l("Подключено", "Підключено")}: {status.recipient?.title}</b><br />{status.delivery_enabled
        ? l("Получатель сохранён. Ручная отправка доступна в проекте; каждая попытка заново проверяет доступ на сервере.", "Одержувача збережено. Ручне надсилання доступне в проєкті; кожна спроба заново перевіряє доступ на сервері.")
        : l("Чат сохранён за проектом. Сейчас отправка сообщений в тестовой версии выключена.", "Чат збережено за проєктом. Зараз надсилання повідомлень у тестовій версії вимкнено.")}</p>}
      {(expired || session?.status === "EXPIRED") && <p role="status">{l("Срок ссылки истёк. Создайте новую или проверьте состояние ранее выполненного подтверждения.", "Строк посилання минув. Створіть нове або перевірте стан раніше виконаного підтвердження.")}</p>}
      {session?.status === "REVOKED" && <p role="status">{l("Эта привязка отменена. Для подключения нужна новая ссылка.", "Цю прив’язку скасовано. Для підключення потрібне нове посилання.")}</p>}
      {session && (!link || expired) && ["WAITING_START", "AWAITING_CONFIRMATION", "EXPIRED"].includes(session.status) && <button type="button" className="button" disabled={!actionable} onClick={() => { setLink(null); void change("CANCEL", { pairing_id: session.pairing_id }); }}>{l("Отменить новый выбор", "Скасувати новий вибір")}</button>}
      {status.recipient && <>{disconnect ? <div role="group" aria-label={l("Подтвердите отключение", "Підтвердьте відключення")}><p>{l("Отключить получателя только у этого проекта?", "Відключити одержувача лише в цього проєкту?")}</p><div className="telegram-project-actions">
        <button type="button" className="button danger" disabled={!actionable} onClick={() => void change("DISCONNECT", { binding_id: status.recipient!.binding_id })}>{l("Да, отключить", "Так, відключити")}</button>
        <button type="button" className="button" disabled={pending} onClick={() => setDisconnect(false)}>{l("Отмена", "Скасувати")}</button></div></div>
        : <button type="button" className="button" disabled={!actionable} onClick={() => setDisconnect(true)}>{l("Отключить получателя", "Відключити одержувача")}</button>}</>}
    </>}
    {automaticallyChecking && <p className="auction-snapshot" role="status">{query.isFetching
      ? l("Проверяем и сохраняем подключение…", "Перевіряємо й зберігаємо підключення…")
      : l("Проверяем автоматически каждые 5 секунд, пока этот экран открыт.", "Перевіряємо автоматично кожні 5 секунд, поки цей екран відкритий.")}</p>}
    {query.isError && status && <p role="alert">{l("Автоматическое подключение приостановлено: ", "Автоматичне підключення призупинено: ")}{failure(query.error && typeof query.error === "object" && "code" in query.error ? String(query.error.code) : null)} {l("Нажмите «Проверить состояние», чтобы повторить.", "Натисніть «Перевірити стан», щоб повторити.")}</p>}
    {pending && <p role="status">{l("Выполняется…", "Виконується…")}</p>}{error && <p role="alert">{error}</p>}
  </details>;
}

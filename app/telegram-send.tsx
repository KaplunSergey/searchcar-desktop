import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import { TelegramLogo } from "./telegram-logo";
import type { AuctionCollection } from "./auction-time-client";
import { useTelegramDelivery, useTelegramHistory, deliveryActive, type TelegramJob } from "./telegram-delivery";

export function TelegramDeliveryProgress({ state, locale }: { state: ReturnType<typeof useTelegramDelivery>; locale: Locale }) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const jobs = state.batch?.jobs || [], count = (status: TelegramJob["state"]) => jobs.filter(job => job.state === status).length;
  return <div className="telegram-delivery-progress">
    {state.submitting && <p role="status">{l("Проверяем отправку…", "Перевіряємо надсилання…")}</p>}
    {state.batch && !state.submitting && <><p>{l("Получатель", "Одержувач")}: {state.batch.recipient.title}</p>
      <p role="status">{l("Отправлено", "Надіслано")}: {count("SENT")} / {jobs.length} · {l("В очереди", "У черзі")}: {count("QUEUED")} · {l("Отправляется", "Надсилається")}: {count("IN_FLIGHT")}</p>
      {count("FAILED") > 0 && <p role="alert">{l("Не отправлено", "Не надіслано")}: {count("FAILED")}. {l("Можно отправить повторно отдельным нажатием.", "Можна надіслати повторно окремим натисканням.")}</p>}
      {count("CANCELLED") > 0 && <p>{l("Отменено или условия изменились", "Скасовано або умови змінилися")}: {count("CANCELLED")}</p>}
      {deliveryActive(state.batch) && <><p>{l("Очередь работает, пока открыто приложение. Закрытие этого окна её не отменяет.", "Черга працює, доки відкрито застосунок. Закриття цього вікна її не скасовує.")}</p>
        <button type="button" className="button" disabled={state.submitting} onClick={() => void state.cancel()}>{l("Отменить оставшиеся", "Скасувати решту")}</button></>}
    </>}
    {state.statusError && <p role="alert">{l("Не удалось прочитать состояние. Не запускайте повторно вслепую.", "Не вдалося прочитати стан. Не запускайте повторно наосліп.")} <button type="button" className="button" onClick={state.refresh}>{l("Проверить состояние", "Перевірити стан")}</button></p>}
    {state.hasUnknown && <div role="alert"><p>{l("Результат части отправок неизвестен. Автоматического повтора не будет.", "Результат частини надсилань невідомий. Автоматичного повтору не буде.")}</p>
      <label><input type="checkbox" checked={state.allowRepeat} onChange={event => state.setAllowRepeat(event.target.checked)} />{l("Разрешаю новый повтор: возможен дубль сообщения", "Дозволяю новий повтор: можливий дубль повідомлення")}</label></div>}
  </div>;
}

export function TelegramCarAction({ source, projectId, carId, title, collection, locale, read, write, settings }: {
  source: string; projectId: number; carId: number; title: string; collection: AuctionCollection; locale: Locale;
  read: SourceWorkspaceProps["read"]; write?: SourceWorkspaceProps["write"]; settings: () => void;
}) {
  const state = useTelegramDelivery(source, projectId, `car:${carId}`, locale, read, write);
  const history = useTelegramHistory(source, projectId, read);
  const sent = history.data?.sent_car_ids.includes(carId) || state.batch?.jobs.some(job => job.state === "SENT");
  const label = `${locale === "uk" ? sent ? "Надіслати ще раз у Telegram" : "Надіслати в Telegram" : sent ? "Отправить ещё раз в Telegram" : "Отправить в Telegram"}: ${title}`;
  return <div className="telegram-car-action">
    <button type="button" className="button telegram-button telegram-icon-button" aria-label={label} title={label}
      aria-busy={state.pending} disabled={state.blocked || !write} onClick={() => void state.send([carId], collection)}><TelegramLogo /></button>
    <div className="telegram-car-feedback">{sent && <small className="telegram-sent-before">{locale === "uk" ? "Раніше надіслано · можна повторити" : "Ранее отправлено · можно повторить"}</small>}
    <TelegramDeliveryProgress state={state} locale={locale} />
    {state.unresolved && <p role="status">{locale === "uk" ? "Постановка не підтверджена. Натисніть іконку знову: перевіримо той самий запит без нової копії." : "Постановка не подтверждена. Нажмите иконку снова: проверим тот же запрос без новой копии."}</p>}
    {state.error && <div role="alert">{state.error} <button type="button" className="button" onClick={settings}>{locale === "uk" ? "Налаштування" : "Настройки"}</button></div>}</div>
  </div>;
}

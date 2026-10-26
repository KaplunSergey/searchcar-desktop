import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { computerTimeZone } from "./auction-time-client";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";

type Status = { project_id: number; source_key: string; enabled: boolean;
  recipient: { project_id: number; title: string; chat_id: number; binding_id: string } | null;
  counts: Partial<Record<"QUEUED" | "IN_FLIGHT" | "SENT" | "UNKNOWN" | "FAILED" | "CANCELLED", number>>;
  issue: string | null; max_result_age_minutes: number };

export function checkedRescheduleStatus(value: Status, source: string, projectId: number) {
  if (!value || value.project_id !== projectId || value.source_key !== source || typeof value.enabled !== "boolean"
    || value.recipient && (value.recipient.project_id !== projectId || typeof value.recipient.title !== "string"
      || !Number.isSafeInteger(value.recipient.chat_id) || !/^[a-f0-9]{32}$/.test(value.recipient.binding_id))
    || !value.counts || typeof value.counts !== "object" || Array.isArray(value.counts)
    || Object.entries(value.counts).some(([key, count]) => !["QUEUED", "IN_FLIGHT", "SENT", "UNKNOWN", "FAILED", "CANCELLED"].includes(key)
      || !Number.isSafeInteger(count) || count < 0)) throw new Error("telegram_context_changed");
  return value;
}

export function TelegramReschedules({ source, projectId, locale, read, write, compact = false }:
  Pick<SourceWorkspaceProps, "source" | "read" | "write"> & { projectId: number; locale: Locale; compact?: boolean }) {
  const client = useQueryClient(), [saving, setSaving] = useState<boolean | null>(null), [error, setError] = useState(false);
  const pending = saving !== null;
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const key = ["telegram-reschedules", source, projectId], path = `/projects/${projectId}/telegram-reschedules?source_key=${source}`;
  const query = useQuery<Status>({ queryKey: key, queryFn: async () => checkedRescheduleStatus(await read(path), source, projectId),
    retry: false, refetchInterval: 5000 });
  const data = query.data, counts = data?.counts || {};
  const change = async (enabled: boolean) => {
    const zone = computerTimeZone();
    if (!write || pending || !zone) { setError(true); return; }
    setSaving(enabled); setError(false);
    try {
      const result = await write<Status>(path, { enabled, computer_timezone: zone, binding_id: data?.recipient?.binding_id || null });
      client.setQueryData(key, checkedRescheduleStatus(result, source, projectId));
    } catch { setError(true); void query.refetch(); }
    finally { setSaving(null); }
  };
  if (compact && !counts.QUEUED && !counts.IN_FLIGHT && !counts.UNKNOWN && !counts.FAILED && !data?.issue) return null;
  const issue = data?.issue === "RUN_INCOMPLETE" ? l("Переносы не отправлены: поиск завершился не полностью.", "Перенесення не надіслані: пошук завершився не повністю.")
    : data?.issue === "SEARCH_CHANGED" ? l("Изменились условия проекта. Нужен новый полный поиск.", "Змінилися умови проєкту. Потрібен новий повний пошук.")
    : data?.issue === "RESULT_EXPIRED" ? l("Данные устарели. Нужно обновить проект.", "Дані застаріли. Потрібно оновити проєкт.")
    : data?.issue === "CLOCK_REQUIRED" ? l("Уведомления приостановлены: ожидаем часовой пояс компьютера.", "Сповіщення призупинені: очікуємо часовий пояс комп’ютера.")
    : data?.issue === "DATA_RESTORED" ? l("После восстановления данных уведомления выключены. Их можно включить здесь.", "Після відновлення даних сповіщення вимкнені. Їх можна ввімкнути тут.") : null;
  return <section className={compact ? "telegram-auto-progress telegram-reschedule-progress" : "telegram-reschedule-settings"}
    aria-busy={pending} aria-label={l("Уведомления о переносах", "Сповіщення про перенесення")}>
    {!compact && <><h3>{l("Перенос аукциона", "Перенесення аукціону")}</h3>
      <label className="telegram-auto-toggle"><input type="checkbox" checked={saving ?? Boolean(data?.enabled)} disabled={!data || !write || query.isError || pending}
        onChange={event => void change(event.target.checked)} />{l("Уведомлять о переносе аукциона", "Сповіщати про перенесення аукціону")}</label>
      <p>{l("Включено по умолчанию. После полного успешного поиска отправим старую и новую дату только для машины, уже отправленной в этот чат. Исходное сообщение не меняется.",
        "Типово ввімкнено. Після повного успішного пошуку надішлемо стару й нову дату лише для авто, уже надісланого в цей чат. Початкове повідомлення не змінюється.")}</p>
      <p className="auction-snapshot">{l("Старые переносы при включении не рассылаются. Можно выключить независимо от автоотправки «Сегодня»; ожидающие уведомления отменятся. Приложение должно быть открыто.",
        "Минулі перенесення при ввімкненні не розсилаються. Можна вимкнути незалежно від автонадсилання «Сьогодні»; сповіщення в черзі скасуються. Застосунок має бути відкритий.")}</p>
      {data?.recipient ? <p>{l("Получатель", "Одержувач")}: <b>{data.recipient.title}</b> · {data.recipient.chat_id}</p>
        : data && <p>{l("Сначала подключите получателя выше.", "Спочатку підключіть одержувача вище.")}</p>}
      {data && <p className="auction-snapshot">{l("Защита от устаревших данных", "Захист від застарілих даних")}: {data.max_result_age_minutes} {l("минут после поиска.", "хвилин після пошуку.")}</p>}
    </>}
    {query.isPending && !compact && <p role="status">{l("Читаем настройку…", "Читаємо налаштування…")}</p>}
    {query.isError && <p role="alert">{l("Не удалось обновить уведомления о переносах.", "Не вдалося оновити сповіщення про перенесення.")} <button type="button" className="button" onClick={() => void query.refetch()}>{l("Повторить", "Повторити")}</button></p>}
    {pending && <p role="status">{l("Сохраняем изменение…", "Зберігаємо зміну…")}</p>}
    {issue && <p role="status">{issue}</p>}
    {data && Object.keys(counts).length > 0 && <p role="status">{l("Переносы", "Перенесення")}: {l("отправлено", "надіслано")} {counts.SENT || 0} · {l("в очереди", "у черзі")} {counts.QUEUED || 0}
      {Boolean(counts.IN_FLIGHT) && ` · ${l("отправляется", "надсилається")}: ${counts.IN_FLIGHT}`}</p>}
    {Boolean(counts.UNKNOWN) && <p role="alert">{l("Результат части уведомлений неизвестен. Автоматического повтора не будет.", "Результат частини сповіщень невідомий. Автоматичного повтору не буде.")}</p>}
    {Boolean(counts.FAILED) && <p role="alert">{l("Не все уведомления отправлены. Проверьте получателя и доступ Telegram.", "Не всі сповіщення надіслані. Перевірте одержувача й доступ Telegram.")}</p>}
    {Boolean(counts.QUEUED) && <button type="button" className="button" disabled={!write || pending || query.isError} onClick={() => void change(false)}>{l("Выключить и отменить ожидающие", "Вимкнути й скасувати очікувані")}</button>}
    {error && <p role="alert">{l("Не удалось подтвердить изменение. Проверьте состояние настройки.", "Не вдалося підтвердити зміну. Перевірте стан налаштування.")}</p>}
  </section>;
}

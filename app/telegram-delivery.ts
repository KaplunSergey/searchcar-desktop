import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
import { computerTimeZone, type AuctionCollection } from "./auction-time-client";

export type TelegramJob = { id: string; car_id: number; state: "QUEUED" | "IN_FLIGHT" | "SENT" | "FAILED" | "UNKNOWN" | "CANCELLED"; previously_sent: boolean; message_id: number | null; error_code: string | null; not_before_ms: number };
export type TelegramBatch = { id: string; project_id: number; source_key: string; recipient: { title: string; chat_id: number; chat_type: string }; jobs: TelegramJob[] };
type Intent = { intent_id: string; car_ids: number[]; collection: AuctionCollection; computer_timezone: string };
type Attempt = { request: Intent; batch?: TelegramBatch };
export const deliveryActive = (batch?: TelegramBatch) => !!batch?.jobs.some(job => job.state === "QUEUED" || job.state === "IN_FLIGHT");

export async function checkTelegramSend(source: string, projectId: number, read: SourceWorkspaceProps["read"]) {
  const status = await read<{ available: boolean; delivery_enabled: boolean; recipient: { project_id: number } | null }>(
    `/projects/${projectId}/telegram?source_key=${encodeURIComponent(source)}`);
  if (status.recipient && status.recipient.project_id !== projectId) throw new Error("context");
  if (!status.available) throw new Error("unavailable");
  if (!status.recipient) throw new Error("recipient");
  if (status.delivery_enabled !== true) throw new Error("delivery");
  return status;
}

export function checkedTelegramBatch(value: TelegramBatch, source: string, projectId: number, request: Pick<Intent, "intent_id" | "car_ids">) {
  if (!value || value.id !== request.intent_id || value.project_id !== projectId || value.source_key !== source
    || !value.recipient || typeof value.recipient.title !== "string" || !Array.isArray(value.jobs) || value.jobs.length !== request.car_ids.length
    || new Set(value.jobs.map(job => job.car_id)).size !== request.car_ids.length
    || value.jobs.some(job => !request.car_ids.includes(job.car_id) || !/^[a-f0-9]{32}$/.test(job.id)
      || !["QUEUED", "IN_FLIGHT", "SENT", "FAILED", "UNKNOWN", "CANCELLED"].includes(job.state)
      || job.state === "SENT" && (!Number.isSafeInteger(job.message_id) || job.message_id! <= 0))) throw new Error("context");
  return value;
}

export function useTelegramHistory(source: string, projectId: number, read: SourceWorkspaceProps["read"]) {
  return useQuery<{ project_id: number; source_key: string; sent_car_ids: number[] }>({
    queryKey: ["telegram-history", source, projectId], retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    queryFn: async () => {
      const data = await read<{ project_id: number; source_key: string; sent_car_ids: number[] }>(`/projects/${projectId}/telegram-history?source_key=${encodeURIComponent(source)}`);
      if (data.project_id !== projectId || data.source_key !== source || !Array.isArray(data.sent_car_ids) || data.sent_car_ids.some(id => !Number.isSafeInteger(id) || id <= 0)) throw Error("context");
      return data;
    },
  });
}

export function useTelegramDelivery(source: string, projectId: number, actionKey: string, locale: Locale, read: SourceWorkspaceProps["read"], write?: SourceWorkspaceProps["write"]) {
  const client = useQueryClient(), lastKey = ["telegram-last-action", source, projectId, actionKey];
  const [attempt, setAttempt] = useState<Attempt | null>(() => client.getQueryData<Attempt>(lastKey) || null);
  const [submitting, setSubmitting] = useState(false), [error, setError] = useState<string | null>(null);
  const [allowRepeat, setAllowRepeat] = useState(false);
  const running = useRef(false);
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const queryKey = ["telegram-delivery", source, projectId, attempt?.request.intent_id];
  const query = useQuery<TelegramBatch>({ queryKey, initialData: attempt?.batch,
    enabled: !!attempt?.batch, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
    refetchInterval: query => query.state.error ? false : deliveryActive(query.state.data) ? 1000 : false,
    queryFn: async () => {
      if (!attempt) throw Error("context");
      const batch = checkedTelegramBatch(await read<TelegramBatch>(`/projects/${projectId}/telegram-deliveries/${attempt.request.intent_id}?source_key=${encodeURIComponent(source)}`), source, projectId, attempt.request);
      if (!deliveryActive(batch)) void client.invalidateQueries({ queryKey: ["telegram-history", source, projectId] });
      return batch;
    },
  });
  const batch = query.data || attempt?.batch;
  const unresolved = !!attempt && !attempt.batch;
  const hasUnknown = !!batch?.jobs.some(job => job.state === "UNKNOWN");
  const pending = submitting || deliveryActive(batch);
  const blocked = pending || hasUnknown && !allowRepeat;
  function remember(value: Attempt) { client.setQueryData(lastKey, value); setAttempt(value); }
  function failure(cause: unknown) {
    const code = cause && typeof cause === "object" && "code" in cause ? String(cause.code) : cause instanceof Error ? cause.message : "";
    return code === "recipient" || code === "telegram_recipient_required" ? l("Для проекта не выбран получатель. Выберите его в настройках Telegram.", "Для проєкту не вибрано одержувача. Виберіть його в налаштуваннях Telegram.")
      : ["delivery", "unavailable", "telegram_delivery_not_configured", "telegram_chat_pairing_disabled"].includes(code) ? l("Отправка ещё не подключена. Сообщения не отправлены.", "Надсилання ще не підключено. Повідомлення не надіслані.")
      : code === "telegram_selection_changed" ? l("Список изменился. Обновите выбор автомобилей.", "Список змінився. Оновіть вибір автомобілів.")
      : code === "timezone" ? l("Не удалось определить часовой пояс компьютера.", "Не вдалося визначити часовий пояс комп’ютера.")
      : l("Действие не подтверждено. Проверьте состояние перед повтором.", "Дію не підтверджено. Перевірте стан перед повтором.");
  }
  async function send(ids: number[], collection: AuctionCollection) {
    if (running.current || blocked || !write) return;
    running.current = true; setSubmitting(true); setError(null);
    try {
      await checkTelegramSend(source, projectId, read);
      const zone = computerTimeZone(); if (!zone) throw Error("timezone");
      const intent = unresolved ? attempt.request : { intent_id: crypto.randomUUID().replaceAll("-", ""), car_ids: [...ids], collection, computer_timezone: zone };
      if (!intent.car_ids.length || intent.car_ids.length > 1000) throw Error("selection");
      remember({ request: intent }); setAllowRepeat(false);
      const value = checkedTelegramBatch(await write<TelegramBatch>(`/projects/${projectId}/telegram-deliveries?source_key=${encodeURIComponent(source)}`, intent), source, projectId, intent);
      client.setQueryData(["telegram-delivery", source, projectId, intent.intent_id], value);
      remember({ request: intent, batch: value });
      void client.invalidateQueries({ queryKey: ["telegram-history", source, projectId] });
    } catch (cause) {
      const code = cause && typeof cause === "object" && "code" in cause ? String(cause.code) : "";
      if (["telegram_selection_changed", "telegram_request_invalid", "telegram_recipient_required", "telegram_listing_link_unavailable", "telegram_delivery_not_configured", "telegram_chat_pairing_disabled"].includes(code)) {
        client.removeQueries({ queryKey: lastKey, exact: true }); setAttempt(null);
      }
      setError(failure(cause));
    } finally { running.current = false; setSubmitting(false); }
  }
  async function cancel() {
    if (!attempt?.batch || running.current || !write) return;
    running.current = true; setSubmitting(true); setError(null);
    try {
      const value = checkedTelegramBatch(await write<TelegramBatch>(`/projects/${projectId}/telegram-deliveries/${attempt.request.intent_id}/cancel?source_key=${encodeURIComponent(source)}`, {}), source, projectId, attempt.request);
      client.setQueryData(queryKey, value); remember({ request: attempt.request, batch: value });
    } catch (cause) { setError(failure(cause)); }
    finally { running.current = false; setSubmitting(false); }
  }
  return { pending, blocked, unresolved, selectionLocked: pending || unresolved, error, batch, send, cancel, submitting,
    intentCount: attempt?.request.car_ids.length || 0,
    hasUnknown, allowRepeat, setAllowRepeat, statusError: query.isError, refresh: () => { void query.refetch(); } };
}

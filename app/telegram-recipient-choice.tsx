import { useQuery } from "@tanstack/react-query";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";

export type SavedRecipients = {
  available: boolean; issue?: string | null;
  recipients: { id: string; bot_id: number; title: string; chat_id: number; chat_type: string }[];
};

export function TelegramRecipientChoice({ locale, read, value, onChange, enabled = true, disabled = false, emptyLabel }: {
  locale: Locale; read: SourceWorkspaceProps["read"]; value: string; onChange: (value: string) => void;
  enabled?: boolean; disabled?: boolean; emptyLabel?: string;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const query = useQuery<SavedRecipients>({ queryKey: ["telegram-recipients"], queryFn: () => read("/telegram/recipients"),
    enabled, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false });
  const types: Record<string, string> = { private: l("Личные сообщения", "Особисті повідомлення"),
    group: l("Группа", "Група"), supergroup: l("Группа", "Група"), channel: l("Канал", "Канал") };
  const selected = (query.data?.recipients || []).find(recipient => recipient.id === value);
  return <div className="telegram-recipient-choice">
    <label>{l("Получатель Telegram", "Одержувач Telegram")}
      <select aria-label={l("Получатель Telegram", "Одержувач Telegram")} value={value} disabled={disabled || !query.data?.available}
        onChange={event => onChange(event.target.value)}>
        <option value="">{emptyLabel || l("Без Telegram", "Без Telegram")}</option>
        {(query.data?.recipients || []).map(recipient => <option key={recipient.id} value={recipient.id}>
          {recipient.title} · {types[recipient.chat_type] || recipient.chat_type} · ID {recipient.chat_id}
        </option>)}
        <option value="new">{l("Добавить нового…", "Додати нового…")}</option>
      </select>
    </label>
    {selected && <p className="auction-snapshot">{selected.title} · {types[selected.chat_type] || selected.chat_type} · ID {selected.chat_id}</p>}
    {query.isFetching && <small role="status">{l("Чтение получателей…", "Читання одержувачів…")}</small>}
    {query.isError && <p role="alert">{l("Не удалось прочитать получателей.", "Не вдалося прочитати одержувачів.")} <button type="button" className="button" disabled={disabled} onClick={() => { void query.refetch(); }}>{l("Повторить", "Повторити")}</button></p>}
    {query.data && !query.data.available && <p className="auction-snapshot">{l("Сервис общего бота пока недоступен. Проект можно создать без Telegram.", "Сервіс спільного бота поки недоступний. Проєкт можна створити без Telegram.")}</p>}
  </div>;
}

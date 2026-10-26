import { useRef, useState, type MouseEvent, type ReactNode } from "react";
import { TelegramLogo } from "./telegram-logo";
import type { Locale } from "./translations";

export function TelegramLinkActions({ url, locale, openLabel, pending, onOpen, children }: {
  url: string; locale: Locale; openLabel: string; pending: boolean;
  onOpen: (event: MouseEvent<HTMLAnchorElement>) => void | Promise<void>;
  children?: ReactNode;
}) {
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const [copyState, setCopyState] = useState<"idle" | "copying" | "copied" | "failed">("idle");
  const copying = useRef(false);
  async function copy() {
    if (pending || copying.current) return;
    copying.current = true; setCopyState("copying");
    try { await navigator.clipboard.writeText(url); setCopyState("copied"); }
    catch { setCopyState("failed"); }
    finally { copying.current = false; }
  }
  return <>
    <div className="telegram-project-actions telegram-link-actions">
      <a className="button telegram-bot-link" href={url} target="_blank" rel="noopener noreferrer" aria-disabled={pending}
        onClick={event => { if (pending) { event.preventDefault(); return; } void onOpen(event); }}>
        <TelegramLogo /><span>{openLabel}</span></a>
      <button className="button" type="button" disabled={pending || copyState === "copying"} onClick={() => void copy()}>
        {copyState === "copying" ? l("Копируем…", "Копіюємо…") : l("Скопировать ссылку", "Скопіювати посилання")}</button>
      {children}
    </div>
    {copyState === "copied" && <p role="status">{l("Ссылка скопирована. Можно переслать её в сообщении.", "Посилання скопійовано. Можна переслати його в повідомленні.")}</p>}
    {copyState === "failed" && <div className="telegram-copy-fallback">
      <p role="alert">{l("Не удалось скопировать. Выделите ссылку ниже и скопируйте вручную.", "Не вдалося скопіювати. Виділіть посилання нижче та скопіюйте вручну.")}</p>
      <label>{l("Ссылка для копирования", "Посилання для копіювання")}
        <input type="text" readOnly value={url} onFocus={event => event.currentTarget.select()} />
      </label>
    </div>}
  </>;
}

import { useState } from "react";
import { TelegramProject } from "./telegram-project";
import { TelegramSharedSetup } from "./telegram-shared-setup";
import { TelegramAutomatic } from "./telegram-automatic";
import { TelegramReschedules } from "./telegram-reschedules";
import { useSourceCatalog, sourceDefinition } from "./source-catalog";
import { sourceName } from "./source-catalog";
import type { Locale } from "./translations";
import type { SourceWorkspaceProps } from "./source-workspace-contract";

export function TelegramSettings({ locale, projects, read, write, saveLegacy, native = true, source, initialProjectId, projectsLoading, projectsError, retryProjects, imageUrl }: {
  locale: Locale; projects: { id: number; source_key: string; name: string; telegram_url?: string | null }[];
  read: SourceWorkspaceProps["read"]; write: NonNullable<SourceWorkspaceProps["write"]>;
  saveLegacy?: (projectId: number, url: string) => Promise<void>;
  native?: boolean;
  source?: string;
  initialProjectId?: number | null;
  projectsLoading?: boolean; projectsError?: boolean; retryProjects?: () => void;
  imageUrl?: SourceWorkspaceProps["imageUrl"];
}) {
  const [selected, setSelected] = useState(initialProjectId ? `${source || "encar"}:${initialProjectId}` : "");
  const catalog = useSourceCatalog();
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const availableProjects = projects.filter(item => item.source_key === (source || "encar"));
  const project = availableProjects.find(item => `${item.source_key}:${item.id}` === selected);
  if (!source && !availableProjects.length && !projectsLoading && !projectsError) return null;
  return <>
    {native && source && <TelegramSharedSetup locale={locale} open={url => write("/desktop/open-external", { url })} />}
    <section className="panel settings telegram-settings">
      {projectsLoading && !availableProjects.length && <p role="status">{l("Читаем проекты…", "Читаємо проєкти…")}</p>}
      {projectsError && <p role="alert">{l("Не удалось обновить список проектов.", "Не вдалося оновити список проєктів.")} {retryProjects && <button type="button" className="button" onClick={retryProjects}>{l("Повторить", "Повторити")}</button>}</p>}
      <label>{l("Проект для настройки Telegram", "Проєкт для налаштування Telegram")}
        <select disabled={!availableProjects.length} value={project ? selected : ""} onChange={event => setSelected(event.target.value)}>
          <option value="">{l("Выберите проект…", "Виберіть проєкт…")}</option>
          {availableProjects.map(item => <option key={`${item.source_key}:${item.id}`} value={`${item.source_key}:${item.id}`}>{sourceName(item.source_key)} · {item.name}</option>)}
        </select>
      </label>
      {!availableProjects.length && !projectsLoading && !projectsError && <p>{l("Сначала создайте проект, затем выберите для него получателя здесь.", "Спочатку створіть проєкт, потім виберіть для нього одержувача тут.")}</p>}
      {native && project && <TelegramProject key={selected} source={project.source_key} projectId={project.id} locale={locale} read={read} write={write} initialExpanded />}
      {native && project && sourceDefinition(project.source_key, catalog)?.family === "AUCTION" && <TelegramAutomatic key={`auto:${selected}`} source={project.source_key} projectId={project.id} projectName={project.name} locale={locale} read={read} write={write} imageUrl={imageUrl} />}
      {native && project && sourceDefinition(project.source_key, catalog)?.family === "AUCTION" && <TelegramReschedules key={`reschedules:${selected}`} source={project.source_key} projectId={project.id} locale={locale} read={read} write={write} />}
      {project?.source_key === "encar" && saveLegacy && <LegacyLink key={`legacy:${selected}`} url={project.telegram_url || ""} locale={locale} save={url => saveLegacy(project.id, url)} />}
    </section>
  </>;
}

function LegacyLink({ url, locale, save }: { url: string; locale: Locale; save: (url: string) => Promise<void> }) {
  const [value, setValue] = useState(url), [pending, setPending] = useState(false), [error, setError] = useState(false), [saved, setSaved] = useState(false);
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  return <form onSubmit={async event => {
    event.preventDefault(); if (pending) return; setPending(true); setError(false); setSaved(false);
    try { await save(value.trim()); setSaved(true); } catch { setError(true); } finally { setPending(false); }
  }}>
    <label>{l("Ссылка на Telegram-канал Encar", "Посилання на Telegram-канал Encar")}<input type="url" value={value} onChange={event => { setValue(event.target.value); setSaved(false); }} placeholder="https://t.me/channel" /></label>
    <p>{l("Ссылка только открывает канал и не подключает отправку ботом.", "Посилання лише відкриває канал і не підключає надсилання ботом.")}</p>
    <button type="submit" className="button" disabled={pending}>{l("Сохранить ссылку", "Зберегти посилання")}</button>
    {saved && <p role="status">{l("Ссылка сохранена", "Посилання збережено")}</p>}
    {error && <p role="alert">{l("Не удалось сохранить ссылку", "Не вдалося зберегти посилання")}</p>}
  </form>;
}

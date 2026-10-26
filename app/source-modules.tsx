import type { ComponentType } from "react";
import { AuctionWorkspace } from "./auction-workspace";
import type { SourceWorkspaceProps } from "./source-workspace-contract";
export type { SourceWorkspaceProps } from "./source-workspace-contract";

export type WorkspaceModules = Readonly<Record<string, ComponentType<SourceWorkspaceProps>>>;
// Explicit developer-shipped UI registrations. A module may reuse a family
// composition or supply its own without changing the shell or another site.
export const WORKSPACE_MODULES: WorkspaceModules = { auction: AuctionWorkspace };

export function MissingWorkspace({ locale }: SourceWorkspaceProps) {
  return <section className="panel" role="status"><h2>{locale === "uk"
    ? "Інтерфейс сайту ще не підключено" : "Интерфейс сайта ещё не подключён"}</h2><p>{locale === "uk"
    ? "Збережені дані не змінюються. Потрібен UI-модуль у новій версії застосунку."
    : "Сохранённые данные не меняются. Нужен UI-модуль в новой версии приложения."}</p></section>;
}

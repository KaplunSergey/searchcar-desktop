import { useCallback, useRef, useState, type ReactNode } from "react";
import type { ActivityProject } from "./activity-dialog";
import { LaunchProgress, launchableProjects, reasonText, type LaunchOptions, type LaunchProject, type LaunchResult } from "./launch-dialog";
import { SOURCE_CATALOG, useSourceCatalog } from "./source-catalog";
import type { Locale } from "./translations";

export function planSourceRefresh(options: LaunchOptions, source: string, catalog = SOURCE_CATALOG) {
  const projects = options.projects.filter((project) => project.source_key === source);
  const ready = launchableProjects(options, catalog).filter((project) => project.source_key === source);
  const active = projects.filter((project) => !!project.active_status);
  const site = options.sources.find((item) => item.source_key === source);
  const unavailable = projects.filter((project) => !project.active_status && !ready.includes(project))
    .map((project) => ({ ...project, reason: !site?.can_search ? site?.reason || "SOURCE_UNAVAILABLE" : project.reason }));
  return { projects, ready, active, unavailable };
}

type RefreshOutcome = {
  projects: LaunchProject[];
  result: Pick<LaunchResult, "projects">;
  unavailable: LaunchProject[];
  activeCount: number;
};

export function AuctionRefresh({ source, locale, projects, disabled, read, refresh, children }: {
  source: string; locale: Locale; projects: ActivityProject[]; disabled: boolean;
  read: <T>(path: string) => Promise<T>; refresh: (ids: number[]) => Promise<LaunchResult>; children: ReactNode;
}) {
  const catalog = useSourceCatalog(), uk = locale === "uk", l = (ru: string, ua: string) => uk ? ua : ru;
  const lock = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<"availability" | "queue" | null>(null);
  const [outcome, setOutcome] = useState<RefreshOutcome | null>(null);
  const resultRef = useRef<HTMLElement>(null);
  const refreshButton = useRef<HTMLButtonElement>(null);
  const dismiss = useCallback(() => {
    if (resultRef.current?.contains(document.activeElement)) refreshButton.current?.focus();
    setOutcome(null);
  }, []);
  const start = async () => {
    if (disabled || lock.current || !projects.length) return;
    lock.current = true; setPending(true); setError(null); setOutcome(null);
    let stage: "availability" | "queue" = "availability";
    try {
      const plan = planSourceRefresh(await read<LaunchOptions>("/scans/launch-options"), source, catalog);
      stage = "queue";
      const result = plan.ready.length ? await refresh(plan.ready.map((project) => project.id)) : { projects: [] };
      const existing = plan.active.filter((project) => project.scan_run_id != null)
        .map((project) => ({ project_id: project.id, status: "ALREADY_ACTIVE" as const, scan_run_id: project.scan_run_id! }));
      setOutcome({ projects: plan.projects, unavailable: plan.unavailable,
        activeCount: plan.active.length + result.projects.filter((item) => item.status === "ALREADY_ACTIVE").length,
        result: { projects: [...existing, ...result.projects] } });
    } catch {
      setError(stage);
    } finally {
      lock.current = false; setPending(false);
    }
  };
  return <>
    <div className="projects-panel-header auction-project-toolbar">{children}<button ref={refreshButton} type="button" className="button projects-refresh-button"
      disabled={disabled || pending || !projects.length} onClick={() => { void start(); }}>
      {pending ? l("Добавляем в очередь…", "Додаємо до черги…") : l("Обновить проекты", "Оновити проєкти")}
    </button></div>
    {error && <p className="warning-text" role="alert">{error === "availability"
      ? l("Не удалось проверить доступность проектов. Обновление не запускалось. Повторите попытку.", "Не вдалося перевірити доступність проєктів. Оновлення не запускалося. Спробуйте ще раз.")
      : l("Не удалось подтвердить добавление в очередь. Проверьте состояние запусков перед повторной попыткой; активные проекты не будут добавлены повторно.", "Не вдалося підтвердити додавання до черги. Перевірте стан запусків перед повторною спробою; активні проєкти не будуть додані повторно.")}</p>}
    {outcome && <section ref={resultRef} className="panel auction-refresh-result" aria-label={l("Результат обновления проектов", "Результат оновлення проєктів")}>
      <p>{l("Подробности сохраняются в разделе «Запуски».", "Подробиці зберігаються в розділі «Запуски».")}</p>
      <p role="status">{l("Добавлено в очередь", "Додано до черги")}: {outcome.result.projects.filter((item) => item.status === "QUEUED").length}
        {` · ${l("Уже активны", "Уже активні")}: ${outcome.activeCount} · ${l("Недоступно", "Недоступно")}: ${outcome.unavailable.length}`}</p>
      {!outcome.projects.length && <p>{l("Нет проектов для обновления.", "Немає проєктів для оновлення.")}</p>}
      {!!outcome.unavailable.length && <ul>{outcome.unavailable.map((project) => <li key={project.id}>{project.name}: {reasonText(project.reason, locale)}</li>)}</ul>}
      {!!outcome.result.projects.length && <LaunchProgress result={outcome.result} projects={outcome.projects} locale={locale} read={read}
        onSuccess={outcome.unavailable.length === 0 ? dismiss : undefined} />}
    </section>}
  </>;
}

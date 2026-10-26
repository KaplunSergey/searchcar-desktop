import { activityProjects, statusLabel, type ActivityProject, type ActivityRun } from "./activity-dialog";
import type { Locale } from "./translations";

export const updateActive = (status: string) => ["QUEUED", "RUNNING", "WAITING_FOR_USER", "CANCEL_REQUESTED", "ACTIVE"].includes(status);

export function latestProjectUpdate(runs: ActivityRun[], projectId: number, projects: ActivityProject[]) {
  // A run can cover several sites. Its global RUNNING state is not the state
  // of every project. Frozen plans also retain names after project deletion.
  for (const run of [...runs].sort((a, b) => b.id - a.id)) {
    const project = activityProjects(run, projects).find((item) => item.id === projectId);
    if (!project) continue;
    const waiting = project.status === "RUNNING" && run.payload?.current_project_id === projectId
      && run.payload?.pagination?.[String(projectId)]?.interaction?.state === "WAITING_FOR_USER";
    return { ...project, status: waiting ? "WAITING_FOR_USER" : project.status, scanId: run.id };
  }
  return null;
}

export function ProjectUpdateStatus({ projectId, status, locale }: { projectId: number; status: string; locale: Locale }) {
  return <span className="project-update" data-project-id={projectId} data-status={status}>
    {statusLabel(status, locale === "uk")}
  </span>;
}

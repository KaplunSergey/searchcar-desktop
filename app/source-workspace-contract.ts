import type { Locale } from "./translations";
import type { SourceKey, View } from "./source-workspace";
import type { ActivityRun } from "./activity-dialog";
import type { LaunchResult } from "./launch-dialog";

// Shell-to-module boundary: no auction/Encar fields or site parsing rules.
export type SourceWorkspaceProps = {
  source: SourceKey; view: View; projectId: number | null; carId: number | null; locale: Locale;
  projects: { id: number; source_key: SourceKey; name: string; cars: number; created_at?: string | null; last_refreshed_at?: string | null; search_page_mode?: "FIRST_PAGE" | "ALL_PAGES" }[];
  projectsLoading: boolean; projectsError: boolean; retryProjects: () => void;
  runs: (ActivityRun & { created_at: string; error?: string | null; source_key?: SourceKey })[];
  runsLoading: boolean; runsError: boolean; retryRuns: () => void;
  read: <T>(path: string) => Promise<T>;
  navigate: (view: View, projectId?: number | null, carId?: number | null) => void;
  imageUrl: (path: string) => string; openListing: (url: string) => void;
  sourceTestMode?: boolean;
  canSearch?: boolean;
  busyProjects?: number[];
  write?: <T>(path: string, body: unknown) => Promise<T>;
  refreshProjects?: (ids: number[]) => Promise<LaunchResult>;
  removeProjectCar?: (projectId: number, carId: number) => Promise<void>;
  requestProjectDeletion?: (projectId: number) => void;
};

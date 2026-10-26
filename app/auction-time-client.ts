/** Runtime clock boundary only. No saved timezone or inferred source timezone. */
export const AUCTION_QUERY_KEY = ["auction"] as const;

export type AuctionTimeView = {
  computer_timezone: string;
  event_kind: "START" | "END" | "UNKNOWN";
  precision: "EXACT" | "DATE_ONLY" | "UNKNOWN";
  local_date: string | null;
  local_datetime: string | null;
  is_today: boolean | null;
  remaining_seconds: number | null;
  time_state: "TIME_UNKNOWN" | "EVENT_KIND_UNKNOWN" | "TIME_NOT_SPECIFIED" | "LOCAL_DAY_UNCERTAIN"
    | "UPCOMING_START" | "UPCOMING_END" | "SCHEDULED_START_PASSED" | "SCHEDULED_END_PASSED";
};

export type AuctionLot = {
  id: number;
  source_key: string;
  source_listing_id: string;
  project_search_status?: "FOUND" | "NOT_FOUND_IN_SEARCH";
  url: string;
  title: string | null;
  image: string | null;
  details: Record<string, unknown>;
  money_values: Array<{
    amount_minor: number | null; currency: string; meaning: string;
    state: "KNOWN" | "UNKNOWN" | "ON_REQUEST"; auction_round: string | null;
  }>;
  round: {
    id: string; evidence_kind: "PROVISIONAL" | "SOURCE_SALE_ID" | "SOURCE_TRANSITION";
    evidence_key: string | null; source_sale_id: string | null;
    last_observed_at: string; source_updated_at: string | null;
  } | null;
  schedule: {
    id: string | null; version: number | null; change_kind: "INITIAL" | "CONFIRMED" | "RESCHEDULED" | null;
    observed_at: string | null; event_kind: AuctionTimeView["event_kind"]; precision: AuctionTimeView["precision"];
    utc_at: string | null; source_timezone: string | null; local_date: string | null;
    local_datetime: string | null; raw: string | null; raw_timezone: string | null; issue: string | null;
    evidence: Record<string, unknown> | null;
  };
  time: AuctionTimeView;
};

export type AuctionClockContext = {
  generated_at: string; computer_timezone: string; local_date: string;
  day_start_utc: string; day_end_utc: string;
};
export type ProjectAuctionView = AuctionClockContext & {
  project_id: number; source_key: string; today_only: boolean; lots: AuctionLot[];
  collection?: AuctionCollection;
};
export type AuctionCollection = "active" | "today" | "future" | "undated" | "archive";
export type SingleAuctionView = AuctionClockContext & { lot: AuctionLot };
export type AuctionSnapshotView = SingleAuctionView & { captured_round_id: string | null };

/** Recalculate a known instant locally; elapsed time never proves LIVE/SOLD. */
export function auctionTimeAt(lot: Pick<AuctionLot, "schedule" | "time">, nowMs: number): AuctionTimeView {
  const time = lot.time, raw = lot.schedule.utc_at;
  if (time.precision !== "EXACT" || !["START", "END"].includes(time.event_kind)
    || !raw || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(raw)) return time;
  const target = Date.parse(raw);
  if (!Number.isFinite(target) || !Number.isFinite(nowMs)) return time;
  return { ...time, remaining_seconds: Math.max(0, Math.ceil((target - nowMs) / 1000)),
    time_state: target > nowMs ? (time.event_kind === "START" ? "UPCOMING_START" : "UPCOMING_END")
      : (time.event_kind === "START" ? "SCHEDULED_START_PASSED" : "SCHEDULED_END_PASSED") };
}

export function watchAuctionCountdown(onTick: (now: number) => void, now = Date.now, win = window, doc = document): () => void {
  let active = true;
  const tick = () => { if (active && doc.visibilityState !== "hidden") onTick(now()); };
  const timer = win.setInterval(tick, 1000);
  win.addEventListener("focus", tick);
  win.addEventListener("pageshow", tick);
  doc.addEventListener("visibilitychange", tick);
  tick();
  return () => {
    active = false; win.clearInterval(timer);
    win.removeEventListener("focus", tick); win.removeEventListener("pageshow", tick);
    doc.removeEventListener("visibilitychange", tick);
  };
}

export function computerTimeZone(): string | null {
  try {
    // A DateTimeFormat instance captures its zone at construction; never reuse
    // a formatter created at startup after sleep/system-setting changes.
    const zone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone === "GMT") return "UTC";
    return typeof zone === "string" && zone.length <= 64 && (zone === "UTC" ||
      /^[A-Za-z0-9_+-]+(?:\/[A-Za-z0-9_+-]+)+$/.test(zone)) ? zone : null;
  } catch {
    return null;
  }
}

export function withAuctionTimeZone(path: string, readZone = computerTimeZone): string {
  if (!/^\/(?:projects\/\d+\/auction-lots|cars\/\d+\/auction|scans\/\d+\/auction-report)(?:\?|$)/.test(path)) return path;
  const zone = readZone();
  if (!zone) throw new Error("computer_timezone_unavailable");
  const url = new URL(path, "https://searchcar.invalid");
  url.searchParams.set("computer_timezone", zone);
  return `${url.pathname}${url.search}`;
}

export function watchAuctionClock(
  onRefresh: (zone: string | null) => void,
  readZone = computerTimeZone,
  win = window,
  doc = document,
): () => void {
  let active = true;
  const refresh = () => {
    if (active && doc.visibilityState !== "hidden") onRefresh(readZone());
  };
  win.addEventListener("focus", refresh);
  win.addEventListener("pageshow", refresh);
  doc.addEventListener("visibilitychange", refresh);
  // The periodic refresh also covers midnight/countdowns and timezone changes
  // made while this window remains focused. No work while hidden.
  const timer = win.setInterval(refresh, 60000);
  refresh();
  return () => {
    active = false;
    win.clearInterval(timer);
    win.removeEventListener("focus", refresh);
    win.removeEventListener("pageshow", refresh);
    doc.removeEventListener("visibilitychange", refresh);
  };
}

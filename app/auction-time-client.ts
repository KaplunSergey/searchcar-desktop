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
};
export type SingleAuctionView = AuctionClockContext & { lot: AuctionLot };

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
  if (!/^\/(?:projects\/\d+\/auction-lots|cars\/\d+\/auction)(?:\?|$)/.test(path)) return path;
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

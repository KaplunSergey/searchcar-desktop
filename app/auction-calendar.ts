import type { AuctionLot } from "./auction-time-client";
import { SOURCE_CATALOG, sourceName, type SourceCatalog } from "./source-catalog";
import { sourceListingLink } from "./source-listing-links";
import type { Locale } from "./translations";

export type CalendarIssue = "NO_START" | "NO_LINK" | "UNAVAILABLE" | "PAST";
export type CalendarLot = Pick<AuctionLot, "source_key" | "source_listing_id" | "title" | "url" | "details" | "project_search_status"> & {
  schedule: Pick<AuctionLot["schedule"], "utc_at">;
  time: Pick<AuctionLot["time"], "event_kind" | "precision">;
  round?: { id: string } | null;
};
const clean = (value: string, length: number) => Array.from(value.replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, " ").replace(/\s+/g, " ").trim()).slice(0, length).join("");
const stamp = (value: number) => new Date(value).toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "Z");
const escapeText = (value: string) => value.replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/;/g, "\\;").replace(/,/g, "\\,");

// RFC 5545 folding counts UTF-8 octets, including the continuation space.
function fold(line: string) {
  const lines: string[] = []; let current = "", bytes = 0;
  for (const char of line) {
    const size = new TextEncoder().encode(char).length;
    if (bytes + size > 75) { lines.push(current); current = " "; bytes = 1; }
    current += char; bytes += size;
  }
  return [...lines, current].join("\r\n");
}

export function auctionCalendar(lot: CalendarLot, nowMs: number, locale: Locale, catalog: SourceCatalog = SOURCE_CATALOG) {
  const fail = (issue: CalendarIssue) => ({ available: false as const, issue });
  if (lot.project_search_status === "NOT_FOUND_IN_SEARCH" || ["LIVE", "CLOSED", "CANCELLED"].includes(String(lot.details.auction_phase))) return fail("UNAVAILABLE");
  const raw = lot.schedule.utc_at;
  if (lot.time.event_kind !== "START" || lot.time.precision !== "EXACT" || !Number.isFinite(new Date(nowMs).getTime())
    || !raw || !/^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)$/.test(raw)) return fail("NO_START");
  const [year, month, day] = raw.slice(0, 10).split("-").map(Number);
  const start = Date.parse(raw);
  if (year < 2000 || year > 9999 || month < 1 || month > 12 || day < 1 || day > new Date(Date.UTC(year, month, 0)).getUTCDate()
    || !Number.isFinite(start) || new Date(start).getUTCFullYear() > 9999 || /[+-]14:(?!00)/.test(raw) || raw.endsWith("-00:00")) return fail("NO_START");
  if (start <= nowMs) return fail("PAST");
  let listing = sourceListingLink(lot, catalog);
  if (!listing || !/^[a-z][a-z0-9_-]{0,40}$/.test(lot.source_key) || !/^[A-Za-z0-9_-]{1,80}$/.test(lot.source_listing_id)) return fail("NO_LINK");
  const url = new URL(listing); url.search = ""; url.hash = ""; listing = url.href;
  if (listing.length > 512) return fail("NO_LINK");
  const l = (ru: string, uk: string) => locale === "uk" ? uk : ru;
  const title = `${clean(lot.title || l("Автомобиль", "Автомобіль"), 120)} · ${sourceName(lot.source_key, catalog)} № ${lot.source_listing_id}`;
  const description = `${listing}\n${l("Напоминание: за 15 минут — проверьте перед сохранением.", "Нагадування: за 15 хвилин — перевірте перед збереженням.")}\n${l("Время окончания неизвестно. Переносы не синхронизируются.", "Час завершення невідомий. Перенесення не синхронізуються.")}`;
  const instant = stamp(start), google = new URL("https://calendar.google.com/calendar/render");
  google.search = new URLSearchParams({ action: "TEMPLATE", text: title, dates: `${instant}/${instant}`, details: description }).toString();
  // Keep the complete listing URL inside the desktop external-open limit.
  const shortTitle = Array.from(title);
  if (google.href.length > 2048) {
    google.searchParams.set("details", `${listing}\n${l("Напомнить за 15 минут.", "Нагадати за 15 хвилин.")}`);
    while (google.href.length > 2048 && shortTitle.length > 1) {
      shortTitle.pop(); google.searchParams.set("text", shortTitle.join("") + "…");
    }
  }
  const outlook = new URL("https://outlook.live.com/calendar/deeplink/compose");
  outlook.search = new URLSearchParams({ path: "/calendar/action/compose", rru: "addevent", subject: title,
    startdt: new Date(start).toISOString(), body: description }).toString();
  // An instant-only event marks the start, not an invented auction duration.
  const uid = `${lot.source_key}-${lot.source_listing_id}-${encodeURIComponent(lot.round?.id || instant)}@calendar.searchcar.invalid`;
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SearchCar//Auction Calendar//EN", "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
    `UID:${uid}`, `DTSTAMP:${stamp(nowMs)}`, `DTSTART:${instant}`, `SUMMARY:${escapeText(title)}`, `DESCRIPTION:${escapeText(description)}`,
    `URL:${listing}`, "TRANSP:TRANSPARENT", "BEGIN:VALARM", "ACTION:DISPLAY", "TRIGGER:-PT15M", `DESCRIPTION:${escapeText(title)}`,
    "END:VALARM", "END:VEVENT", "END:VCALENDAR"].map(fold).join("\r\n") + "\r\n";
  return { available: true as const, title, google_url: google.href, outlook_url: outlook.href, ics, filename: `SearchCar-${lot.source_key}-${lot.source_listing_id}.ics`,
    start_utc: new Date(start).toISOString(), reminder_late: start - nowMs < 900000, google_shortened: google.searchParams.get("text") !== title };
}

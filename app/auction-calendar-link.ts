import { auctionCalendar, type CalendarLot } from "./auction-calendar";
import { sourceListingLink } from "./source-listing-links";
import type { Locale } from "./translations";

export const AUCTION_CALENDAR_PAGE_URL = "https://searchcar-auction-calendar-preview.pages.dev/";
export const REMINDER_SHORTCUT_NAME = "SearchCar Reminder";

type EventLink = { v: 1; source: string; id: string; title: string; url: string; start: string; round: string | null; locale: Locale };
const fields = ["v", "source", "id", "title", "url", "start", "round", "locale"];

// Only public event fields cross devices. The fragment is not sent to the host.
export function calendarPageLink(lot: CalendarLot, nowMs: number, locale: Locale, pageUrl: string): string | null {
  try {
    const page = new URL(pageUrl), host = page.hostname;
    if (page.protocol !== "https:" || page.username || page.password || page.port || page.search || page.hash
      || !host.includes(".") || /^[\d.]+$/.test(host) || host.includes(":") || /(?:^|\.)(?:localhost|local|internal|test|invalid)$/.test(host)) return null;
    const calendar = auctionCalendar(lot, nowMs, locale);
    if (!calendar.available) return null;
    const listing = new URL(sourceListingLink(lot)!); listing.search = ""; listing.hash = "";
    const title = Array.from((lot.title || "").replace(/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/g, " ").replace(/\s+/g, " ").trim()).slice(0, 120);
    const data: EventLink = { v: 1, source: lot.source_key, id: lot.source_listing_id, title: title.join(""), url: listing.href,
      start: calendar.start_utc, round: lot.round?.id || null, locale };
    if (data.round && data.round.length > 128) return null;
    page.hash = encodeURIComponent(JSON.stringify(data));
    while (page.href.length > 2048 && title.length > 1) {
      title.pop(); data.title = title.join("") + "…"; page.hash = encodeURIComponent(JSON.stringify(data));
    }
    return page.href.length <= 2048 ? page.href : null;
  } catch { return null; }
}

export function readCalendarPage(hash: string, nowMs: number) {
  try {
    if (!hash.startsWith("#") || hash.length > 8192) return null;
    const data = JSON.parse(decodeURIComponent(hash.slice(1))) as EventLink;
    if (!data || typeof data !== "object" || Array.isArray(data) || Object.keys(data).length !== fields.length
      || Object.keys(data).some(key => !fields.includes(key)) || data.v !== 1 || !["ru", "uk"].includes(data.locale)
      || ["source", "id", "title", "url", "start"].some(key => typeof data[key as keyof EventLink] !== "string")
      || data.title.length > 260 || data.url.length > 512 || data.source.length > 41 || data.id.length > 80
      || data.start.length > 35 || (data.round !== null && (typeof data.round !== "string" || data.round.length > 128))) return null;
    const lot: CalendarLot = { source_key: data.source, source_listing_id: data.id, title: data.title, url: data.url,
      details: {}, schedule: { utc_at: data.start }, time: { event_kind: "START", precision: "EXACT" }, round: data.round ? { id: data.round } : null };
    const calendar = auctionCalendar(lot, nowMs, data.locale);
    if (!calendar.available) return null;
    const listing = new URL(sourceListingLink(lot)!); listing.search = ""; listing.hash = "";
    const alert = Date.parse(calendar.start_utc) - 15 * 60 * 1000;
    const reminder = alert <= nowMs ? { available: false as const, issue: "REMINDER_PAST" as const } : (() => {
      const notes = `${data.locale === "uk" ? "Початок торгів" : "Начало торгов"}: ${calendar.start_utc}\n${listing.href}\n${data.locale === "uk"
        ? "Розклад із повідомлення. Перенесення автоматично не оновлюються."
        : "Расписание из сообщения. Переносы автоматически не обновляются."}`;
      const payload = { v: 1, title: calendar.title, url: listing.href, start_utc: calendar.start_utc,
        alert_utc: new Date(alert).toISOString(), notes, locale: data.locale };
      const shortcut = new URL("shortcuts://run-shortcut");
      // Shortcuts percent-decodes parameters, not form-style + spaces.
      shortcut.search = new URLSearchParams({ name: REMINDER_SHORTCUT_NAME, input: "text", text: JSON.stringify(payload) }).toString().replace(/\+/g, "%20");
      return { available: true as const, shortcut_url: shortcut.href };
    })();
    return { calendar, listing: listing.href, locale: data.locale, reminder };
  } catch { return null; }
}

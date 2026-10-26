import { readCalendarPage } from "./auction-calendar-link";

/** A file response only: no storage, fetching or calendar writes. */
export function calendarIcsResponse(request: Request, nowMs = Date.now()): Response {
  const headers = new Headers({
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
  });
  const fail = (status: number) => new Response(request.method === "HEAD" ? null : "Calendar event unavailable.", { status, headers });
  if (!["GET", "HEAD"].includes(request.method)) {
    headers.set("Allow", "GET, HEAD"); return fail(405);
  }
  if (request.url.length > 12288) return fail(414);
  const url = new URL(request.url);
  if (url.pathname !== "/event.ics") return fail(404);
  const values = url.searchParams.getAll("event");
  if (values.length !== 1 || [...url.searchParams.keys()].some(key => key !== "event")) return fail(400);
  const event = readCalendarPage("#" + values[0], nowMs);
  if (!event) return fail(400);
  headers.set("Content-Type", "text/calendar; charset=utf-8");
  headers.set("Content-Disposition", `inline; filename="${event.calendar.filename}"`);
  return new Response(request.method === "HEAD" ? null : event.calendar.ics, { headers });
}

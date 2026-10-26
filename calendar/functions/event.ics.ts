import { calendarIcsResponse } from "../../app/auction-calendar-response";

export function onRequest({ request }: { request: Request }): Response {
  return calendarIcsResponse(request);
}

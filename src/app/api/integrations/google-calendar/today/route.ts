import { NextRequest, NextResponse } from "next/server";
import {
  getGoogleCalendarConnectionStatus,
  isGoogleCalendarEnabled,
  listGoogleTodayEvents,
} from "@/lib/integrations/google-calendar";

export async function GET(request: NextRequest) {
  if (!isGoogleCalendarEnabled()) {
    return NextResponse.json({ error: "google_calendar_disabled" }, { status: 503 });
  }

  const calendarId = request.nextUrl.searchParams.get("calendarId");
  const timeMin = request.nextUrl.searchParams.get("timeMin");
  const timeMax = request.nextUrl.searchParams.get("timeMax");

  try {
    const [status, result] = await Promise.all([
      getGoogleCalendarConnectionStatus(),
      listGoogleTodayEvents({ calendarId, timeMin, timeMax }),
    ]);
    return NextResponse.json({
      ok: true,
      status,
      calendarId: result.calendarId,
      timeMin: result.timeMin,
      timeMax: result.timeMax,
      count: result.events.length,
      events: result.events,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown_error";
    const status = detail.includes("not_connected") ? 401 : 500;
    return NextResponse.json({ error: "google_calendar_today_failed", detail }, { status });
  }
}

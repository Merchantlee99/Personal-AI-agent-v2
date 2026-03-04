import { NextRequest, NextResponse } from "next/server";
import {
  buildGoogleOAuthAuthorizationUrl,
  createGoogleOAuthState,
  isGoogleCalendarEnabled,
  isGoogleCalendarReadonly,
} from "@/lib/integrations/google-calendar";

export async function GET(request: NextRequest) {
  if (!isGoogleCalendarEnabled()) {
    return NextResponse.json({ error: "google_calendar_disabled" }, { status: 503 });
  }

  try {
    const returnTo = request.nextUrl.searchParams.get("return_to");
    const mode = request.nextUrl.searchParams.get("mode");
    const state = await createGoogleOAuthState(returnTo);
    const authorizationUrl = buildGoogleOAuthAuthorizationUrl(state.state);

    if (mode === "redirect") {
      return NextResponse.redirect(authorizationUrl);
    }

    return NextResponse.json({
      ok: true,
      readonly: isGoogleCalendarReadonly(),
      authorizationUrl,
      state: state.state,
      createdAt: state.createdAt,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown_error";
    return NextResponse.json({ error: "google_oauth_start_failed", detail }, { status: 500 });
  }
}

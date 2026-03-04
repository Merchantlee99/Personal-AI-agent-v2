import { NextRequest, NextResponse } from "next/server";
import {
  consumeGoogleOAuthState,
  isGoogleCalendarEnabled,
  saveGoogleTokenFromCode,
} from "@/lib/integrations/google-calendar";

export async function GET(request: NextRequest) {
  if (!isGoogleCalendarEnabled()) {
    return NextResponse.json({ error: "google_calendar_disabled" }, { status: 503 });
  }

  const errorCode = request.nextUrl.searchParams.get("error");
  if (errorCode) {
    return NextResponse.json(
      {
        error: "google_oauth_rejected",
        detail: errorCode,
        errorDescription: request.nextUrl.searchParams.get("error_description") ?? null,
      },
      { status: 400 }
    );
  }

  const code = request.nextUrl.searchParams.get("code");
  const state = request.nextUrl.searchParams.get("state");
  if (!code || !state) {
    return NextResponse.json({ error: "google_oauth_invalid_callback", required: ["code", "state"] }, { status: 400 });
  }

  const storedState = await consumeGoogleOAuthState(state);
  if (!storedState) {
    return NextResponse.json({ error: "google_oauth_invalid_state" }, { status: 400 });
  }

  try {
    const token = await saveGoogleTokenFromCode(code);
    return NextResponse.json({
      ok: true,
      connected: true,
      tokenUpdatedAt: token.updatedAt,
      tokenExpiresAt: token.expiresAt,
      scope: token.scope,
      returnTo: storedState.returnTo,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown_error";
    return NextResponse.json({ error: "google_oauth_token_exchange_failed", detail }, { status: 500 });
  }
}

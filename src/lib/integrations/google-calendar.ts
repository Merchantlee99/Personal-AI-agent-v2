import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

type OAuthStateRecord = {
  state: string;
  createdAt: string;
  returnTo: string | null;
};

type TokenStore = {
  accessToken: string;
  refreshToken: string | null;
  scope: string | null;
  tokenType: string | null;
  expiresAt: string | null;
  updatedAt: string;
};

export type GoogleCalendarEvent = {
  id: string;
  summary: string;
  status: string;
  htmlLink: string | null;
  location: string | null;
  start: string | null;
  end: string | null;
};

const DEFAULT_SHARED_ROOT = process.env.SHARED_ROOT_PATH?.trim() || path.join(process.cwd(), "shared_data");
const OAUTH_STATE_TTL_MS = 10 * 60 * 1000;

function parseBoolean(raw: string | undefined, fallback: boolean): boolean {
  if (!raw) {
    return fallback;
  }
  const token = raw.trim().toLowerCase();
  if (token === "1" || token === "true" || token === "yes" || token === "on") {
    return true;
  }
  if (token === "0" || token === "false" || token === "no" || token === "off") {
    return false;
  }
  return fallback;
}

function resolveStorePath(rawPath: string | undefined, fileName: string): string {
  const fallback = path.join(DEFAULT_SHARED_ROOT, "shared_memory", fileName);
  const configured = rawPath?.trim();
  if (!configured) {
    return fallback;
  }
  if (path.isAbsolute(configured)) {
    return configured;
  }
  return path.join(process.cwd(), configured);
}

const TOKEN_PATH = resolveStorePath(process.env.GOOGLE_CALENDAR_TOKEN_PATH, "google_calendar_tokens.json");
const STATE_PATH = resolveStorePath(process.env.GOOGLE_CALENDAR_STATE_PATH, "google_calendar_oauth_state.json");

async function ensureParentDir(filePath: string) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
}

async function readJsonFile<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const raw = await fs.readFile(filePath, "utf-8");
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

async function writeJsonFile(filePath: string, payload: unknown) {
  await ensureParentDir(filePath);
  const tempPath = `${filePath}.tmp`;
  await fs.writeFile(tempPath, JSON.stringify(payload, null, 2), "utf-8");
  await fs.rename(tempPath, filePath);
}

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`missing_env:${name}`);
  }
  return value;
}

function getOAuthConfig() {
  return {
    clientId: requiredEnv("GOOGLE_CALENDAR_OAUTH_CLIENT_ID"),
    clientSecret: requiredEnv("GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET"),
    redirectUri: requiredEnv("GOOGLE_CALENDAR_OAUTH_REDIRECT_URI"),
    scope:
      process.env.GOOGLE_CALENDAR_OAUTH_SCOPES?.trim() || "https://www.googleapis.com/auth/calendar.readonly",
  };
}

export function isGoogleCalendarEnabled() {
  return parseBoolean(process.env.GOOGLE_CALENDAR_ENABLED, false);
}

export function isGoogleCalendarReadonly() {
  return parseBoolean(process.env.GOOGLE_CALENDAR_READONLY, true);
}

export async function createGoogleOAuthState(returnTo: string | null): Promise<OAuthStateRecord> {
  const record: OAuthStateRecord = {
    state: crypto.randomUUID(),
    createdAt: new Date().toISOString(),
    returnTo: returnTo?.trim() ? returnTo.trim() : null,
  };
  await writeJsonFile(STATE_PATH, record);
  return record;
}

export async function consumeGoogleOAuthState(state: string): Promise<OAuthStateRecord | null> {
  const stored = await readJsonFile<OAuthStateRecord | null>(STATE_PATH, null);
  if (!stored || stored.state !== state) {
    return null;
  }
  const createdAt = Date.parse(stored.createdAt);
  if (!Number.isFinite(createdAt)) {
    return null;
  }
  if (Date.now() - createdAt > OAUTH_STATE_TTL_MS) {
    return null;
  }
  await fs.rm(STATE_PATH, { force: true });
  return stored;
}

export function buildGoogleOAuthAuthorizationUrl(state: string): string {
  const config = getOAuthConfig();
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", config.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", config.scope);
  url.searchParams.set("access_type", "offline");
  url.searchParams.set("include_granted_scopes", "true");
  url.searchParams.set("prompt", "consent");
  url.searchParams.set("state", state);
  return url.toString();
}

async function exchangeAuthorizationCode(code: string): Promise<TokenStore> {
  const config = getOAuthConfig();
  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    code,
    grant_type: "authorization_code",
    redirect_uri: config.redirectUri,
  });
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`google_token_exchange_failed:${response.status}:${detail}`);
  }
  const data = (await response.json()) as {
    access_token?: string;
    refresh_token?: string;
    scope?: string;
    token_type?: string;
    expires_in?: number;
  };
  const accessToken = (data.access_token ?? "").trim();
  if (!accessToken) {
    throw new Error("google_token_exchange_missing_access_token");
  }
  const expiresAt =
    typeof data.expires_in === "number" && Number.isFinite(data.expires_in)
      ? new Date(Date.now() + data.expires_in * 1000).toISOString()
      : null;
  return {
    accessToken,
    refreshToken: data.refresh_token?.trim() || null,
    scope: data.scope?.trim() || null,
    tokenType: data.token_type?.trim() || "Bearer",
    expiresAt,
    updatedAt: new Date().toISOString(),
  };
}

async function refreshAccessToken(existing: TokenStore): Promise<TokenStore> {
  if (!existing.refreshToken) {
    throw new Error("google_refresh_token_missing");
  }
  const config = getOAuthConfig();
  const body = new URLSearchParams({
    client_id: config.clientId,
    client_secret: config.clientSecret,
    refresh_token: existing.refreshToken,
    grant_type: "refresh_token",
  });
  const response = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`google_token_refresh_failed:${response.status}:${detail}`);
  }
  const data = (await response.json()) as {
    access_token?: string;
    scope?: string;
    token_type?: string;
    expires_in?: number;
  };
  const accessToken = (data.access_token ?? "").trim();
  if (!accessToken) {
    throw new Error("google_token_refresh_missing_access_token");
  }
  const expiresAt =
    typeof data.expires_in === "number" && Number.isFinite(data.expires_in)
      ? new Date(Date.now() + data.expires_in * 1000).toISOString()
      : null;
  const refreshed: TokenStore = {
    accessToken,
    refreshToken: existing.refreshToken,
    scope: data.scope?.trim() || existing.scope,
    tokenType: data.token_type?.trim() || existing.tokenType || "Bearer",
    expiresAt,
    updatedAt: new Date().toISOString(),
  };
  await writeJsonFile(TOKEN_PATH, refreshed);
  return refreshed;
}

function tokenExpired(tokens: TokenStore): boolean {
  if (!tokens.expiresAt) {
    return false;
  }
  const expiresAt = Date.parse(tokens.expiresAt);
  if (!Number.isFinite(expiresAt)) {
    return false;
  }
  return Date.now() >= expiresAt - 60_000;
}

async function getStoredTokens(): Promise<TokenStore | null> {
  return readJsonFile<TokenStore | null>(TOKEN_PATH, null);
}

export async function saveGoogleTokenFromCode(code: string): Promise<TokenStore> {
  const token = await exchangeAuthorizationCode(code);
  await writeJsonFile(TOKEN_PATH, token);
  return token;
}

async function getValidAccessToken(): Promise<string> {
  const stored = await getStoredTokens();
  if (!stored || !stored.accessToken) {
    throw new Error("google_calendar_not_connected");
  }
  if (!tokenExpired(stored)) {
    return stored.accessToken;
  }
  const refreshed = await refreshAccessToken(stored);
  return refreshed.accessToken;
}

function normalizeIsoDate(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return new Date(parsed).toISOString();
}

function buildTodayWindow() {
  const now = new Date();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const end = new Date(now);
  end.setHours(23, 59, 59, 999);
  return {
    timeMin: start.toISOString(),
    timeMax: end.toISOString(),
  };
}

async function fetchCalendarEvents(accessToken: string, calendarId: string, timeMin: string, timeMax: string) {
  const url = new URL(
    `https://www.googleapis.com/calendar/v3/calendars/${encodeURIComponent(calendarId)}/events`
  );
  url.searchParams.set("singleEvents", "true");
  url.searchParams.set("orderBy", "startTime");
  url.searchParams.set("timeMin", timeMin);
  url.searchParams.set("timeMax", timeMax);
  url.searchParams.set("maxResults", "30");
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`google_calendar_events_failed:${response.status}:${detail}`);
  }
  return (await response.json()) as { items?: Array<Record<string, unknown>> };
}

export async function listGoogleTodayEvents(input: {
  calendarId?: string | null;
  timeMin?: string | null;
  timeMax?: string | null;
}): Promise<{
  calendarId: string;
  timeMin: string;
  timeMax: string;
  events: GoogleCalendarEvent[];
}> {
  const calendarId = input.calendarId?.trim() || process.env.GOOGLE_CALENDAR_ID?.trim() || "primary";
  const window = buildTodayWindow();
  const timeMin = normalizeIsoDate(input.timeMin ?? null) || window.timeMin;
  const timeMax = normalizeIsoDate(input.timeMax ?? null) || window.timeMax;

  let accessToken = await getValidAccessToken();
  let raw: { items?: Array<Record<string, unknown>> };
  try {
    raw = await fetchCalendarEvents(accessToken, calendarId, timeMin, timeMax);
  } catch (error) {
    if (!(error instanceof Error) || !error.message.startsWith("google_calendar_events_failed:401:")) {
      throw error;
    }
    const stored = await getStoredTokens();
    if (!stored) {
      throw error;
    }
    accessToken = (await refreshAccessToken(stored)).accessToken;
    raw = await fetchCalendarEvents(accessToken, calendarId, timeMin, timeMax);
  }

  const events: GoogleCalendarEvent[] = (raw.items ?? []).map((item) => {
    const startValue = item.start as { dateTime?: string; date?: string } | undefined;
    const endValue = item.end as { dateTime?: string; date?: string } | undefined;
    return {
      id: String(item.id ?? ""),
      summary: String(item.summary ?? "(제목 없음)"),
      status: String(item.status ?? "unknown"),
      htmlLink: item.htmlLink ? String(item.htmlLink) : null,
      location: item.location ? String(item.location) : null,
      start: startValue?.dateTime ? String(startValue.dateTime) : startValue?.date ? String(startValue.date) : null,
      end: endValue?.dateTime ? String(endValue.dateTime) : endValue?.date ? String(endValue.date) : null,
    };
  });

  return { calendarId, timeMin, timeMax, events };
}

export async function getGoogleCalendarConnectionStatus() {
  const token = await getStoredTokens();
  return {
    enabled: isGoogleCalendarEnabled(),
    readonly: isGoogleCalendarReadonly(),
    connected: Boolean(token?.accessToken),
    tokenUpdatedAt: token?.updatedAt ?? null,
    tokenExpiresAt: token?.expiresAt ?? null,
    scope: token?.scope ?? null,
  };
}

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


OAUTH_STATE_TTL_MS = 10 * 60 * 1000


def _parse_bool(raw: str | None, fallback: bool) -> bool:
    if not raw:
        return fallback
    token = raw.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return fallback


def _default_shared_root() -> Path:
    configured = (os.getenv("SHARED_ROOT_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path("/app/shared_data")


def _resolve_store_path(raw_path: str | None, file_name: str) -> Path:
    fallback = _default_shared_root() / "shared_memory" / file_name
    configured = (raw_path or "").strip()
    if not configured:
        return fallback
    path = Path(configured)
    if path.is_absolute():
        return path
    return Path.cwd() / configured


TOKEN_PATH = _resolve_store_path(os.getenv("GOOGLE_CALENDAR_TOKEN_PATH"), "google_calendar_tokens.json")
STATE_PATH = _resolve_store_path(os.getenv("GOOGLE_CALENDAR_STATE_PATH"), "google_calendar_oauth_state.json")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return fallback


def _write_json(path: Path, payload) -> None:
    _ensure_parent(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temp.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass
    temp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"missing_env:{name}")
    return value


def _oauth_config() -> dict[str, str]:
    return {
        "clientId": _required_env("GOOGLE_CALENDAR_OAUTH_CLIENT_ID"),
        "clientSecret": _required_env("GOOGLE_CALENDAR_OAUTH_CLIENT_SECRET"),
        "redirectUri": _required_env("GOOGLE_CALENDAR_OAUTH_REDIRECT_URI"),
        "scope": (os.getenv("GOOGLE_CALENDAR_OAUTH_SCOPES") or "").strip()
        or "https://www.googleapis.com/auth/calendar.readonly",
    }


def is_google_calendar_enabled() -> bool:
    return _parse_bool(os.getenv("GOOGLE_CALENDAR_ENABLED"), False)


def is_google_calendar_readonly() -> bool:
    return _parse_bool(os.getenv("GOOGLE_CALENDAR_READONLY"), True)


def create_google_oauth_state(return_to: str | None) -> dict[str, str | None]:
    record = {
        "state": str(uuid.uuid4()),
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "returnTo": (return_to or "").strip() or None,
    }
    _write_json(STATE_PATH, record)
    return record


def consume_google_oauth_state(state: str) -> dict[str, str | None] | None:
    stored = _read_json(STATE_PATH, None)
    if not isinstance(stored, dict):
        return None
    if str(stored.get("state", "")) != state:
        return None

    created_at = str(stored.get("createdAt", ""))
    try:
        created_ms = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    if now_ms - created_ms > OAUTH_STATE_TTL_MS:
        return None

    try:
        STATE_PATH.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return {
        "state": str(stored.get("state", "")),
        "createdAt": created_at,
        "returnTo": stored.get("returnTo") if isinstance(stored.get("returnTo"), str) else None,
    }


def build_google_oauth_authorization_url(state: str) -> str:
    config = _oauth_config()
    query = urlencode(
        {
            "client_id": config["clientId"],
            "redirect_uri": config["redirectUri"],
            "response_type": "code",
            "scope": config["scope"],
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"


def _post_form(url: str, payload: dict[str, str]) -> dict:
    body = urlencode(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urlopen(request, timeout=12) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _exchange_authorization_code(code: str) -> dict:
    config = _oauth_config()
    data = _post_form(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": config["clientId"],
            "client_secret": config["clientSecret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config["redirectUri"],
        },
    )
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("google_token_exchange_missing_access_token")
    expires_in = data.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + float(expires_in),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    return {
        "accessToken": access_token,
        "refreshToken": str(data.get("refresh_token") or "").strip() or None,
        "scope": str(data.get("scope") or "").strip() or None,
        "tokenType": str(data.get("token_type") or "").strip() or "Bearer",
        "expiresAt": expires_at,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _token_expired(tokens: dict) -> bool:
    expires_at = tokens.get("expiresAt")
    if not isinstance(expires_at, str) or not expires_at:
        return False
    try:
        expires_ms = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return False
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return now_ms >= expires_ms - 60_000


def _stored_tokens() -> dict | None:
    payload = _read_json(TOKEN_PATH, None)
    return payload if isinstance(payload, dict) else None


def save_google_token_from_code(code: str) -> dict:
    token = _exchange_authorization_code(code)
    _write_json(TOKEN_PATH, token)
    return token


def _refresh_access_token(existing: dict) -> dict:
    refresh_token = str(existing.get("refreshToken") or "").strip()
    if not refresh_token:
        raise RuntimeError("google_refresh_token_missing")

    config = _oauth_config()
    data = _post_form(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": config["clientId"],
            "client_secret": config["clientSecret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    access_token = str(data.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("google_token_refresh_missing_access_token")

    expires_in = data.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)):
        expires_at = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + float(expires_in),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")

    refreshed = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "scope": str(data.get("scope") or existing.get("scope") or "").strip() or None,
        "tokenType": str(data.get("token_type") or existing.get("tokenType") or "").strip() or "Bearer",
        "expiresAt": expires_at,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    _write_json(TOKEN_PATH, refreshed)
    return refreshed


def _valid_access_token() -> str:
    stored = _stored_tokens()
    if not stored:
        raise RuntimeError("google_calendar_not_connected")
    access_token = str(stored.get("accessToken") or "").strip()
    if not access_token:
        raise RuntimeError("google_calendar_not_connected")
    if not _token_expired(stored):
        return access_token
    refreshed = _refresh_access_token(stored)
    return str(refreshed.get("accessToken") or "")


def _normalize_iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _today_window() -> tuple[str, str]:
    configured_timezone = (os.getenv("GOOGLE_CALENDAR_TIMEZONE") or os.getenv("MINERVA_BRIEFING_TIMEZONE") or "Asia/Seoul").strip()
    try:
        local_tz = ZoneInfo(configured_timezone or "Asia/Seoul")
    except Exception:  # noqa: BLE001
        local_tz = ZoneInfo("Asia/Seoul")
    now = datetime.now(local_tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999000)
    return (
        start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def _fetch_calendar_events(access_token: str, calendar_id: str, time_min: str, time_max: str) -> dict:
    query = urlencode(
        {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": time_min,
            "timeMax": time_max,
            "maxResults": "30",
        }
    )
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events?{query}"
    request = Request(url, headers={"Authorization": f"Bearer {access_token}"}, method="GET")
    with urlopen(request, timeout=12) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def list_google_today_events(
    input_payload: dict[str, str | None] | None = None,
) -> dict:
    input_payload = input_payload or {}
    calendar_id = (input_payload.get("calendarId") or os.getenv("GOOGLE_CALENDAR_ID") or "primary").strip()
    default_min, default_max = _today_window()
    time_min = _normalize_iso_date(input_payload.get("timeMin")) or default_min
    time_max = _normalize_iso_date(input_payload.get("timeMax")) or default_max

    access_token = _valid_access_token()
    try:
        raw = _fetch_calendar_events(access_token, calendar_id, time_min, time_max)
    except Exception as error:  # noqa: BLE001
        message = str(error)
        if "401" not in message:
            raise
        stored = _stored_tokens()
        if not stored:
            raise
        refreshed = _refresh_access_token(stored)
        raw = _fetch_calendar_events(str(refreshed.get("accessToken") or ""), calendar_id, time_min, time_max)

    events = []
    for item in raw.get("items", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        start_value = start if isinstance(start, dict) else {}
        end_value = end if isinstance(end, dict) else {}
        events.append(
            {
                "id": str(item.get("id") or ""),
                "summary": str(item.get("summary") or "(제목 없음)"),
                "status": str(item.get("status") or "unknown"),
                "htmlLink": str(item.get("htmlLink")) if item.get("htmlLink") else None,
                "location": str(item.get("location")) if item.get("location") else None,
                "start": str(start_value.get("dateTime") or start_value.get("date") or "") or None,
                "end": str(end_value.get("dateTime") or end_value.get("date") or "") or None,
            }
        )

    return {
        "calendarId": calendar_id,
        "timeMin": time_min,
        "timeMax": time_max,
        "events": events,
    }


def get_google_calendar_connection_status() -> dict:
    token = _stored_tokens()
    token_expired = _token_expired(token) if isinstance(token, dict) else False
    refresh_available = bool(isinstance(token, dict) and str(token.get("refreshToken") or "").strip())
    return {
        "enabled": is_google_calendar_enabled(),
        "readonly": is_google_calendar_readonly(),
        "connected": bool(token and token.get("accessToken")),
        "tokenExpired": token_expired,
        "refreshAvailable": refresh_available,
        "tokenUpdatedAt": token.get("updatedAt") if isinstance(token, dict) else None,
        "tokenExpiresAt": token.get("expiresAt") if isinstance(token, dict) else None,
        "scope": token.get("scope") if isinstance(token, dict) else None,
    }

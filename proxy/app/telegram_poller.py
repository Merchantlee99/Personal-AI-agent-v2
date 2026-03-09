from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _read_int_env(name: str, default: int, minimum: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        parsed = int(float(raw))
    except ValueError:
        return default
    return max(minimum, parsed)


def _read_bool_env(name: str, fallback: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return fallback
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return fallback


def _default_shared_root() -> Path:
    configured = (os.getenv("SHARED_ROOT_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path("/app/shared_data")


def _resolve_store_path(raw_path: str | None, file_name: str) -> Path:
    configured = (raw_path or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else Path.cwd() / path
    return _default_shared_root() / "shared_memory" / file_name


STATE_PATH = _resolve_store_path(os.getenv("TELEGRAM_POLLER_STATE_PATH"), "telegram_poller_state.json")
DEAD_LETTER_PATH = _resolve_store_path(
    os.getenv("TELEGRAM_POLLER_DEAD_LETTER_PATH"), "telegram_poller_dead_letter.jsonl"
)
REPLAY_ARCHIVE_PATH = _resolve_store_path(
    os.getenv("TELEGRAM_POLLER_REPLAY_ARCHIVE_PATH"), "telegram_poller_replayed.jsonl"
)
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
WEBHOOK_SECRET = (os.getenv("TELEGRAM_WEBHOOK_SECRET") or "").strip()
POLL_TIMEOUT_SEC = _read_int_env("TELEGRAM_POLL_TIMEOUT_SEC", 30, 1)
RETRY_DELAY_SEC = _read_int_env("TELEGRAM_POLL_RETRY_DELAY_SEC", 5, 1)
DELETE_WEBHOOK_ON_START = _read_bool_env("TELEGRAM_POLLER_DELETE_WEBHOOK_ON_START", True)
INTERNAL_WEBHOOK_URL = (
    os.getenv("TELEGRAM_INTERNAL_WEBHOOK_URL") or "http://llm-proxy:8000/api/telegram/webhook"
).strip()
RETRYABLE_CLIENT_STATUSES = {408, 409, 425, 429}


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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    _ensure_parent(path)
    encoded = json.dumps(payload, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
    try:
        path.chmod(0o600)
    except Exception:  # noqa: BLE001
        pass


def _telegram_api(path: str, payload: dict | None = None, timeout: int = 40) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("missing_env:TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{path}"
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError(f"telegram_api_error:{data}")
    return data


def _forward_update(update: dict) -> tuple[str, int | None, str]:
    body = json.dumps(update).encode("utf-8")
    headers = {"content-type": "application/json"}
    if WEBHOOK_SECRET:
        headers["x-telegram-bot-api-secret-token"] = WEBHOOK_SECRET
    request = urllib.request.Request(INTERNAL_WEBHOOK_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as err:
        status = err.code
        raw = err.read().decode("utf-8", errors="ignore")
    except Exception as err:  # noqa: BLE001
        return "retry", None, str(err)

    if 200 <= status < 300:
        return "forwarded", status, raw
    if status in RETRYABLE_CLIENT_STATUSES:
        return "retry", status, raw
    if 400 <= status < 500:
        # Permanent client errors are dead-lettered and can be replayed manually.
        return "dead_letter", status, raw
    return "retry", status, raw


def _load_offset() -> int | None:
    payload = _read_json(STATE_PATH, {})
    if not isinstance(payload, dict):
        return None
    value = payload.get("offset")
    return value if isinstance(value, int) and value >= 0 else None


def _save_offset(offset: int) -> None:
    _write_json(STATE_PATH, {"offset": offset, "updatedAt": int(time.time())})


def _record_dead_letter(*, update: dict[str, Any], status: int | None, detail: str) -> None:
    update_id = update.get("update_id")
    _append_jsonl(
        DEAD_LETTER_PATH,
        {
            "recordedAt": int(time.time()),
            "updateId": update_id if isinstance(update_id, int) else None,
            "status": status,
            "detail": detail[:500],
            "update": update,
        },
    )


def _delete_webhook() -> None:
    _telegram_api("deleteWebhook", {"drop_pending_updates": False}, timeout=20)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("missing_env:TELEGRAM_BOT_TOKEN")

    if DELETE_WEBHOOK_ON_START:
        print("[telegram-poller] deleteWebhook on start")
        try:
            _delete_webhook()
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram-poller] deleteWebhook failed: {exc}")

    offset = _load_offset()
    print(f"[telegram-poller] polling started internal_webhook={INTERNAL_WEBHOOK_URL} offset={offset}")

    while True:
        try:
            payload: dict[str, object] = {"timeout": POLL_TIMEOUT_SEC}
            if offset is not None:
                payload["offset"] = offset
            response = _telegram_api("getUpdates", payload, timeout=POLL_TIMEOUT_SEC + 10)
            updates = response.get("result") or []
            if not isinstance(updates, list):
                updates = []

            if not updates:
                continue

            for item in updates:
                if not isinstance(item, dict):
                    continue
                update_id = item.get("update_id")
                if not isinstance(update_id, int):
                    continue
                outcome, status, detail = _forward_update(item)
                if outcome == "retry":
                    print(f"[telegram-poller] forward failed update_id={update_id} status={status} detail={detail[:300]}")
                    time.sleep(RETRY_DELAY_SEC)
                    break
                if outcome == "dead_letter":
                    _record_dead_letter(update=item, status=status, detail=detail)
                    print(f"[telegram-poller] dead-lettered update_id={update_id} status={status}")
                offset = update_id + 1
                _save_offset(offset)
                if outcome == "forwarded":
                    print(f"[telegram-poller] forwarded update_id={update_id} status={status}")
        except Exception as exc:  # noqa: BLE001
            print(f"[telegram-poller] polling error: {exc}")
            time.sleep(RETRY_DELAY_SEC)


if __name__ == "__main__":
    main()

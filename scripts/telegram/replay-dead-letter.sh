#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/load-env.sh

TARGET="${1:-all}"
ENV_FILE="${ENV_FILE:-.env.local}"
API_PORT="${API_PORT:-8001}"
load_runtime_env "$ENV_FILE"

get_env() {
  local key="$1"
  runtime_env_get "$key"
}

SECRET="$(get_env TELEGRAM_WEBHOOK_SECRET)"
DEAD_LETTER_PATH="${TELEGRAM_POLLER_DEAD_LETTER_PATH:-$(get_env TELEGRAM_POLLER_DEAD_LETTER_PATH)}"
REPLAY_ARCHIVE_PATH="${TELEGRAM_POLLER_REPLAY_ARCHIVE_PATH:-$(get_env TELEGRAM_POLLER_REPLAY_ARCHIVE_PATH)}"

if [[ -z "$DEAD_LETTER_PATH" ]]; then
  DEAD_LETTER_PATH="shared_data/logs/telegram_poller_dead_letter.jsonl"
fi
if [[ -z "$REPLAY_ARCHIVE_PATH" ]]; then
  REPLAY_ARCHIVE_PATH="shared_data/logs/telegram_poller_replayed.jsonl"
fi

python3 - <<'PY' "$TARGET" "$DEAD_LETTER_PATH" "$REPLAY_ARCHIVE_PATH" "$API_PORT" "$SECRET"
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


target = sys.argv[1].strip() or "all"
dead_letter_path = Path(sys.argv[2])
replay_archive_path = Path(sys.argv[3])
api_port = sys.argv[4].strip() or "8001"
secret = sys.argv[5].strip()

if not dead_letter_path.exists():
    raise SystemExit(f"[telegram-replay] dead-letter file not found: {dead_letter_path}")

records = []
for line in dead_letter_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    records.append(json.loads(line))

if not records:
    print("[telegram-replay] nothing to replay")
    raise SystemExit(0)

selected: list[dict] = []
remaining: list[dict] = []
for record in records:
    update_id = str(record.get("updateId") or "")
    if target == "all" or target == update_id:
        selected.append(record)
    else:
        remaining.append(record)

if not selected:
    print(f"[telegram-replay] no dead-letter record matched target={target}")
    raise SystemExit(0)

url = f"http://127.0.0.1:{api_port}/api/telegram/webhook"
replay_archive_path.parent.mkdir(parents=True, exist_ok=True)

attempted = 0
replayed = 0
failed = 0

for record in selected:
    update = record.get("update")
    if not isinstance(update, dict):
        record["lastReplayStatus"] = None
        record["lastReplayDetail"] = "missing update payload"
        remaining.append(record)
        failed += 1
        continue

    attempted += 1
    body = json.dumps(update, ensure_ascii=False).encode("utf-8")
    headers = {"content-type": "application/json"}
    if secret:
        headers["x-telegram-bot-api-secret-token"] = secret
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")

    status = None
    detail = ""
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            detail = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as err:
        status = err.code
        detail = err.read().decode("utf-8", errors="ignore")
    except Exception as err:  # noqa: BLE001
        detail = str(err)

    if status is not None and 200 <= status < 300:
        replay_record = {
            **record,
            "replayedAt": int(time.time()),
            "replayStatus": status,
        }
        with replay_archive_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(replay_record, ensure_ascii=False) + "\n")
        replayed += 1
        continue

    record["lastReplayStatus"] = status
    record["lastReplayDetail"] = detail[:500]
    remaining.append(record)
    failed += 1

output = ""
if remaining:
    output = "\n".join(json.dumps(item, ensure_ascii=False) for item in remaining) + "\n"
dead_letter_path.write_text(output, encoding="utf-8")

print(
    json.dumps(
        {
            "attempted": attempted,
            "replayed": replayed,
            "failed": failed,
            "deadLetterPath": str(dead_letter_path),
            "replayArchivePath": str(replay_archive_path),
        },
        ensure_ascii=False,
    )
)

if failed:
    raise SystemExit(1)
PY

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

LOG_DIR="shared_data/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
REPORT_FILE="${LOG_DIR}/morning-briefing-report-${TIMESTAMP}.json"
LATEST_LINK="${LOG_DIR}/morning-briefing-report.latest.json"

bash scripts/verify/report-morning-briefing-observations.sh > "$REPORT_FILE"
ln -sf "$(basename "$REPORT_FILE")" "$LATEST_LINK"

REPORT_FILE="$REPORT_FILE" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

report_path = Path(os.environ["REPORT_FILE"])
report = json.loads(report_path.read_text(encoding="utf-8"))
tz = timezone(timedelta(hours=9))
today = datetime.now(tz).date().isoformat()
daily = report.get("daily") or []
today_row = next((row for row in daily if row.get("day") == today), None)

result = {
    "status": "pass",
    "today": today,
    "observedEvents": report.get("observedEvents", 0),
    "successfulDays": report.get("successfulDays", 0),
    "reportFile": str(report_path),
}

if today_row is None:
    result["status"] = "fail"
    result["reason"] = "no_observation_for_today"
elif int(today_row.get("telegramSent", 0)) < 1:
    result["status"] = "fail"
    result["reason"] = "telegram_not_sent"
else:
    result["reason"] = "ok"
    result["todayRow"] = today_row

print(json.dumps(result, ensure_ascii=False))
if result["status"] != "pass":
    raise SystemExit(2)
PY

echo "[morning-observe] PASS"

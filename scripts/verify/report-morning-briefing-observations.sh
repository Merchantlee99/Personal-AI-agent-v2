#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

WINDOW_DAYS="${WINDOW_DAYS:-7}"
OBSERVATION_FILE="shared_data/logs/morning_briefing_observations.jsonl"

if [[ ! -f "$OBSERVATION_FILE" ]]; then
  echo "[morning-report] no observation file: $OBSERVATION_FILE"
  exit 0
fi

python3 - <<'PY' "$OBSERVATION_FILE" "$WINDOW_DAYS"
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

path = Path(sys.argv[1])
window_days = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

rows = []
for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        continue
    observed = payload.get("observedAt")
    if not observed:
        continue
    try:
        dt = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
    except ValueError:
        continue
    if dt < cutoff:
        continue
    rows.append(payload)

rows.sort(key=lambda item: item.get("observedAt", ""))
by_day = defaultdict(list)
for row in rows:
    day_key = str(row.get("observedAt", ""))[:10]
    by_day[day_key].append(row)

daily = []
success_days = 0
for day_key in sorted(by_day.keys()):
    entries = by_day[day_key]
    sent = sum(1 for item in entries if bool((item.get("telegram") or {}).get("sent")))
    decisions = sorted({str(item.get("decision") or "") for item in entries if item.get("decision")})
    attached = sum(1 for item in entries if item.get("calendarBriefingAttached") is True)
    if sent > 0:
        success_days += 1
    daily.append(
        {
            "day": day_key,
            "events": len(entries),
            "telegramSent": sent,
            "calendarAttached": attached,
            "decisions": decisions,
        }
    )

report = {
    "windowDays": window_days,
    "observedEvents": len(rows),
    "observedDays": len(daily),
    "successfulDays": success_days,
    "successRateObservedDays": round((success_days / len(daily)) * 100, 1) if daily else 0.0,
    "latestObservation": rows[-1] if rows else None,
    "daily": daily,
}
print(json.dumps(report, ensure_ascii=False, indent=2))
PY

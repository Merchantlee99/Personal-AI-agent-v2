#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

RETENTION_DAYS="${N8N_EXECUTION_RETENTION_DAYS:-14}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="shared_data/workflows/backups/n8n-execution-cleanup-${TIMESTAMP}"
LATEST_SUMMARY="shared_data/logs/n8n-execution-cleanup.latest.json"
TMP_DB="$(mktemp /tmp/n8n-execution-cleanup.XXXXXX.sqlite)"
trap 'rm -f "$TMP_DB"' EXIT

wait_for_n8n() {
  for _ in $(seq 1 30); do
    if docker exec nanoclaw-n8n n8n list:workflow >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[n8n-execution-cleanup] FAIL n8n CLI not ready" >&2
  return 1
}

volume_name() {
  docker volume ls --format '{{.Name}}' | awk '/_n8n_data$/ {print; exit}'
}

mkdir -p "$BACKUP_DIR" "shared_data/logs"

echo "[n8n-execution-cleanup] ensure n8n up"
compose_cmd up -d n8n >/dev/null
wait_for_n8n

echo "[n8n-execution-cleanup] backup live sqlite"
docker cp nanoclaw-n8n:/home/node/.n8n/database.sqlite "$BACKUP_DIR/database.sqlite.before"

echo "[n8n-execution-cleanup] stop n8n for sqlite maintenance"
compose_cmd stop n8n >/dev/null
docker cp nanoclaw-n8n:/home/node/.n8n/database.sqlite "$TMP_DB"

python3 - <<'PY' "$TMP_DB" "$RETENTION_DAYS" "$BACKUP_DIR/cleanup-summary.json" "$LATEST_SUMMARY"
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

db_path = Path(sys.argv[1])
retention_days = int(sys.argv[2])
summary_path = Path(sys.argv[3])
latest_path = Path(sys.argv[4])
cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")

con = sqlite3.connect(db_path)
con.execute("PRAGMA foreign_keys = ON")
con.row_factory = sqlite3.Row

counts_before = {
    "executionCount": con.execute("SELECT COUNT(*) FROM execution_entity").fetchone()[0],
    "executionDataCount": con.execute("SELECT COUNT(*) FROM execution_data").fetchone()[0],
    "executionMetadataCount": con.execute("SELECT COUNT(*) FROM execution_metadata").fetchone()[0],
    "orphanExecutionData": con.execute(
        "SELECT COUNT(*) FROM execution_data WHERE executionId NOT IN (SELECT id FROM execution_entity)"
    ).fetchone()[0],
    "missingExecutionData": con.execute(
        "SELECT COUNT(*) FROM execution_entity WHERE id NOT IN (SELECT executionId FROM execution_data)"
    ).fetchone()[0],
    "orphanExecutionMetadata": con.execute(
        "SELECT COUNT(*) FROM execution_metadata WHERE executionId NOT IN (SELECT id FROM execution_entity)"
    ).fetchone()[0],
}

deleted_orphan_data = con.execute(
    "DELETE FROM execution_data WHERE executionId NOT IN (SELECT id FROM execution_entity)"
).rowcount
deleted_orphan_metadata = con.execute(
    "DELETE FROM execution_metadata WHERE executionId NOT IN (SELECT id FROM execution_entity)"
).rowcount
deleted_missing_data = con.execute(
    """
    DELETE FROM execution_entity
    WHERE id NOT IN (SELECT executionId FROM execution_data)
    """
).rowcount
pruned_old_finished = con.execute(
    """
    DELETE FROM execution_entity
    WHERE finished = 1
      AND COALESCE(stoppedAt, createdAt, startedAt) < ?
    """,
    (cutoff_iso,),
).rowcount

con.commit()
con.execute("VACUUM")
counts_after = {
    "executionCount": con.execute("SELECT COUNT(*) FROM execution_entity").fetchone()[0],
    "executionDataCount": con.execute("SELECT COUNT(*) FROM execution_data").fetchone()[0],
    "executionMetadataCount": con.execute("SELECT COUNT(*) FROM execution_metadata").fetchone()[0],
    "orphanExecutionData": con.execute(
        "SELECT COUNT(*) FROM execution_data WHERE executionId NOT IN (SELECT id FROM execution_entity)"
    ).fetchone()[0],
    "missingExecutionData": con.execute(
        "SELECT COUNT(*) FROM execution_entity WHERE id NOT IN (SELECT executionId FROM execution_data)"
    ).fetchone()[0],
    "orphanExecutionMetadata": con.execute(
        "SELECT COUNT(*) FROM execution_metadata WHERE executionId NOT IN (SELECT id FROM execution_entity)"
    ).fetchone()[0],
}
con.close()

summary = {
    "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "retentionDays": retention_days,
    "cutoffIso": cutoff_iso,
    "countsBefore": counts_before,
    "deleted": {
        "orphanExecutionData": deleted_orphan_data,
        "orphanExecutionMetadata": deleted_orphan_metadata,
        "missingExecutionDataRows": deleted_missing_data,
        "oldFinishedExecutions": pruned_old_finished,
    },
    "countsAfter": counts_after,
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
latest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=True))
PY

docker cp "$TMP_DB" nanoclaw-n8n:/home/node/.n8n/database.sqlite
cp "$TMP_DB" "$BACKUP_DIR/database.sqlite.after"

N8N_VOLUME_NAME="$(volume_name)"
if [[ -z "$N8N_VOLUME_NAME" ]]; then
  echo "[n8n-execution-cleanup] FAIL unable to locate n8n data volume" >&2
  exit 1
fi

docker run --rm --entrypoint sh -u 0 -v "${N8N_VOLUME_NAME}:/home/node/.n8n" n8nio/n8n:1.95.3 -lc \
  'chown -R 1000:1000 /home/node/.n8n && chmod 700 /home/node/.n8n && chmod 600 /home/node/.n8n/database.sqlite* 2>/dev/null || true' \
  >/dev/null

echo "[n8n-execution-cleanup] start n8n"
compose_cmd up -d n8n >/dev/null
wait_for_n8n
sleep 3

if docker logs --since 30s nanoclaw-n8n 2>&1 | grep -q "Found executions without executionData"; then
  echo "[n8n-execution-cleanup] FAIL startup warning persists: Found executions without executionData" >&2
  exit 1
fi

echo "[n8n-execution-cleanup] PASS"

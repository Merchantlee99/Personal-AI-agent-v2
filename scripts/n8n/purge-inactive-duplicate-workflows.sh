#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="shared_data/workflows/backups/n8n-purge-${TIMESTAMP}"
TMP_DB="$(mktemp /tmp/n8n-dedupe.XXXXXX.sqlite)"
trap 'rm -f "$TMP_DB"' EXIT

WORKFLOW_NAME="${N8N_WORKFLOW_NAME:-}"
KEEP_WORKFLOW_ID="${N8N_KEEP_WORKFLOW_ID:-}"

wait_for_n8n_cli() {
  for _ in $(seq 1 30); do
    if docker exec nanoclaw-n8n n8n list:workflow >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[n8n-purge] FAIL n8n CLI not ready" >&2
  return 1
}

volume_name() {
  docker volume ls --format '{{.Name}}' | awk '/_n8n_data$/ {print; exit}'
}

list_workflow_retry() {
  local active_flag="$1"
  local attempts=0
  local output=""
  while [[ "$attempts" -lt 30 ]]; do
    if output="$(docker exec nanoclaw-n8n n8n list:workflow --active="${active_flag}" 2>/dev/null)"; then
      printf '%s' "$output"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 2
  done
  return 1
}

echo "[n8n-purge] ensure n8n up"
compose_cmd up -d n8n >/dev/null
wait_for_n8n_cli

mkdir -p "$BACKUP_DIR"
REMOTE_EXPORT_DIR="/data/${BACKUP_DIR}/exported"

echo "[n8n-purge] backup current workflow export -> $BACKUP_DIR"
docker exec nanoclaw-n8n sh -lc "rm -rf \"$REMOTE_EXPORT_DIR\" && mkdir -p \"$REMOTE_EXPORT_DIR\" && n8n export:workflow --backup --output=\"$REMOTE_EXPORT_DIR\" >/dev/null"
docker cp nanoclaw-n8n:/home/node/.n8n/database.sqlite "$BACKUP_DIR/database.sqlite.before"

echo "[n8n-purge] stop n8n to edit sqlite safely"
compose_cmd stop n8n >/dev/null
docker cp nanoclaw-n8n:/home/node/.n8n/database.sqlite "$TMP_DB"

python3 - <<'PY' "$TMP_DB" "$WORKFLOW_NAME" "$KEEP_WORKFLOW_ID" "$BACKUP_DIR/purge-summary.json"
import json
import sqlite3
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
workflow_name = sys.argv[2]
keep_workflow_id = sys.argv[3]
summary_path = Path(sys.argv[4])

managed_names = [
    "NanoClaw v2 Smoke Webhook",
    "Hermes Daily Briefing Workflow",
    "Hermes Web Search Workflow",
]
target_names = [workflow_name] if workflow_name else managed_names

con = sqlite3.connect(db_path)
con.execute("PRAGMA foreign_keys = ON")
con.row_factory = sqlite3.Row

rows = con.execute(
    """
    SELECT id, name, active
    FROM workflow_entity
    WHERE name IN ({})
    ORDER BY name, active DESC, id
    """.format(",".join("?" for _ in target_names)),
    target_names,
).fetchall()

by_name = {}
for row in rows:
    by_name.setdefault(row["name"], []).append(dict(row))

to_delete = []
skipped = []

for name, items in by_name.items():
    active = [item for item in items if item["active"] == 1]
    inactive = [item for item in items if item["active"] == 0]
    if len(active) != 1 or not inactive:
      skipped.append(
          {
              "name": name,
              "reason": "requires exactly one active workflow and at least one inactive duplicate",
              "activeCount": len(active),
              "inactiveCount": len(inactive),
          }
      )
      continue
    keep_id = keep_workflow_id if keep_workflow_id and any(item["id"] == keep_workflow_id for item in items) else active[0]["id"]
    to_delete.extend(item["id"] for item in inactive if item["id"] != keep_id)

deleted = []
for workflow_id in to_delete:
    con.execute("DELETE FROM webhook_entity WHERE workflowId = ?", (workflow_id,))
    con.execute("DELETE FROM workflow_entity WHERE id = ?", (workflow_id,))
    deleted.append(workflow_id)

con.commit()

remaining = [
    dict(row)
    for row in con.execute(
        """
        SELECT id, name, active
        FROM workflow_entity
        WHERE name IN ({})
        ORDER BY name, active DESC, id
        """.format(",".join("?" for _ in target_names)),
        target_names,
    ).fetchall()
]
con.close()

summary = {
    "targetNames": target_names,
    "deletedIds": deleted,
    "deletedCount": len(deleted),
    "skipped": skipped,
    "remaining": remaining,
}
summary_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=True))
PY

docker cp "$TMP_DB" nanoclaw-n8n:/home/node/.n8n/database.sqlite
cp "$TMP_DB" "$BACKUP_DIR/database.sqlite.after"

N8N_VOLUME_NAME="$(volume_name)"
if [[ -z "$N8N_VOLUME_NAME" ]]; then
  echo "[n8n-purge] FAIL unable to locate n8n data volume" >&2
  exit 1
fi
docker run --rm --entrypoint sh -u 0 -v "${N8N_VOLUME_NAME}:/home/node/.n8n" n8nio/n8n:1.95.3 -lc \
  'chown -R 1000:1000 /home/node/.n8n && chmod 700 /home/node/.n8n && chmod 600 /home/node/.n8n/database.sqlite* 2>/dev/null || true' \
  >/dev/null

echo "[n8n-purge] start n8n"
compose_cmd up -d n8n >/dev/null
wait_for_n8n_cli

ACTIVE_WORKFLOWS="$(list_workflow_retry true)"
INACTIVE_WORKFLOWS="$(list_workflow_retry false || true)"

python3 - <<'PY' "$WORKFLOW_NAME" "$ACTIVE_WORKFLOWS" "$INACTIVE_WORKFLOWS"
import sys

workflow_name = sys.argv[1]
active_out = sys.argv[2]
inactive_out = sys.argv[3]
managed_names = [workflow_name] if workflow_name else [
    "NanoClaw v2 Smoke Webhook",
    "Hermes Daily Briefing Workflow",
    "Hermes Web Search Workflow",
]
for name in managed_names:
    active_matches = [line for line in active_out.splitlines() if line.endswith(f"|{name}")]
    if len(active_matches) != 1:
        raise SystemExit(f"[n8n-purge] FAIL {name!r} active singleton mismatch: {len(active_matches)}")
    inactive_matches = [line for line in inactive_out.splitlines() if line.endswith(f"|{name}")]
    if inactive_matches:
        raise SystemExit(f"[n8n-purge] FAIL {name!r} still has inactive duplicates: {inactive_matches}")
print("[n8n-purge] PASS")
PY

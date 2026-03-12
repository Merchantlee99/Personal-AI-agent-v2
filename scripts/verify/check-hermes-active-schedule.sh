#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[hermes-active] verify active schedule expressions"
docker exec nanoclaw-n8n sh -lc 'id=$(n8n list:workflow | awk -F"|" '\''$2 == "Hermes Daily Briefing Workflow" {id=$1} END{print id}'\''); test -n "$id" && n8n export:workflow --id="$id" --output=/tmp/hermes-active-readonly.json >/dev/null && cat /tmp/hermes-active-readonly.json' >/tmp/hermes-active-readonly.json

python3 - <<'PY'
import json
from pathlib import Path

workflow = json.loads(Path("/tmp/hermes-active-readonly.json").read_text(encoding="utf-8"))[0]
expected = {
    "Schedule P0 Daily (KST 09:00)": "0 9 * * *",
    "Schedule P1 Every2Days (KST 09:10)": "10 9 */2 * *",
    "Schedule P2 Every3Days (KST 09:20)": "20 9 */3 * *",
}
found = {}
for node in workflow.get("nodes", []):
    if "scheduleTrigger" not in str(node.get("type", "")):
        continue
    found[node.get("name")] = str(node.get("parameters", {}).get("rule", {}).get("interval", [{}])[0].get("expression", ""))
for name, expr in expected.items():
    if found.get(name) != expr:
        raise SystemExit(f"[hermes-active] active workflow cron mismatch: {name} -> {found.get(name)!r} expected {expr!r}")
print("[hermes-active] active schedule expressions verified")
PY

echo "[hermes-active] PASS"

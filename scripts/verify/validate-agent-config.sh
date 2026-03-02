#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/config/agents.json}"

python3 - "$CONFIG_PATH" <<'PY'
import json
import sys
from pathlib import Path

expected_canonical = {"minerva", "clio", "hermes"}

path = Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(f"[config] missing file: {path}")

try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"[config] invalid json: {exc}") from exc

canonical_ids = payload.get("canonical_ids")
if not isinstance(canonical_ids, list):
    raise SystemExit("[config] canonical_ids must be a list")

normalized_canonical = [str(item).strip().lower() for item in canonical_ids if str(item).strip()]
if len(normalized_canonical) != len(set(normalized_canonical)):
    raise SystemExit("[config] canonical_ids must not contain duplicates")

if set(normalized_canonical) != expected_canonical:
    raise SystemExit(
        f"[config] canonical_ids must be exactly {sorted(expected_canonical)}; got {sorted(set(normalized_canonical))}"
    )

aliases = payload.get("aliases")
if not isinstance(aliases, dict):
    raise SystemExit("[config] aliases must be an object")
if aliases:
    raise SystemExit("[config] aliases must be empty; use canonical ids only")

agents = payload.get("agents")
if not isinstance(agents, dict):
    raise SystemExit("[config] agents must be an object")

for canonical in expected_canonical:
    entry = agents.get(canonical)
    if not isinstance(entry, dict):
        raise SystemExit(f"[config] agents.{canonical} must be an object")
    display_name = str(entry.get("display_name", "")).strip()
    role = str(entry.get("role", "")).strip()
    if not display_name:
        raise SystemExit(f"[config] agents.{canonical}.display_name is required")
    if not role:
        raise SystemExit(f"[config] agents.{canonical}.role is required")

print(f"[config] OK: {path}")
PY

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"
source scripts/runtime/compose-env.sh

echo "[clio-knowledge] ensure nanoclaw-agent is running"
compose_cmd up -d nanoclaw-agent >/dev/null

count_queue_items() {
  python3 - <<'PY'
import json
from pathlib import Path
path = Path("shared_data/shared_memory/clio_claim_review_queue.json")
if not path.exists():
    print(0)
else:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    print(len(items) if isinstance(items, list) else 0)
PY
}

latest_queue_item() {
  python3 - <<'PY'
import json
from pathlib import Path
path = Path("shared_data/shared_memory/clio_claim_review_queue.json")
if not path.exists():
    print("")
else:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        print("")
    else:
        print(json.dumps(items[-1], ensure_ascii=False))
PY
}

RUN_ID="$(date +%Y%m%d-%H%M%S)-$RANDOM"
INPUT_FILE="shared_data/inbox/${RUN_ID}-clio-knowledge-runtime.json"
BEFORE_COUNT="$(count_queue_items)"

cat > "$INPUT_FILE" <<JSON
{
  "agent_id": "clio",
  "source": "integration-test",
  "message": "[title] PM은 측정 가능한 학습 루프를 설계해야 한다\n[topic] pm-learning-loop-${RUN_ID}\n\n핵심 주장: PM은 측정 가능한 학습 루프를 설계해야 한다.\n왜 이렇게 생각하는가: 런칭 이후 학습은 정량 지표와 연결되어야 반복 개선이 가능하기 때문이다."
}
JSON

echo "[clio-knowledge] wait for review queue update"
AFTER_COUNT="$BEFORE_COUNT"
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  AFTER_COUNT="$(count_queue_items)"
  if [[ "$AFTER_COUNT" -gt "$BEFORE_COUNT" ]]; then
    break
  fi
  sleep 1
done

if [[ "$AFTER_COUNT" -le "$BEFORE_COUNT" ]]; then
  echo "[clio-knowledge] claim review queue did not increase" >&2
  exit 1
fi

LATEST_ITEM="$(latest_queue_item)"
if [[ -z "$LATEST_ITEM" ]]; then
  echo "[clio-knowledge] latest queue item missing" >&2
  exit 1
fi

echo "[clio-knowledge] latest queue item: $LATEST_ITEM"

python3 - <<'PY' "$LATEST_ITEM"
import json
import sys

item = json.loads(sys.argv[1])
assert item.get("status") == "pending_user_review", "review status mismatch"
assert item.get("title"), "title missing"
assert item.get("vaultFile"), "vaultFile missing"
print("[clio-knowledge] queue payload validated")
PY

python3 - <<'PY'
import json
from pathlib import Path

path = Path("shared_data/shared_memory/clio_knowledge_memory.json")
assert path.exists(), "clio_knowledge_memory.json missing"
payload = json.loads(path.read_text(encoding="utf-8"))
recent = payload.get("recentNotes", [])
assert isinstance(recent, list) and recent, "recentNotes missing"
latest = recent[-1]
assert latest.get("type") == "knowledge", f"latest note type mismatch: {latest.get('type')}"
assert latest.get("claimReviewRequired") is True, "claimReviewRequired mismatch"
print("[clio-knowledge] clio knowledge memory validated")
PY

if [[ "${KEEP_ARTIFACTS:-false}" != "true" ]]; then
  python3 - <<'PY' "$RUN_ID"
import json
import sys
from pathlib import Path

run_id = sys.argv[1]
shared_root = Path("shared_data")
queue_path = shared_root / "shared_memory" / "clio_claim_review_queue.json"
memory_path = shared_root / "shared_memory" / "clio_knowledge_memory.json"
queue = json.loads(queue_path.read_text(encoding="utf-8"))
target_item = next((item for item in queue.get("items", []) if run_id in str(item.get("topicKey", ""))), None)
vault_file = str(target_item.get("vaultFile") or "") if target_item else ""
queue["items"] = [item for item in queue.get("items", []) if run_id not in str(item.get("topicKey", ""))]
queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
memory = json.loads(memory_path.read_text(encoding="utf-8"))
memory["recentNotes"] = [
    note for note in memory.get("recentNotes", [])
    if run_id not in str(note.get("claimReviewId", "")) and run_id not in str(note.get("vaultFile", ""))
]
memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
input_path = shared_root / "inbox" / f"{run_id}-clio-knowledge-runtime.json"
if input_path.exists():
    input_path.unlink()
if vault_file:
    note_path = shared_root / vault_file
    if note_path.exists():
        note_path.unlink()
print("[clio-knowledge] cleanup ok")
PY
fi

echo "[clio-knowledge] PASS"

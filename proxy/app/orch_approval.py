from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


def approval_is_pending(status: str) -> bool:
    return status in {"pending_stage1", "pending_stage2"}


def default_approval_store() -> dict[str, Any]:
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "approvals": {},
    }


def prune_approval_store(store: dict[str, Any], now: datetime, *, retention_hours: int) -> dict[str, Any]:
    approvals = store.get("approvals")
    if not isinstance(approvals, dict):
        store["approvals"] = {}
        approvals = store["approvals"]
    dirty = False
    retention_ms = retention_hours * 3600 * 1000
    now_ms = int(now.timestamp() * 1000)

    for approval_id, approval in list(approvals.items()):
        if not isinstance(approval, dict):
            del approvals[approval_id]
            dirty = True
            continue
        expires_at = str(approval.get("expiresAt", ""))
        status = str(approval.get("status", ""))
        try:
            expires_ms = int(datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            expires_ms = 0

        if approval_is_pending(status) and expires_ms and expires_ms <= now_ms:
            approval["status"] = "expired"
            history = approval.get("history")
            if not isinstance(history, list):
                history = []
            history.append({"at": now.isoformat().replace("+00:00", "Z"), "type": "expired"})
            approval["history"] = history
            approvals[approval_id] = approval
            dirty = True
            continue

        requested_at = str(approval.get("requestedAt", ""))
        try:
            requested_ms = int(datetime.fromisoformat(requested_at.replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            requested_ms = 0
        if not approval_is_pending(status) and requested_ms and now_ms - requested_ms > retention_ms:
            del approvals[approval_id]
            dirty = True

    if dirty:
        store["updatedAt"] = now.isoformat().replace("+00:00", "Z")
    return store


def read_approval_store(
    *,
    approval_queue_file: Path,
    read_json_file: Callable[[Path, Any], Any],
    retention_hours: int,
) -> dict[str, Any]:
    raw = read_json_file(approval_queue_file, default_approval_store())
    if not isinstance(raw, dict):
        raw = default_approval_store()
    if not isinstance(raw.get("approvals"), dict):
        raw["approvals"] = {}
    return prune_approval_store(raw, datetime.now(timezone.utc), retention_hours=retention_hours)


def write_approval_store(
    *,
    approval_queue_file: Path,
    store: dict[str, Any],
    write_json_file: Callable[[Path, Any], None],
) -> None:
    store["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_json_file(approval_queue_file, store)


def create_approval_request(
    *,
    approval_queue_file: Path,
    action: str,
    event_id: str,
    event_title: str,
    topic_key: str,
    chat_id: str,
    requested_by_user_id: str,
    payload: dict[str, Any] | None,
    approval_ttl_sec: int,
    required_steps: int,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    normalize_approval_request_artifact: Callable[[dict[str, Any]], dict[str, Any]],
    retention_hours: int,
) -> dict[str, Any]:
    store = read_approval_store(
        approval_queue_file=approval_queue_file,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    approvals = store.get("approvals", {})

    for existing in approvals.values():
        if not isinstance(existing, dict):
            continue
        if (
            existing.get("action") == action
            and existing.get("eventId") == event_id
            and existing.get("chatId") == chat_id
            and existing.get("requestedByUserId") == requested_by_user_id
            and approval_is_pending(str(existing.get("status")))
        ):
            return {"approval": existing, "reused": True}

    now = datetime.now(timezone.utc)
    approval = {
        "id": secrets.token_hex(6),
        "action": action,
        "eventId": event_id,
        "eventTitle": event_title,
        "topicKey": topic_key,
        "chatId": chat_id,
        "requestedByUserId": requested_by_user_id,
        "requestedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(seconds=approval_ttl_sec)).isoformat().replace("+00:00", "Z"),
        "requiredSteps": required_steps,
        "status": "pending_stage1",
        "payload": payload if isinstance(payload, dict) and payload else None,
        "history": [
            {
                "at": now.isoformat().replace("+00:00", "Z"),
                "type": "created",
                "actorUserId": requested_by_user_id,
            }
        ],
    }
    approval = normalize_approval_request_artifact(approval)
    approvals[approval["id"]] = approval
    store["approvals"] = approvals
    write_approval_store(
        approval_queue_file=approval_queue_file,
        store=store,
        write_json_file=write_json_file,
    )
    return {"approval": approval, "reused": False}


def get_approval_request(
    *,
    approval_queue_file: Path,
    approval_id: str,
    read_json_file: Callable[[Path, Any], Any],
    retention_hours: int,
) -> dict[str, Any] | None:
    store = read_approval_store(
        approval_queue_file=approval_queue_file,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    approvals = store.get("approvals", {})
    value = approvals.get(approval_id) if isinstance(approvals, dict) else None
    return value if isinstance(value, dict) else None


def update_approval_status(
    *,
    approval_queue_file: Path,
    approval_id: str,
    status: str,
    history_type: str,
    actor_user_id: str,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    retention_hours: int,
) -> dict[str, Any] | None:
    store = read_approval_store(
        approval_queue_file=approval_queue_file,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    approvals = store.get("approvals", {})
    if not isinstance(approvals, dict):
        return None
    found = approvals.get(approval_id)
    if not isinstance(found, dict):
        return None
    if found.get("status") == status:
        return found
    found["status"] = status
    history = found.get("history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "type": history_type,
            "actorUserId": actor_user_id,
        }
    )
    found["history"] = history
    approvals[approval_id] = found
    store["approvals"] = approvals
    write_approval_store(
        approval_queue_file=approval_queue_file,
        store=store,
        write_json_file=write_json_file,
    )
    return found


def approve_stage_one(
    *,
    approval_queue_file: Path,
    approval_id: str,
    actor_user_id: str,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    retention_hours: int,
) -> dict[str, Any] | None:
    current = get_approval_request(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    if not current:
        return None
    if current.get("status") != "pending_stage1":
        return current
    return update_approval_status(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        status="pending_stage2",
        history_type="stage1_approved",
        actor_user_id=actor_user_id,
        read_json_file=read_json_file,
        write_json_file=write_json_file,
        retention_hours=retention_hours,
    )


def reject_approval_request(
    *,
    approval_queue_file: Path,
    approval_id: str,
    actor_user_id: str,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    retention_hours: int,
) -> dict[str, Any] | None:
    current = get_approval_request(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    if not current:
        return None
    if current.get("status") == "rejected":
        return current
    return update_approval_status(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        status="rejected",
        history_type="rejected",
        actor_user_id=actor_user_id,
        read_json_file=read_json_file,
        write_json_file=write_json_file,
        retention_hours=retention_hours,
    )


def mark_approval_executed(
    *,
    approval_queue_file: Path,
    approval_id: str,
    actor_user_id: str,
    read_json_file: Callable[[Path, Any], Any],
    write_json_file: Callable[[Path, Any], None],
    retention_hours: int,
) -> dict[str, Any] | None:
    current = get_approval_request(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    if not current:
        return None
    if current.get("status") == "executed":
        return current
    return update_approval_status(
        approval_queue_file=approval_queue_file,
        approval_id=approval_id,
        status="executed",
        history_type="executed",
        actor_user_id=actor_user_id,
        read_json_file=read_json_file,
        write_json_file=write_json_file,
        retention_hours=retention_hours,
    )


def list_pending_approvals(
    *,
    approval_queue_file: Path,
    limit: int,
    read_json_file: Callable[[Path, Any], Any],
    retention_hours: int,
) -> list[dict[str, Any]]:
    store = read_approval_store(
        approval_queue_file=approval_queue_file,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    approvals = store.get("approvals", {})
    if not isinstance(approvals, dict):
        return []
    pending = [
        value
        for value in approvals.values()
        if isinstance(value, dict) and approval_is_pending(str(value.get("status")))
    ]
    pending.sort(key=lambda item: str(item.get("requestedAt", "")), reverse=True)
    return pending[: max(1, limit)]


def get_approval_queue_stats(
    *,
    approval_queue_file: Path,
    read_json_file: Callable[[Path, Any], Any],
    retention_hours: int,
) -> dict[str, Any]:
    store = read_approval_store(
        approval_queue_file=approval_queue_file,
        read_json_file=read_json_file,
        retention_hours=retention_hours,
    )
    approvals = [item for item in store.get("approvals", {}).values() if isinstance(item, dict)]
    stats = {
        "pending": 0,
        "pendingStage1": 0,
        "pendingStage2": 0,
        "executed": 0,
        "rejected": 0,
        "expired": 0,
        "total": len(approvals),
        "updatedAt": store.get("updatedAt"),
    }
    for approval in approvals:
        status = str(approval.get("status"))
        if status == "pending_stage1":
            stats["pending"] += 1
            stats["pendingStage1"] += 1
        elif status == "pending_stage2":
            stats["pending"] += 1
            stats["pendingStage2"] += 1
        elif status == "executed":
            stats["executed"] += 1
        elif status == "rejected":
            stats["rejected"] += 1
        elif status == "expired":
            stats["expired"] += 1
    return stats

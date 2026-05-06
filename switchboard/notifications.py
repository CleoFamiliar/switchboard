"""Notification queue — append-only JSONL for skill results needing attention.

Stores notifications at ~/.switchboard/notifications.jsonl. Each entry tracks
a skill result that needs operator review (MEDIUM/LOW confidence detections).
"""

import datetime
import json
import uuid
from pathlib import Path
from typing import Optional

NOTIFICATIONS_PATH = Path.home() / ".switchboard" / "notifications.jsonl"


def _ensure_dir() -> None:
    NOTIFICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_notification(
    skill: str,
    confidence: str,
    message: str,
    jack_ids: list[str],
    path: Optional[Path] = None,
) -> str:
    """Append a notification entry. Returns the notification ID."""
    path = path or NOTIFICATIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": datetime.datetime.now().isoformat(),
        "skill": skill,
        "confidence": confidence,
        "message": message,
        "jack_ids": jack_ids,
        "status": "pending",
    }

    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return entry["id"]


def list_notifications(
    status: Optional[str] = None,
    path: Optional[Path] = None,
) -> list[dict]:
    """Read all notifications, optionally filtering by status."""
    path = path or NOTIFICATIONS_PATH
    if not path.exists():
        return []

    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if status is None or entry.get("status") == status:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries


def ack_notification(
    notification_id: str,
    path: Optional[Path] = None,
) -> bool:
    """Mark a notification as acked. Rewrites the file. Returns True if found."""
    path = path or NOTIFICATIONS_PATH
    if not path.exists():
        return False

    lines = []
    found = False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("id") == notification_id:
                    entry["status"] = "acked"
                    entry["acked_at"] = datetime.datetime.now().isoformat()
                    found = True
                lines.append(json.dumps(entry))
            except json.JSONDecodeError:
                lines.append(line)

    if found:
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")

    return found

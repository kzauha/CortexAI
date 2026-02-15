"""
Lightweight snapshot cache.
Saves last-known-good results from Tally as JSON files.
Total size: ~100KB even for large companies.
No database needed.
"""

import json
import os
from datetime import datetime

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)


def save(key: str, data: str):
    """Save a tool result with timestamp."""
    snapshot = {
        "data": data,
        "saved_at": datetime.now().isoformat(),
        "timestamp_human": datetime.now().strftime("%d %b %Y, %I:%M %p"),
    }
    path = os.path.join(SNAPSHOT_DIR, f"{key}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)


def load(key: str) -> dict | None:
    """Load a snapshot. Returns {"data": ..., "saved_at": ..., "timestamp_human": ...} or None."""
    path = os.path.join(SNAPSHOT_DIR, f"{key}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def age_str(key: str) -> str:
    """Human-readable age of a snapshot."""
    snap = load(key)
    if not snap:
        return "no cached data"
    saved = datetime.fromisoformat(snap["saved_at"])
    diff = datetime.now() - saved
    
    minutes = int(diff.total_seconds() / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days} days ago"
"""Collect the read-only universe records page for the dashboard.

``page=records`` lists four blocks (buildings, research, ships, defense) with
the universe-wide record, the account's personal best and the record holders.
The daemon owns the game session and writes ``state/records.json``; the TUI
only reads that file (ADR-011/ADR-022).  One GET is enough, so there is no
per-board retry loop like the leaderboard/battle collectors.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from newstar_sdk.exceptions import NewStarError


def collect_records(client: Any) -> Dict[str, Any]:
    """Fetch the universe records page."""

    payload: Dict[str, Any] = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "game.php?page=records",
        "base_url": str(getattr(client, "base_url", "") or ""),
        "updated_at": "",
        "updated_text": "",
        "sections": [],
        "errors": [],
    }
    try:
        records = client.get_universe_records()
    except NewStarError as exc:
        payload["errors"].append(
            {"section": "all", "error": f"{type(exc).__name__}: {exc}"}
        )
        return payload

    payload["updated_at"] = records.updated_at
    payload["updated_text"] = records.updated_text
    payload["sections"] = [section.as_dict() for section in records.sections]
    if not records.sections:
        payload["errors"].append(
            {"section": "all", "error": "空页（页面结构可能变化）"}
        )
    return payload


def write_records(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish the universe-records snapshot for the read-only TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_records(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

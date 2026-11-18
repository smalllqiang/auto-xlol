"""Collect the empire overview for the dashboard (``page=control``).

One request returns every colony; the daemon owns the game session, so it
collects and writes ``state/empire.json``; the TUI only reads that file
(ADR-011, same split as statistics/messages/records).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from newstar_sdk.exceptions import NewStarError

SOURCE = "game.php?page=control"


def collect_empire(client: Any) -> Dict[str, Any]:
    """Fetch ``page=control`` and return a JSON-ready payload.

    Failures are recorded in ``errors`` instead of raising: a broken empire
    page must not stop the loop or hide the rest of the journal.
    """

    payload: Dict[str, Any] = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE,
        "colony_slots": None,
        "planets": [],
        "total": None,
        "unresolved": {},
        "errors": [],
    }
    try:
        report = client.get_control()
    except NewStarError as exc:
        payload["errors"].append(
            {"section": "control", "error": f"{type(exc).__name__}: {exc}"}
        )
        return payload

    payload.update(report.as_dict())
    if not report.planets:
        payload["errors"].append(
            {"section": "planets", "error": "空表（页面结构可能变化）"}
        )
    return payload


def write_empire(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish the empire snapshot for the read-only TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_empire(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

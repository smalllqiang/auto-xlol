"""Collect the read-only battle hall page for the dashboard.

``page=battleHall`` lists the top 100 battles (rank, attacker/defender,
winner/loser colors, date, unit score).  The daemon owns the game session and
writes ``state/battles.json``; the TUI only reads that file (ADR-011/ADR-021).
Nothing here opens a battle report, so the cached file stays small.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from newstar_sdk.exceptions import NewStarError

#: The two useful orderings: the hall ranking and the newest battles.
BATTLE_BOARDS: Tuple[Tuple[str, str], ...] = (("units", "desc"), ("date", "desc"))

BATTLE_ORDER_LABELS = {"units": "单位积分", "date": "日期"}


def battle_board_key(order: str, sort: str) -> str:
    return f"{str(order).lower()}:{str(sort).lower()}"


def collect_battles(
    client: Any,
    *,
    boards: Iterable[Tuple[str, str]] = BATTLE_BOARDS,
) -> Dict[str, Any]:
    """Fetch the configured battle-hall orderings.

    Per-board failures are collected in ``errors`` instead of aborting the
    whole collection: one slow ordering must not hide the other.
    """

    payload: Dict[str, Any] = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "game.php?page=battleHall",
        "base_url": str(getattr(client, "base_url", "") or ""),
        "boards": {},
        "errors": [],
    }
    for order, sort in boards:
        key = battle_board_key(order, sort)
        try:
            hall = client.get_battle_hall(order=order, sort=sort)
        except NewStarError as exc:
            payload["errors"].append(
                {"board": key, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        payload["boards"][key] = hall.as_dict()
        if not hall.rows:
            payload["errors"].append(
                {"board": key, "error": "空榜（页面结构可能变化）"}
            )
        if hall.order != order or hall.sort != sort:
            payload["errors"].append(
                {
                    "board": key,
                    "error": (
                        f"页面返回 order={hall.order} sort={hall.sort}，"
                        f"请求 order={order} sort={sort}"
                    ),
                }
            )
    return payload


def write_battles(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish the battle-hall snapshot for the read-only TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_battles(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

"""Collect the read-only leaderboard page for the dashboard.

``page=statistics`` is a filter form (who/type/range) plus a ranking table.
The daemon owns the game session, so it collects the boards and writes
``state/statistics.json``; the TUI only reads that file (ADR-011/ADR-019).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

from newstar_sdk.exceptions import NewStarError

WHO_LABELS = {1: "玩家", 2: "联盟"}
SORT_LABELS = {1: "积分", 2: "舰队", 3: "研究", 4: "建筑", 5: "防御"}


def board_key(who: int, sort: int) -> str:
    return f"{int(who)}:{int(sort)}"


def collect_statistics(
    client: Any,
    *,
    who_values: Iterable[int] = (1, 2),
    sort_values: Iterable[int] = (1,),
    range_value: int = 1,
) -> Dict[str, Any]:
    """Fetch the configured leaderboard combinations.

    Per-board failures are collected in ``errors`` instead of aborting the
    whole collection: one slow tab should not hide the other tab.
    """

    payload: Dict[str, Any] = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "game.php?page=statistics",
        "range": int(range_value),
        "boards": {},
        "errors": [],
    }
    for who in who_values:
        for sort in sort_values:
            key = board_key(who, sort)
            try:
                board = client.get_statistics(who=who, sort=sort, range=range_value)
            except NewStarError as exc:
                payload["errors"].append(
                    {"board": key, "error": f"{type(exc).__name__}: {exc}"}
                )
                continue
            payload["boards"][key] = board.as_dict()
            if not board.rows:
                payload["errors"].append(
                    {"board": key, "error": "空榜（页面结构可能变化）"}
                )
            if int(board.who) != int(who) or int(board.sort) != int(sort):
                payload["errors"].append(
                    {
                        "board": key,
                        "error": (
                            f"页面返回 who={board.who} sort={board.sort}，"
                            f"请求 who={who} sort={sort}"
                        ),
                    }
                )
    return payload


def write_statistics(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish the leaderboard snapshot for the read-only TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_statistics(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

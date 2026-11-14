"""Collect recent inbox messages for the read-only TUI reader.

The daemon owns the game session and writes ``state/messages.json``; the TUI
only reads that file.  Nothing here marks messages read, archives or deletes
them: the game's unread counters are untouched.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from newstar_sdk.exceptions import NewStarError


def collect_messages(
    client: Any,
    *,
    category: Any = "all",
    max_pages: int = 10,
) -> Dict[str, Any]:
    """Fetch the newest ``max_pages`` inbox pages and serialise the messages.

    A failed later page is recorded in ``errors`` and the messages already
    fetched are still published; a failed first page yields an empty archive
    with the error attached.
    """

    payload: Dict[str, Any] = {
        "schema": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "category": str(category),
        "category_name": "",
        "pages_total": 0,
        "pages_fetched": 0,
        "messages": [],
        "errors": [],
    }
    try:
        first = client.get_messages(category, page=1)
    except NewStarError as exc:
        payload["errors"].append(
            {"page": 1, "error": f"{type(exc).__name__}: {exc}"}
        )
        return payload

    payload["category"] = first.category if first.category is not None else str(category)
    payload["category_name"] = first.category_name or ""
    pages = sorted({int(page) for page in (first.pages or [1])})
    payload["pages_total"] = len(pages)
    target = pages[: max(1, int(max_pages))]
    fetched = 1

    collected = list(first.messages)
    for page in target:
        if page == 1:
            continue
        try:
            result = client.get_messages(category, page=page)
        except NewStarError as exc:
            payload["errors"].append(
                {"page": page, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        collected.extend(result.messages)
        fetched += 1

    seen = set()
    unique: List[Any] = []
    for message in collected:
        key = int(getattr(message, "id", 0) or 0)
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)

    unique.sort(
        key=lambda item: (
            getattr(item, "time", None) or datetime.min,
            int(getattr(item, "id", 0) or 0),
        ),
        reverse=True,
    )
    payload["pages_fetched"] = fetched
    payload["messages"] = [message.as_dict() for message in unique]
    return payload


def write_messages(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish the message archive for the read-only TUI."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def load_messages(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

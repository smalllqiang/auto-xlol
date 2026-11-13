"""Append-only JSONL journal with simple size-based rotation.

Rotation keeps one backup (``<name>.1``); that is enough for a single-account
loop and avoids pulling in a logging framework.  Rotation is size based, not
date based, because the write rate varies with the cycle interval.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def rotate_if_needed(path: Path, max_bytes: int) -> bool:
    """Rotate ``path`` to ``path.1`` when it exceeds ``max_bytes``."""

    if max_bytes <= 0 or not path.exists():
        return False
    if path.stat().st_size < max_bytes:
        return False
    backup = path.with_name(path.name + ".1")
    backup.unlink(missing_ok=True)
    path.rename(backup)
    return True


def append_jsonl(path: Path, payload: Dict[str, Any], *, max_bytes: int = 0) -> None:
    """Append one JSON object as a line, rotating first if needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_if_needed(path, max_bytes)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

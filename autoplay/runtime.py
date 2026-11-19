"""Process-level helpers for the unattended loops.

The deterministic daemon has its own copies of these helpers in
``scripts/run_forever.py``; this module is the shared version for the agent
loop.  Migrating the daemon to it is tracked as roadmap P4 (it is a pure
refactor, so it is deliberately not mixed into the agent work).

Nothing here touches the game: it is pid locks, heartbeat files and backoff.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

#: Exit code used when another writer already holds the account.
EXIT_BUSY = 3


def alive(pid: int) -> bool:
    """True when ``pid`` looks alive (or when we cannot tell)."""

    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError):
        return True
    return True


def read_lock(path: Path) -> int:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def acquire_lock(path: Path) -> int:
    """Create an exclusive pid lock; take over a stale one.

    Raises ``SystemExit`` when a live process holds it: two writers on one
    account is the one thing this project must never do (AGENTS rule 6).
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except FileExistsError:
            pid = read_lock(path)
            if pid and alive(pid):
                raise SystemExit(
                    f"另一个写进程正在运行（pid {pid}，锁 {path}）；同一账号不能并发写"
                ) from None
            path.unlink(missing_ok=True)
    raise SystemExit(f"无法获取锁 {path}")


def release_lock(fd: int, path: Path) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    Path(path).unlink(missing_ok=True)


def write_status(path: Path, payload: Dict[str, Any]) -> None:
    """Atomically publish a heartbeat JSON file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


__all__ = [
    "EXIT_BUSY",
    "acquire_lock",
    "alive",
    "read_lock",
    "release_lock",
    "write_status",
]

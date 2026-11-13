"""Read-only dashboard state and text rendering.

The TUI (``scripts/tui.py``) is a thin wrapper around this module: everything
here is file reading plus pure formatting, so it can be tested (and rendered
to plain text with ``--once``) without a terminal.

Data comes from the files the daemon already writes:

* ``state/snapshots.jsonl`` -- full account state (every N cycles)
* ``state/journal.jsonl``   -- one line per cycle (action, queues, alerts)
* ``state/events.jsonl``    -- diffed events (on snapshot cadence)
* ``state/daemon.lock``     -- pid of a running loop, if any
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TAIL_BYTES = 512 * 1024

QUEUE_NAMES = {"building": "建造", "research": "研究", "shipyard": "船坞"}

PHASE_NAMES = {
    "pre_gravity": "种田初起",
    "expedition_prep": "探险准备",
    "expedition": "探险纪元",
}


def _read_tail(path: Path, limit: int) -> List[Dict[str, Any]]:
    """Read up to ``limit`` complete JSON objects from the end of a JSONL file."""

    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        data = handle.read()
    lines = data.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]  # drop the possibly partial first line
    items: List[Dict[str, Any]] = []
    for line in lines[-limit * 4 :]:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items[-limit:]


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class DashboardState:
    snapshot: Optional[Dict[str, Any]] = None
    journal: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    daemon_pid: Optional[int] = None
    daemon_lock_age: Optional[float] = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def last_cycle(self) -> Optional[Dict[str, Any]]:
        for entry in reversed(self.journal):
            if entry.get("kind") == "cycle":
                return entry
        return None

    @property
    def alerts(self) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        for entry in self.journal:
            for alert in entry.get("alerts") or []:
                alerts.append({"at": entry.get("at"), **alert})
        return alerts

    @property
    def updated_at(self) -> Optional[datetime]:
        candidates = []
        if self.snapshot:
            candidates.append(_parse_time(self.snapshot.get("captured_at")))
        if self.last_cycle:
            candidates.append(_parse_time(self.last_cycle.get("at")))
        return max((item for item in candidates if item), default=None)


def load_state(state_dir: Path, *, now: Optional[datetime] = None) -> DashboardState:
    now = now or datetime.now(timezone.utc)
    snapshots = _read_tail(state_dir / "snapshots.jsonl", 1)
    journal = _read_tail(state_dir / "journal.jsonl", 40)
    events = _read_tail(state_dir / "events.jsonl", 40)

    pid: Optional[int] = None
    lock_age: Optional[float] = None
    lock_path = state_dir / "daemon.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid = None
        try:
            lock_age = max(0.0, now.timestamp() - lock_path.stat().st_mtime)
        except OSError:
            lock_age = None

    return DashboardState(
        snapshot=snapshots[-1] if snapshots else None,
        journal=journal,
        events=events,
        daemon_pid=pid,
        daemon_lock_age=lock_age,
        now=now,
    )


# ---------------------------------------------------------------------------
# Formatting (CJK-aware width)
# ---------------------------------------------------------------------------


def text_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def truncate(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    result = ""
    used = 0
    for char in text:
        step = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + step > width - 1:
            break
        result += char
        used += step
    return result + "…"


def pad(text: str, width: int) -> str:
    trimmed = truncate(text, width)
    return trimmed + " " * max(0, width - text_width(trimmed))


def fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    for unit, scale in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(number) >= scale:
            return f"{number / scale:.2f}{unit}"
    if number == int(number):
        return f"{int(number):,}"
    return f"{number:,.1f}"


def fmt_time(value: Any) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "-"
    return parsed.astimezone().strftime("%H:%M:%S")


def fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分前"
    if seconds < 86400:
        return f"{seconds // 3600} 时前"
    return f"{seconds // 86400} 天前"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _rule(width: int) -> str:
    return "─" * width


def _resource_rows(snapshot: Dict[str, Any], width: int) -> List[str]:
    resources = snapshot.get("resources") or {}
    amounts = resources.get("amounts") or {}
    capacities = resources.get("capacities") or {}
    production = resources.get("production") or {}

    rows = [f"{pad('资源', 8)}{pad('数量', 14)}{pad('容量', 12)}{pad('每小时', 12)}"]
    for key, label in (("901", "金属"), ("902", "水晶"), ("903", "重氢"), ("911", "幽能")):
        rows.append(
            f"{pad(label, 8)}"
            f"{pad(fmt_number(amounts.get(key, 0)), 14)}"
            f"{pad(fmt_number(capacities[key]) if key in capacities else '-', 12)}"
            f"{pad(fmt_number(production.get(key, 0)) if key in production else '-', 12)}"
        )
    return [truncate(row, width) for row in rows]


def _account_line(snapshot: Dict[str, Any], width: int) -> str:
    account = snapshot.get("account") or {}
    planet = snapshot.get("planet") or {}
    left = (
        f"{account.get('game_name') or 'New-Star'} · "
        f"{account.get('username') or '?'} @ {account.get('universe') or '?'}"
    )
    right = f"{planet.get('name') or '?'} {planet.get('coordinates') or ''}"
    left, right = truncate(left, width - text_width(right) - 2), truncate(right, width // 2)
    gap = max(1, width - text_width(left) - text_width(right))
    return left + " " * gap + right


def render_text(
    state: DashboardState, *, width: int = 110, log_lines: int = 10, event_lines: int = 6
) -> List[str]:
    width = max(40, width)
    lines: List[str] = []

    if state.snapshot:
        lines.append(_account_line(state.snapshot, width))
    else:
        lines.append("New-Star 看板")

    age = None
    if state.updated_at:
        age = (state.now - state.updated_at).total_seconds()
    daemon = (
        f"守护 pid {state.daemon_pid}（{fmt_age(state.daemon_lock_age)}）"
        if state.daemon_pid
        else "守护未运行"
    )
    cycle = state.last_cycle or {}
    mode = cycle.get("mode") or "-"
    lines.append(
        truncate(f"数据 {fmt_age(age)} · {daemon} · 模式 {mode}", width)
    )
    lines.append(_rule(width))

    if not state.snapshot:
        lines.append("尚无快照：先运行 scripts/collect_state.py 或 scripts/run_forever.py")
        return [truncate(line, width).rstrip() for line in lines]

    lines.extend(_resource_rows(state.snapshot, width))
    lines.append(_rule(width))

    phase_key = cycle.get("phase") or "-"
    phase = f"{PHASE_NAMES.get(phase_key, phase_key)}（{phase_key}）"
    goal = cycle.get("goal") or "-"
    slots = ((state.snapshot.get("research") or {}).get("expedition_slots"))
    slot_text = f"    探险航道 {slots}" if slots is not None else ""
    lines.append(truncate(f"阶段  {phase}    目标  {goal}{slot_text}", width))

    if cycle:
        action = (cycle.get("action") or {}).get("name") or "-"
        queues = cycle.get("queues") or {}
        queue_text = " ".join(
            f"{QUEUE_NAMES.get(key, key)}{value}" for key, value in queues.items()
        )
        lines.append(
            truncate(
                f"上轮  {fmt_time(cycle.get('at'))}  {cycle.get('status')}: {action}"
                f"  ·  队列 {queue_text}  ·  {cycle.get('reason')}",
                width,
            )
        )

    notes = cycle.get("notes") or []
    for note in notes[:2]:
        lines.append(truncate(f"备注  {note}", width))

    lines.append(_rule(width))

    alerts = state.alerts[-event_lines:]
    if alerts:
        lines.append("告警")
        for alert in alerts:
            lines.append(
                truncate(f"  {fmt_time(alert.get('at'))}  ALERT  {alert.get('message', '')}", width)
            )

    events = state.events[-event_lines:]
    if events:
        lines.append("事件")
        for event in events:
            detail = event.get("name") or event.get("queue") or event.get("resource") or ""
            lines.append(
                truncate(
                    f"  {fmt_time(event.get('at'))}  {event.get('kind')}  "
                    f"{event.get('severity', '')}  {detail}",
                    width,
                )
            )

    lines.append("日志")
    for entry in state.journal[-log_lines:]:
        if entry.get("kind") == "error":
            text = f"error  {entry.get('error', '')}"
        else:
            action = (entry.get("action") or {}).get("name") or "-"
            text = f"{entry.get('status', '?'):<8} {action}  {entry.get('reason', '')}"
        lines.append(truncate(f"  {fmt_time(entry.get('at'))}  {text}", width))

    return [truncate(line, width).rstrip() for line in lines]

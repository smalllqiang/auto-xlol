"""Read-only dashboard state and text rendering.

The TUI (``scripts/tui.py``) is a thin wrapper around this module: everything
here is file reading plus pure formatting, so it can be tested (and rendered
to plain text with ``--once``) without a terminal.

Data comes from the files the daemon already writes:

* ``state/daemon.json``     -- heartbeat: pid, mode, interval, next cycle
* ``state/snapshots.jsonl`` -- full account state (every N cycles)
* ``state/journal.jsonl``   -- one line per cycle (action, queues, alerts)
* ``state/events.jsonl``    -- diffed events (on snapshot cadence)
* ``state/statistics.json`` -- leaderboard snapshot for the second tab
* ``state/messages.json``   -- inbox cache for the third tab
* ``state/battles.json``    -- battle hall snapshot for the fourth tab
* ``state/daemon.lock``     -- pid of a running loop (fallback for old runs)
* ``state/STOP``            -- kill switch requested by the operator

Nothing here talks to the game, and nothing here writes to ``state/``.
"""

from __future__ import annotations

import json
import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

TAIL_BYTES = 512 * 1024
DEFAULT_INTERVAL = 60.0

QUEUE_NAMES = {"building": "建造", "research": "研究", "shipyard": "船坞"}

RESOURCE_NAMES = {"901": "金属", "902": "水晶", "903": "重氢", "911": "幽能"}

PHASE_NAMES = {
    "pre_gravity": "种田初起",
    "expedition_prep": "探险准备",
    "expedition": "探险纪元",
}

STATISTICS_WHO = {1: "玩家", 2: "联盟"}
STATISTICS_SORT = {1: "积分", 2: "舰队", 3: "研究", 4: "建筑", 5: "防御"}

BATTLE_ORDER_NAMES = {"units": "单位积分", "date": "日期"}

STATUS_NAMES = {
    "executed": "已执行",
    "dry_run": "预览",
    "skipped": "跳过",
    "paused": "暂停",
    "error": "失败",
    "dispatched": "已派出",
    "partial": "部分派出",
}

EVENT_NAMES = {
    "research_completed": "研究完成",
    "queue_item_finished": "队列完成",
    "queue_item_added": "队列新增",
    "message_new": "新消息",
    "message_check_failed": "消息读取失败",
}


@dataclass
class FrameLine:
    """One rendered line plus the style the curses layer should apply."""

    text: str
    style: str = "normal"  # normal | title | section | muted | ok | warn | error | alert
    entry: Optional[Dict[str, Any]] = None


@dataclass
class DashboardState:
    snapshot: Optional[Dict[str, Any]] = None
    journal: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    statistics: Dict[str, Any] = field(default_factory=dict)
    message_log: Dict[str, Any] = field(default_factory=dict)
    battle_log: Dict[str, Any] = field(default_factory=dict)
    daemon: Dict[str, Any] = field(default_factory=dict)
    daemon_pid: Optional[int] = None
    daemon_lock_age: Optional[float] = None
    daemon_alive: Optional[bool] = None
    stop_requested: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def cycle_entries(self) -> List[Dict[str, Any]]:
        return [entry for entry in self.journal if _is_cycle(entry)]

    @property
    def last_cycle(self) -> Optional[Dict[str, Any]]:
        entries = self.cycle_entries
        return entries[-1] if entries else None

    @property
    def last_expedition(self) -> Optional[Dict[str, Any]]:
        for entry in reversed(self.journal):
            if entry.get("kind") in {"expedition", "expedition_error"}:
                return entry
        return None

    @property
    def last_error(self) -> Optional[Dict[str, Any]]:
        for entry in reversed(self.journal):
            if entry.get("kind") == "error":
                return entry
        return None

    @property
    def consecutive_failures(self) -> int:
        count = 0
        for entry in reversed(self.journal):
            kind = entry.get("kind")
            if kind == "error":
                count += 1
            elif kind in {"expedition", "expedition_error"}:
                continue
            else:
                break
        return count

    @property
    def alerts(self) -> List[Dict[str, Any]]:
        alerts: List[Dict[str, Any]] = []
        seen = set()
        for entry in self.journal:
            for alert in entry.get("alerts") or []:
                key = (entry.get("at"), alert.get("message"))
                if key in seen:
                    continue
                seen.add(key)
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

    @property
    def data_age(self) -> Optional[float]:
        updated = self.updated_at
        if updated is None:
            return None
        return max(0.0, (self.now - updated).total_seconds())

    @property
    def interval(self) -> float:
        try:
            return float(self.daemon.get("interval") or DEFAULT_INTERVAL)
        except (TypeError, ValueError):
            return DEFAULT_INTERVAL

    @property
    def messages(self) -> Dict[str, Any]:
        cycle = self.last_cycle or {}
        cycle_messages = cycle.get("messages")
        snapshot_messages = (self.snapshot or {}).get("messages")
        cycle_at = _parse_time(cycle.get("at"))
        snapshot_at = _parse_time((self.snapshot or {}).get("captured_at"))
        if (
            isinstance(snapshot_messages, dict)
            and snapshot_messages
            and snapshot_at is not None
            and cycle_at is not None
            and snapshot_at > cycle_at
        ):
            return snapshot_messages
        if isinstance(cycle_messages, dict) and cycle_messages:
            return cycle_messages
        return snapshot_messages if isinstance(snapshot_messages, dict) else {}

    @property
    def queue_items(self) -> Dict[str, List[Dict[str, Any]]]:
        cycle = self.last_cycle or {}
        cycle_items = cycle.get("queue_items") or {}
        snapshot_queues = (self.snapshot or {}).get("queues")
        cycle_at = _parse_time(cycle.get("at"))
        snapshot_at = _parse_time((self.snapshot or {}).get("captured_at"))

        # The snapshot is written after the cycle; prefer it when it is newer
        # (a queue may have finished in between).  With --snapshot-every > 1
        # the per-cycle items are fresher, so fall back to them.
        use_snapshot = isinstance(snapshot_queues, dict) and (
            snapshot_at is None
            or cycle_at is None
            or snapshot_at >= cycle_at
        )
        items: Dict[str, List[Dict[str, Any]]] = {}
        if use_snapshot:
            for key, value in snapshot_queues.items():
                if isinstance(value, list):
                    items[key] = list(value)
            return items
        for key, value in cycle_items.items():
            if isinstance(value, list) and value:
                items[key] = list(value)
        return items

    @property
    def plan_top(self) -> List[Dict[str, Any]]:
        cycle = self.last_cycle or {}
        plan = cycle.get("plan_top")
        if isinstance(plan, list) and plan:
            return plan
        action = cycle.get("action")
        if isinstance(action, dict):
            item = dict(action)
            item.setdefault("selected", True)
            return [item]
        return []


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _read_tail(path: Path, limit: int) -> List[Dict[str, Any]]:
    """Read up to ``limit`` complete JSON objects from the end of a JSONL file."""

    if limit <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read()
    except OSError:
        return []
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


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sort_entries(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort JSONL records by time; untimed records stay first, in file order."""

    decorated: List[Tuple[int, datetime, int, Dict[str, Any]]] = []
    for index, item in enumerate(items):
        when = _parse_time(item.get("at") or item.get("captured_at"))
        decorated.append(
            (
                1 if when is not None else 0,
                when or datetime.min.replace(tzinfo=timezone.utc),
                index,
                item,
            )
        )
    decorated.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[3] for row in decorated]


def _read_recent(path: Path, limit: int) -> List[Dict[str, Any]]:
    """Read the newest ``limit`` records, falling back to the rotated ``.1``."""

    current = _read_tail(path, limit)
    if len(current) < limit:
        backup = _read_tail(path.with_name(path.name + ".1"), limit - len(current))
        current = backup + current
    return _sort_entries(current)[-limit:]


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


def _pid_alive(pid: Optional[int]) -> Optional[bool]:
    if not pid:
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def _is_cycle(entry: Dict[str, Any]) -> bool:
    kind = entry.get("kind")
    if kind == "cycle":
        return True
    if kind:
        # error / expedition / expedition_error / start / stop are other kinds
        return False
    # Older journals wrote cycle records without a kind; keep reading them.
    return "phase" in entry and "status" in entry


def load_state(state_dir: Path, *, now: Optional[datetime] = None) -> DashboardState:
    now = now or datetime.now(timezone.utc)
    daemon = _read_json(state_dir / "daemon.json")
    snapshots = _read_tail(state_dir / "snapshots.jsonl", 1)
    journal = _read_recent(state_dir / "journal.jsonl", 120)
    events = _read_recent(state_dir / "events.jsonl", 80)
    statistics = _read_json(state_dir / "statistics.json")
    message_log = _read_json(state_dir / "messages.json")
    battle_log = _read_json(state_dir / "battles.json")

    pid: Optional[int] = None
    lock_age: Optional[float] = None
    lock_path = state_dir / "daemon.lock"
    if daemon.get("pid") is not None:
        try:
            pid = int(daemon["pid"])
        except (TypeError, ValueError):
            pid = None
    if lock_path.exists():
        if pid is None:
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
        statistics=statistics,
        message_log=message_log,
        battle_log=battle_log,
        daemon=daemon,
        daemon_pid=pid,
        daemon_lock_age=lock_age,
        daemon_alive=_pid_alive(pid),
        stop_requested=(state_dir / "STOP").exists(),
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


def fmt_time(value: Any, *, now: Optional[datetime] = None) -> str:
    parsed = _parse_time(value)
    if parsed is None:
        return "-"
    local = parsed.astimezone()
    reference = (now or datetime.now(timezone.utc)).astimezone()
    if local.date() == reference.date():
        return local.strftime("%H:%M:%S")
    return local.strftime("%m-%d %H:%M")


def fmt_message_time(
    message: Dict[str, Any], *, now: Optional[datetime] = None
) -> str:
    """Format a message timestamp.

    Message pages render the *server's* wall clock without an offset, so a
    naive value must not be treated as UTC (doing so shifts it by the local
    timezone).  Aware values are converted to local time as usual.
    """

    raw = message.get("time")
    parsed: Optional[datetime] = None
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            parsed = None
    if parsed is None:
        return str(message.get("time_text") or "-")
    if parsed.tzinfo is None:
        local = parsed
    else:
        local = parsed.astimezone()
    reference = (now or datetime.now(timezone.utc)).astimezone()
    if local.date() == reference.date():
        return local.strftime("%H:%M:%S")
    return local.strftime("%m-%d %H:%M")


def fmt_battle_time(
    row: Dict[str, Any], *, now: Optional[datetime] = None
) -> str:
    """Format a battle-hall timestamp.

    The hall renders the viewer's wall clock without an offset, so a naive
    value must not be treated as UTC.  The hall is historical, so the date is
    always shown: same-year battles use ``MM-DD HH:MM`` and older ones use
    ``YYYY-MM-DD``.
    """

    raw = row.get("time")
    parsed: Optional[datetime] = None
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
        except ValueError:
            parsed = None
    if parsed is None:
        return str(row.get("date") or "-")
    if parsed.tzinfo is None:
        local = parsed
    else:
        local = parsed.astimezone()
    reference = (now or datetime.now(timezone.utc)).astimezone()
    if local.year == reference.year:
        return local.strftime("%m-%d %H:%M")
    return local.strftime("%Y-%m-%d")


def fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分前"
    if seconds < 86400:
        return f"{seconds // 3600} 时前"
    return f"{seconds // 86400} 天前"


def fmt_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    value = max(0, int(seconds))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m{value % 60:02d}s"
    if value < 86400:
        return f"{value // 3600}h{(value % 3600) // 60:02d}m"
    return f"{value // 86400}d{(value % 86400) // 3600:02d}h"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _rule(width: int) -> str:
    return "─" * width


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


def _health(state: DashboardState) -> Tuple[str, str]:
    status = state.daemon or {}
    pid = state.daemon_pid
    interval = state.interval
    mode = status.get("mode") or (state.last_cycle or {}).get("mode") or "-"
    failures = int(status.get("failures") or state.consecutive_failures or 0)
    max_failures = int(status.get("max_failures") or 10)
    data_age = state.data_age
    heartbeat = _parse_time(status.get("updated_at"))
    heartbeat_age = (
        max(0.0, (state.now - heartbeat).total_seconds())
        if heartbeat is not None
        else None
    )
    stale_after = max(2.0 * interval, 90.0)
    dead_after = max(5.0 * interval, 300.0)

    if status.get("state") == "stopped":
        suffix = " · state/STOP 仍在" if state.stop_requested else ""
        return "muted", f"● 守护已停止 · 数据 {fmt_age(data_age)}{suffix}"
    if state.stop_requested:
        return "warn", "● 停止请求已存在（state/STOP），守护将在下一轮退出"

    # The heartbeat is authoritative: pid namespaces (containers, sandboxes)
    # can make a live daemon invisible to ``os.kill(pid, 0)``.  Fall back to
    # the lock file only for old runs that predate daemon.json.
    if heartbeat is not None and heartbeat_age is not None:
        if heartbeat_age > dead_after:
            level, label = "error", "心跳停更"
        elif heartbeat_age > stale_after:
            level, label = "warn", "心跳偏旧"
        elif status.get("state") == "starting":
            level, label = "warn", "启动中"
        else:
            level, label = "ok", "运行中"
    elif pid and state.daemon_lock_age is not None:
        if state.daemon_alive is False:
            return "error", f"● 守护已退出（pid {pid} 不存在，锁残留）"
        if state.daemon_lock_age > dead_after:
            level, label = "error", "锁已过期"
        else:
            level, label = "ok", "运行中（旧版无心跳）"
    else:
        if data_age is None:
            return "warn", "● 守护未运行 · 状态目录为空"
        level = "error" if data_age > dead_after else "warn"
        return level, f"● 守护未运行 · 数据 {fmt_age(data_age)}"

    if data_age is None:
        level = "warn" if level == "ok" else level
        label += " / 等待数据"
    elif data_age > dead_after:
        level = "error"
        label += " / 数据停更"
    elif data_age > stale_after:
        if level == "ok":
            level = "warn"
        label += " / 数据偏旧"

    if failures and level == "ok":
        level = "warn"

    parts = [f"● {label}", f"pid {pid}" if pid else "pid -", mode, f"{fmt_seconds(interval)}/轮"]
    if heartbeat_age is not None:
        parts.append(f"心跳 {fmt_age(heartbeat_age)}")
    parts.append(f"数据 {fmt_age(data_age)}")

    cycle_started = _parse_time(status.get("cycle_started_at"))
    last_cycle_at = _parse_time(status.get("last_cycle_at"))
    if cycle_started is not None and (
        last_cycle_at is None or cycle_started > last_cycle_at
    ):
        elapsed = (state.now - cycle_started).total_seconds()
        parts.append(f"本轮进行中 {fmt_seconds(elapsed)}")
    else:
        next_at = _parse_time(status.get("next_cycle_at"))
        if next_at is not None:
            remaining = (next_at - state.now).total_seconds()
            parts.append("即将开始" if remaining <= 1 else f"下轮 {fmt_seconds(remaining)}")

    if failures:
        parts.append(f"连续失败 {failures}/{max_failures}")
    return level, " · ".join(parts)


def _resource_rows(snapshot: Dict[str, Any], width: int) -> List[str]:
    resources = snapshot.get("resources") or {}
    amounts = resources.get("amounts") or {}
    capacities = resources.get("capacities") or {}
    production = resources.get("production") or {}
    energy = snapshot.get("energy") or {}

    rows = [f"{pad('资源', 8)}{pad('数量', 14)}{pad('产能/h', 12)}{pad('容量*', 12)}"]
    for key, label in (("901", "金属"), ("902", "水晶"), ("903", "重氢"), ("911", "幽能")):
        amount = amounts.get(key)
        capacity = capacities.get(key)
        per_hour = production.get(key)
        if per_hour is None and key == "911":
            per_hour = energy.get("production")
        rows.append(
            f"{pad(label, 8)}"
            f"{pad(fmt_number(amount), 14)}"
            f"{pad(fmt_number(per_hour), 12)}"
            f"{pad(fmt_number(capacity), 12)}"
        )
    return [truncate(row, width) for row in rows]


def _queue_line(kind: str, items: List[Dict[str, Any]], width: int) -> str:
    label = QUEUE_NAMES.get(kind, kind)
    if not items:
        return truncate(f"  {label}  空闲", width)
    parts: List[str] = []
    for item in items[:2]:
        name = item.get("name") or f"#{item.get('element_id') or '?'}"
        target = item.get("target_level")
        remaining = item.get("remaining_seconds")
        text = name
        if target is not None:
            text += f" →{target}"
        if remaining is not None:
            text += f"  剩余 {fmt_seconds(remaining)}"
        parts.append(text)
    if len(items) > 2:
        parts.append(f"等 {len(items)} 项")
    return truncate(f"  {label}  " + "；".join(parts), width)


def _cost_text(action: Dict[str, Any]) -> str:
    missing = action.get("missing_resources") or {}
    cost = action.get("cost") or {}
    parts: List[str] = []
    for key, label in RESOURCE_NAMES.items():
        missed = _as_float(missing.get(key))
        if missed > 0:
            parts.append(f"缺{label} {fmt_number(missed)}")
        elif _as_float(cost.get(key)) > 0:
            parts.append(f"{label} {fmt_number(cost.get(key))}")
    return " ".join(parts)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _action_line(action: Dict[str, Any], width: int) -> str:
    marker = "▸" if action.get("selected") else "·"
    name = action.get("name") or f"#{action.get('element_id') or '?'}"
    current = action.get("current_level")
    target = action.get("target_level")
    levels = f"{current}→{target}" if current is not None or target is not None else ""
    text = f"  {marker} {name} {levels}".rstrip()
    costs = _cost_text(action)
    if costs:
        text += f"  {costs}"
    payback = action.get("payback_hours")
    if payback is not None:
        text += f"  回本 {payback}h"
    affordable = action.get("affordable")
    text += "  ✔" if affordable else "  ✖"
    reason = action.get("reason")
    if reason and not costs:
        text += f"  {reason}"
    return truncate(text, width)


def _event_text(event: Dict[str, Any]) -> str:
    kind = event.get("kind") or "?"
    label = EVENT_NAMES.get(kind, kind)
    if kind == "research_completed":
        return f"{label} #{event.get('element_id')} {event.get('from')}→{event.get('to')}"
    if kind in {"queue_item_finished", "queue_item_added"}:
        queue = QUEUE_NAMES.get(event.get("queue"), event.get("queue") or "")
        detail = f"{queue} #{event.get('element_id')}"
        if event.get("target_level") is not None:
            detail += f" →{event.get('target_level')}"
        return f"{label} {detail}"
    if kind == "message_new":
        return f"{label} {event.get('name') or event.get('category') or ''}".rstrip()
    if kind == "message_check_failed":
        return f"{label} {event.get('error') or ''}".rstrip()
    return label


def _log_style(entry: Dict[str, Any]) -> str:
    kind = entry.get("kind")
    status = entry.get("status")
    if kind in {"error", "expedition_error"}:
        return "error"
    if status == "error":
        return "error"
    if status == "paused":
        return "warn"
    if status == "skipped":
        return "muted"
    if status in {"executed", "dry_run"}:
        return "ok"
    return "normal"


def _log_text(entry: Dict[str, Any]) -> str:
    kind = entry.get("kind")
    if kind == "error":
        return f"error  {entry.get('error') or ''}"
    if kind == "expedition_error":
        return f"探险 error  {entry.get('error') or ''}"
    if kind == "expedition":
        status = STATUS_NAMES.get(str(entry.get("status")), entry.get("status") or "?")
        return f"探险 {status}  {entry.get('reason') or ''}"
    status = STATUS_NAMES.get(str(entry.get("status")), entry.get("status") or "?")
    action = (entry.get("action") or {}).get("name") or "-"
    return f"{status}  {action}  {entry.get('reason') or ''}"


def _log_groups(
    entries: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """Group consecutive identical cycles so a stable plan does not flood the log."""

    groups: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    for entry in entries:
        action = (entry.get("action") or {}).get("name")
        signature = (
            entry.get("kind"),
            entry.get("status"),
            action,
            entry.get("reason"),
            entry.get("error"),
        )
        if groups and groups[-1][0] == signature:
            groups[-1][1].append(entry)
        else:
            groups.append((signature, [entry]))
    return [(items[-1], items) for _, items in groups]


def render_lines(
    state: DashboardState, *, width: int = 110, log_lines: int = 8, event_lines: int = 4
) -> List[FrameLine]:
    """Render the dashboard as styled lines (``--once`` prints their text)."""

    width = max(40, width)
    lines: List[FrameLine] = []

    def add(text: str, style: str = "normal", entry: Optional[Dict[str, Any]] = None) -> None:
        lines.append(FrameLine(truncate(text, width), style, entry))

    cycle = state.last_cycle or {}
    snapshot = state.snapshot

    if snapshot:
        add(_account_line(snapshot, width), "title")
    else:
        add("New-Star 看板", "title")

    level, health = _health(state)
    add(health, level)
    add(_rule(width), "muted")

    if snapshot:
        for row in _resource_rows(snapshot, width):
            add(row)
        snapshot_at = _parse_time(snapshot.get("captured_at"))
        snapshot_age = (
            fmt_age(max(0.0, (state.now - snapshot_at).total_seconds()))
            if snapshot_at is not None
            else "-"
        )
        add(
            f"* 仓容字段与资源量不一致，仅供参考；产能为页面值；快照 {snapshot_age}",
            "muted",
        )
    else:
        add("尚无快照：先运行 scripts/collect_state.py 或 scripts/run_forever.py", "warn")
    add(_rule(width), "muted")

    add("队列", "section")
    items = state.queue_items
    if items:
        for kind in ("building", "research", "shipyard"):
            add(_queue_line(kind, items.get(kind) or [], width))
    else:
        queues = cycle.get("queues") or {}
        if queues:
            summary = " ".join(
                f"{QUEUE_NAMES.get(key, key)} {value}" for key, value in queues.items()
            )
            add(f"  队列计数  {summary}（无明细）", "muted")
        else:
            add("  队列数据缺失", "muted")

    phase_key = cycle.get("phase") or "-"
    phase_name = PHASE_NAMES.get(phase_key, phase_key)
    goal = cycle.get("goal") or "-"
    research = (snapshot or {}).get("research") or {}
    slots = research.get("expedition_slots")
    add(f"阶段  {phase_name}（{phase_key}）   目标  {goal}", "normal")

    plan = state.plan_top
    if plan:
        add("候选", "section")
        for action in plan[:5]:
            style = "selected" if action.get("selected") else "normal"
            add(_action_line(action, width), style)
    elif cycle:
        add("候选  无（本轮未产出计划）", "muted")

    notes = cycle.get("notes") or []
    for note in notes[:2]:
        add(f"备注  {note}", "muted")
    add(_rule(width), "muted")

    add("探险", "section")
    tech = research.get("expedition_tech")
    if slots is not None:
        add(f"  技术 {tech if tech is not None else '-'} · 航道 {slots}")
    else:
        add("  暂无探险数据", "muted")
    fleets = (snapshot or {}).get("fleets") or []
    if fleets:
        for fleet in fleets[:3]:
            mission = fleet.get("mission") or "舰队"
            target = fleet.get("target") or "-"
            arrival = fleet.get("arrival_time") or "-"
            state_name = "返航" if fleet.get("returning") else "前往"
            add(f"  {mission} {state_name} {target} · 到达 {arrival}")
    else:
        add("  舰队  无在途", "muted")
    expedition = state.last_expedition
    if expedition:
        status = STATUS_NAMES.get(str(expedition.get("status")), expedition.get("status") or "?")
        add(f"  上次调度 {status}  {expedition.get('reason') or ''}")
    else:
        add("  上次调度  无（需 --expedition --execute）", "muted")

    messages = state.messages
    if messages:
        categories = messages.get("categories") or []
        if not categories:
            categories = messages.get("counters") or []
        categories = [
            item
            for item in categories
            if int(item.get("category") or 0) not in (100, 999)
            and int(item.get("unread") or 0) > 0
        ]
        detail = "、".join(
            f"{item.get('name') or item.get('category')} {item.get('unread')}"
            for item in categories[:4]
        )
        line = f"消息  未读 {messages.get('unread', 0)}"
        if detail:
            line += f"（{detail}）"
        line += f" · 收件箱 {messages.get('total', '-')} · 发件箱 {messages.get('outbox', '-')}"
        add(line)
    add(_rule(width), "muted")

    alerts = state.alerts[-event_lines:]
    add("告警", "section")
    if alerts:
        for alert in alerts:
            message = alert.get("message") or ""
            add(f"  {fmt_time(alert.get('at'), now=state.now)}  ALERT  {message}", "alert")
    else:
        add("  无", "muted")

    events = state.events[-event_lines:]
    if events:
        add("事件", "section")
        for event in events:
            severity = str(event.get("severity") or "info")
            style = "warn" if severity in {"warning", "notice"} else "normal"
            if severity == "error":
                style = "error"
            add(
                f"  {fmt_time(event.get('at'), now=state.now)}  {_event_text(event)}",
                style,
            )

    add("日志", "section")
    entries = state.journal[-log_lines:]
    if entries:
        for entry, group in _log_groups(entries):
            text = f"  {fmt_time(entry.get('at'), now=state.now)}  {_log_text(entry)}"
            if len(group) > 1:
                first = fmt_time(group[0].get("at"), now=state.now)
                last = fmt_time(entry.get("at"), now=state.now)
                span = f"{first}→{last}" if first != last else last
                text += f"  ×{len(group)}（{span}）"
            add(text, _log_style(entry), entry)
    else:
        add("  无", "muted")

    return lines


def render_statistics_lines(
    state: DashboardState,
    *,
    who: int = 1,
    sort: int = 1,
    width: int = 110,
) -> List[FrameLine]:
    """Render the leaderboard tab from the daemon's statistics snapshot."""

    width = max(40, width)
    lines: List[FrameLine] = []

    def add(text: str, style: str = "normal") -> None:
        lines.append(FrameLine(truncate(text, width), style))

    payload = state.statistics or {}
    label = f"{STATISTICS_WHO.get(who, who)}{STATISTICS_SORT.get(sort, sort)}榜"
    add(f"排行榜 · {label} · 1-100", "title")

    if not payload:
        add("尚无排行榜数据：守护在首轮会采集（--statistics-every，默认 120 轮）", "warn")
        add("命令示例：run_forever --no-verify-ssl --statistics-every 120", "muted")
        return lines

    captured = _parse_time(payload.get("captured_at"))
    age = (
        fmt_age(max(0.0, (state.now - captured).total_seconds()))
        if captured is not None
        else "-"
    )
    add(f"来源 {payload.get('source') or 'page=statistics'} · 快照 {age}", "muted")
    add(_rule(width), "muted")

    errors = payload.get("errors") or []
    if errors:
        add("采集错误", "section")
        for error in errors[:3]:
            add(f"  {error.get('board')}: {error.get('error')}", "warn")

    board = (payload.get("boards") or {}).get(f"{int(who)}:{int(sort)}")
    if not isinstance(board, dict):
        available = ", ".join(sorted((payload.get("boards") or {}).keys())) or "无"
        add(f"这张榜还没有采集到（已采集：{available}）", "warn")
        add("看板每 --statistics-every 轮重试一次。", "muted")
        return lines

    rows = board.get("rows") or []
    account = (state.snapshot or {}).get("account") or {}
    me = account.get("username") or ""
    my_rank = next(
        (row.get("rank") for row in rows if me and row.get("name") == me), None
    )
    summary = f"{len(rows)} 名"
    if my_rank is not None:
        summary += f" · 我的排名 #{my_rank}"
    add(summary, "muted")

    if who == 1:
        header = f"{pad('排名', 5)}{pad('玩家', 24)}{pad('联盟', 20)}{pad('积分', 12)}"
    else:
        header = f"{pad('排名', 5)}{pad('联盟', 30)}{pad('成员', 8)}{pad('积分', 12)}"
    add(header, "section")

    for row in rows:
        name = str(row.get("name") or "")
        score = fmt_number(row.get("score"))
        selected = bool(me and name == me)
        marker = "▸ " if selected else "  "
        rank = str(row.get("rank") or "-")
        if who == 1:
            alliance = str(row.get("alliance") or "-")
            text = (
                f"{marker}{pad(rank, 4)}"
                f"{pad(name, 24)}"
                f"{pad(alliance, 20)}"
                f"{pad(score, 12)}"
            )
        else:
            members = str(row.get("members") if row.get("members") is not None else "-")
            text = (
                f"{marker}{pad(rank, 4)}"
                f"{pad(name, 30)}"
                f"{pad(members, 8)}"
                f"{pad(score, 12)}"
            )
        add(text, "selected" if selected else "normal")

    return lines


def render_statistics_text(
    state: DashboardState,
    *,
    who: int = 1,
    sort: int = 1,
    width: int = 110,
) -> List[str]:
    return [
        line.text.rstrip()
        for line in render_statistics_lines(
            state, who=who, sort=sort, width=width
        )
    ]


def wrap_text(text: str, width: int) -> List[str]:
    """Wrap text by display width; wide CJK characters count as two."""

    width = max(10, width)
    lines: List[str] = []
    for paragraph in str(text or "").splitlines():
        paragraph = paragraph.rstrip()
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            if text_width(current + char) > width:
                lines.append(current)
                current = char
            else:
                current += char
        if current:
            lines.append(current)
    return lines or [""]


def _message_subject(message: Dict[str, Any], width: int = 60) -> str:
    subject = str(message.get("subject") or "").strip()
    if subject:
        return subject
    body = " ".join(str(message.get("body") or "").split())
    return body[:width] if body else "(无主题)"


def render_messages_lines(
    state: DashboardState,
    *,
    width: int = 110,
    selected: int = 0,
) -> List[FrameLine]:
    """Render the inbox reader's message list."""

    width = max(40, width)
    lines: List[FrameLine] = []

    def add(text: str, style: str = "normal") -> None:
        lines.append(FrameLine(truncate(text, width), style))

    payload = state.message_log or {}
    add("消息 · 收件箱", "title")
    if not payload:
        add("尚无消息缓存：守护首轮会采集（--messages-every，默认 60 轮）", "warn")
        add("命令示例：run_forever --no-verify-ssl --messages-pages 10", "muted")
        return lines

    captured = _parse_time(payload.get("captured_at"))
    age = (
        fmt_age(max(0.0, (state.now - captured).total_seconds()))
        if captured is not None
        else "-"
    )
    category = payload.get("category_name") or payload.get("category") or "收件箱"
    pages = payload.get("pages_fetched")
    total = payload.get("pages_total")
    add(
        f"来源 {category} · 快照 {age} · 页 {pages}/{total}",
        "muted",
    )
    add(_rule(width), "muted")

    errors = payload.get("errors") or []
    if errors:
        add("采集错误", "section")
        for error in errors[:3]:
            add(f"  第 {error.get('page')} 页: {error.get('error')}", "warn")

    messages = [item for item in (payload.get("messages") or []) if isinstance(item, dict)]
    if not messages:
        add("（没有消息）", "muted")
        return lines

    selected = max(0, min(int(selected), len(messages) - 1))
    add(f"{len(messages)} 条 · j/k 选择 · Enter/d 阅读", "muted")
    header = f"{pad('#', 4)}{pad('时间', 17)}{pad('发送方', 18)}主题"
    add(header, "section")

    for index, message in enumerate(messages):
        when = fmt_message_time(message, now=state.now)
        sender = str(message.get("sender") or "?")
        subject = _message_subject(message)
        marker = "▸ " if index == selected else "  "
        text = (
            f"{marker}{pad(str(index + 1), 4)}"
            f"{pad(when, 17)}"
            f"{pad(sender, 18)}"
            f"{subject}"
        )
        add(text, "selected" if index == selected else "normal")

    return lines


def render_message_body(message: Dict[str, Any], *, width: int = 110) -> List[str]:
    """Wrap one message's body for the full-screen reader."""

    width = max(40, width)
    lines: List[str] = []
    subject = str(message.get("subject") or "").strip()
    if subject:
        lines.append(truncate(subject, width))
    lines.append(_rule(width))
    lines.extend(truncate(line, width) for line in wrap_text(message.get("body") or "", width))

    coordinates = message.get("coordinates") or []
    if coordinates:
        lines.append("")
        lines.append(truncate("坐标 " + "、".join(str(item) for item in coordinates), width))
    return lines


def render_message_text(
    state: DashboardState,
    *,
    index: int = 0,
    width: int = 110,
) -> List[str]:
    messages = (state.message_log or {}).get("messages") or []
    if not messages:
        return render_messages_text(state, width=width)
    index = max(0, min(int(index), len(messages) - 1))
    message = messages[index]
    header = (
        f"消息 · {fmt_message_time(message, now=state.now)}"
        f" · {message.get('sender') or '?'}"
    )
    return [truncate(header, width)] + render_message_body(message, width=width)


def render_messages_text(
    state: DashboardState,
    *,
    width: int = 110,
    selected: int = 0,
) -> List[str]:
    return [
        line.text.rstrip()
        for line in render_messages_lines(state, width=width, selected=selected)
    ]


def _battle_has_player(row: Dict[str, Any], username: str) -> bool:
    if not username:
        return False
    for side in ("attacker", "defender"):
        names = [name.strip() for name in str(row.get(side) or "").split(" & ")]
        if username in names:
            return True
    return False


def _battle_participants_text(row: Dict[str, Any]) -> str:
    attacker = str(row.get("attacker") or "?")
    defender = str(row.get("defender") or "?")
    result = str(row.get("result") or "").lower()
    if result == "a":
        return f"✔ {attacker} VS ✘ {defender}"
    if result == "r":
        return f"✘ {attacker} VS ✔ {defender}"
    return f"{attacker} VS {defender}"


def render_battles_lines(
    state: DashboardState,
    *,
    order: str = "units",
    sort: str = "desc",
    width: int = 110,
    selected: int = 0,
) -> List[FrameLine]:
    """Render the battle-hall tab from the daemon's snapshot."""

    width = max(40, width)
    lines: List[FrameLine] = []

    def add(text: str, style: str = "normal") -> None:
        lines.append(FrameLine(truncate(text, width), style))

    payload = state.battle_log or {}
    label = BATTLE_ORDER_NAMES.get(order, order)
    add(f"战役 · 名人堂 · {label}榜", "title")
    if not payload:
        add("尚无战役数据：守护首轮会采集（--battles-every，默认 120 轮）", "warn")
        add("命令示例：run_forever --no-verify-ssl --battles-every 120", "muted")
        return lines

    captured = _parse_time(payload.get("captured_at"))
    age = (
        fmt_age(max(0.0, (state.now - captured).total_seconds()))
        if captured is not None
        else "-"
    )
    source = payload.get("source") or "game.php?page=battleHall"
    add(f"来源 {source} · 快照 {age}", "muted")
    add(_rule(width), "muted")

    errors = payload.get("errors") or []
    if errors:
        add("采集错误", "section")
        for error in errors[:3]:
            add(f"  {error.get('board')}: {error.get('error')}", "warn")

    board = (payload.get("boards") or {}).get(f"{order}:{sort}")
    if not isinstance(board, dict):
        available = ", ".join(sorted((payload.get("boards") or {}).keys())) or "无"
        add(f"这个排序还没有采集到（已采集：{available}）", "warn")
        add("守护每 --battles-every 轮重试一次。", "muted")
        return lines

    rows = [row for row in (board.get("rows") or []) if isinstance(row, dict)]
    if not rows:
        add("（榜上暂无战役）", "muted")
        return lines

    account = (state.snapshot or {}).get("account") or {}
    me = account.get("username") or ""
    mine = sum(1 for row in rows if _battle_has_player(row, me))
    summary = f"{len(rows)} 场"
    if me:
        summary += f" · ★ 我的战役 {mine} 场"
    summary += " · ✔ 胜者 · ✘ 败者"
    add(summary, "muted")

    selected = max(0, min(int(selected), len(rows) - 1))
    header = (
        f"  {pad('排名', 6)}{pad('参战方', 40)}"
        f"{pad('日期', 12)}{pad('单位积分', 10)}"
    )
    add(header, "section")

    for index, row in enumerate(rows):
        mine_row = _battle_has_player(row, me)
        marker = "▸ " if index == selected else ("★ " if mine_row else "  ")
        text = (
            f"{marker}{pad(str(row.get('rank') or index + 1), 6)}"
            f"{pad(_battle_participants_text(row), 40)}"
            f"{pad(fmt_battle_time(row, now=state.now), 12)}"
            f"{pad(fmt_number(row.get('units')), 10)}"
        )
        if index == selected:
            style = "selected"
        elif mine_row:
            style = "ok"
        else:
            style = "normal"
        add(text, style)

    return lines


def battle_rows(payload: Dict[str, Any], order: str, sort: str) -> List[Dict[str, Any]]:
    """Return the battle rows of one collected ordering (possibly empty)."""

    board = (payload.get("boards") or {}).get(f"{order}:{sort}")
    if not isinstance(board, dict):
        return []
    return [row for row in (board.get("rows") or []) if isinstance(row, dict)]


def render_battle_detail(
    row: Dict[str, Any], *, base_url: str = "", width: int = 110
) -> List[str]:
    """Render one battle-hall entry with its report link."""

    width = max(40, width)
    attacker = str(row.get("attacker") or "?")
    defender = str(row.get("defender") or "?")
    result = str(row.get("result") or "").lower()
    lines = [truncate(f"{pad('参战方', 10)}{attacker} VS {defender}", width)]
    if result == "a":
        lines.append(truncate(f"{pad('胜者', 10)}{attacker}", width))
        lines.append(truncate(f"{pad('败者', 10)}{defender}", width))
    elif result == "r":
        lines.append(truncate(f"{pad('胜者', 10)}{defender}", width))
        lines.append(truncate(f"{pad('败者', 10)}{attacker}", width))
    else:
        lines.append(truncate(f"{pad('结果', 10)}未知（页面未标注胜者）", width))

    units = fmt_number(row.get("units"))
    lines.append(truncate(f"{pad('单位积分', 10)}{units}", width))
    date = str(row.get("date") or "").strip()
    if date:
        lines.append(truncate(f"{pad('日期', 10)}{date}", width))

    report_id = str(row.get("report_id") or "").strip()
    if report_id:
        path = f"game.php?page=raport&mode=battlehall&raport={report_id}"
        url = urljoin(base_url, path) if base_url else path
        lines.append(pad("报告", 10))
        lines.extend(truncate(part, width) for part in wrap_text(url, width))
    return lines


def render_battles_text(
    state: DashboardState,
    *,
    order: str = "units",
    sort: str = "desc",
    width: int = 110,
    selected: int = 0,
) -> List[str]:
    return [
        line.text.rstrip()
        for line in render_battles_lines(
            state, order=order, sort=sort, width=width, selected=selected
        )
    ]


def render_battle_text(
    state: DashboardState,
    *,
    order: str = "units",
    sort: str = "desc",
    index: int = 0,
    width: int = 110,
) -> List[str]:
    """Plain-text frame for one battle; falls back to the list without data."""

    payload = state.battle_log or {}
    rows = battle_rows(payload, order, sort)
    if not rows:
        return render_battles_text(state, order=order, sort=sort, width=width)
    index = max(0, min(int(index), len(rows) - 1))
    row = rows[index]
    header = (
        f"战役 · 第 {row.get('rank') or index + 1} 名"
        f" · {row.get('date') or fmt_battle_time(row, now=state.now)}"
    )
    return [truncate(header, width)] + render_battle_detail(
        row, base_url=str(payload.get("base_url") or ""), width=width
    )


def render_text(
    state: DashboardState, *, width: int = 110, log_lines: int = 8, event_lines: int = 4
) -> List[str]:
    """Plain-text frame for ``--once``, logs and golden tests."""

    return [
        line.text.rstrip()
        for line in render_lines(
            state, width=width, log_lines=log_lines, event_lines=event_lines
        )
    ]

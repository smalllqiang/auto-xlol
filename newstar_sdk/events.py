"""Turn two state snapshots into a list of events.

This is the bridge between observation and reaction: the snapshot says "what
is", the diff says "what changed".  A long running loop can poll cheaply and
only act when an event matters (a new battle report, a queue going idle, an
energy deficit).

The function is pure: it takes two snapshot dicts and returns dataclasses.  It
does not read files or talk to the game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Message categories that usually require attention.
ALERT_MESSAGE_CATEGORIES = frozenset({0, 3})  # espionage, battle

QUEUE_KINDS: Tuple[str, ...] = ("building", "research", "shipyard")

#: Severity ordering, handy for filtering.
SEVERITIES = ("info", "notice", "warning")


@dataclass(frozen=True)
class Event:
    kind: str
    severity: str = "info"
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity, **self.data}


def _messages(snapshot: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    counters = (snapshot.get("messages") or {}).get("counters") or []
    return {int(item["category"]): item for item in counters}


def _queue(snapshot: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    return list((snapshot.get("queues") or {}).get(kind) or [])


def _queue_keys(entries: Sequence[Dict[str, Any]]) -> List[Tuple[Any, Any, Any]]:
    return [
        (entry.get("element_id"), entry.get("target_level"), entry.get("amount"))
        for entry in entries
    ]


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _message_events(
    previous: Dict[str, Any], current: Dict[str, Any]
) -> List[Event]:
    events: List[Event] = []
    before = _messages(previous)
    after = _messages(current)

    for category, item in after.items():
        old = before.get(category, {"unread": 0})
        delta = int(item.get("unread", 0)) - int(old.get("unread", 0))
        if delta <= 0:
            continue
        severity = "notice" if category in ALERT_MESSAGE_CATEGORIES else "info"
        events.append(
            Event(
                "message_new",
                severity,
                {
                    "category": category,
                    "name": item.get("name", ""),
                    "new": delta,
                    "unread": int(item.get("unread", 0)),
                    "total": int(item.get("total", 0)),
                },
            )
        )
    return events


def _queue_events(previous: Dict[str, Any], current: Dict[str, Any]) -> List[Event]:
    events: List[Event] = []
    for kind in QUEUE_KINDS:
        before = _queue(previous, kind)
        after = _queue(current, kind)
        before_keys = _queue_keys(before)
        after_keys = _queue_keys(after)

        if before and not after:
            # The queue going idle already implies the last item finished;
            # do not also emit a per-item completion event.
            events.append(Event("queue_empty", "notice", {"queue": kind}))
            continue

        for key in before_keys:
            if key in after_keys:
                continue
            element_id, target_level, amount = key
            events.append(
                Event(
                    "queue_item_finished",
                    "info",
                    {
                        "queue": kind,
                        "element_id": element_id,
                        "target_level": target_level,
                        "amount": amount,
                    },
                )
            )

        for entry in after:
            key = (entry.get("element_id"), entry.get("target_level"), entry.get("amount"))
            if key in before_keys:
                continue
            events.append(
                Event(
                    "queue_item_added",
                    "info",
                    {
                        "queue": kind,
                        "element_id": key[0],
                        "target_level": key[1],
                        "amount": key[2],
                    },
                )
            )
    return events


def _energy_events(previous: Dict[str, Any], current: Dict[str, Any]) -> List[Event]:
    before = (previous.get("energy") or {}).get("available")
    after = (current.get("energy") or {}).get("available")
    if before is None or after is None:
        return []
    if before >= 0 > after:
        return [Event("energy_deficit", "warning", {"available": after})]
    if before < 0 <= after:
        return [Event("energy_recovered", "info", {"available": after})]
    return []


def _storage_events(
    previous: Dict[str, Any], current: Dict[str, Any], threshold: float
) -> List[Event]:
    events: List[Event] = []
    before = (previous.get("resources") or {}).get("usage") or {}
    after = (current.get("resources") or {}).get("usage") or {}
    for resource_id, ratio in after.items():
        if ratio >= threshold > float(before.get(resource_id, 0.0)):
            events.append(
                Event(
                    "storage_full",
                    "notice",
                    {"resource": resource_id, "usage": ratio},
                )
            )
    return events


def _planet_events(previous: Dict[str, Any], current: Dict[str, Any]) -> List[Event]:
    events: List[Event] = []
    before = {item.get("id") for item in previous.get("planets") or []}
    after = {item.get("id") for item in current.get("planets") or []}
    for planet_id in sorted(after - before, key=str):
        events.append(Event("planet_added", "notice", {"planet_id": planet_id}))
    for planet_id in sorted(before - after, key=str):
        events.append(Event("planet_lost", "warning", {"planet_id": planet_id}))

    before_planet = (previous.get("planet") or {}).get("id")
    after_planet = (current.get("planet") or {}).get("id")
    if before_planet != after_planet:
        events.append(
            Event(
                "planet_switched",
                "info",
                {"from": before_planet, "to": after_planet},
            )
        )
    return events


def _research_events(
    previous: Dict[str, Any], current: Dict[str, Any]
) -> List[Event]:
    events: List[Event] = []
    before = (previous.get("research") or {}).get("levels") or {}
    after = (current.get("research") or {}).get("levels") or {}
    for element_id, level in after.items():
        old = int(before.get(element_id, 0))
        if int(level) > old:
            events.append(
                Event(
                    "research_completed",
                    "info",
                    {"element_id": element_id, "from": old, "to": int(level)},
                )
            )
    return events


def _gap_events(
    previous: Dict[str, Any], current: Dict[str, Any], gap_seconds: float
) -> List[Event]:
    before = _parse_time(previous.get("captured_at"))
    after = _parse_time(current.get("captured_at"))
    if before is None or after is None:
        return []
    seconds = (after - before).total_seconds()
    if seconds > gap_seconds:
        return [Event("snapshot_gap", "warning", {"seconds": int(seconds)})]
    return []


def diff_snapshots(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
    *,
    storage_threshold: float = 0.95,
    gap_seconds: float = 3600.0,
) -> List[Event]:
    """Return the events that happened between ``previous`` and ``current``."""

    if not previous:
        return []

    events: List[Event] = []
    events.extend(_message_events(previous, current))
    events.extend(_queue_events(previous, current))
    events.extend(_energy_events(previous, current))
    events.extend(_storage_events(previous, current, storage_threshold))
    events.extend(_planet_events(previous, current))
    events.extend(_research_events(previous, current))
    events.extend(_gap_events(previous, current, gap_seconds))
    return events

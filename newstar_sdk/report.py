"""Summarise a series of state snapshots into trend statistics.

Input is the list of snapshot dicts produced by
:func:`newstar_sdk.snapshot.build_snapshot` (or read back from a JSONL file);
output is a plain dict with resource deltas, per-hour rates, queue occupancy,
capture gaps and event counts.  Pure computation -- no file or network access.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence

RESOURCE_IDS = ("901", "902", "903")
QUEUE_KINDS = ("building", "research", "shipyard")


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _median_interval(times: Sequence[datetime]) -> Optional[float]:
    if len(times) < 2:
        return None
    deltas = [
        (later - earlier).total_seconds()
        for earlier, later in zip(times, times[1:])
        if later > earlier
    ]
    return median(deltas) if deltas else None


def _min_interval(times: Sequence[datetime]) -> Optional[float]:
    if len(times) < 2:
        return None
    deltas = [
        (later - earlier).total_seconds()
        for earlier, later in zip(times, times[1:])
        if later > earlier
    ]
    return min(deltas) if deltas else None


def _gaps(
    times: Sequence[datetime], threshold: float
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for earlier, later in zip(times, times[1:]):
        seconds = (later - earlier).total_seconds()
        if seconds > threshold:
            gaps.append(
                {
                    "from": earlier.isoformat(),
                    "to": later.isoformat(),
                    "seconds": int(seconds),
                }
            )
    return gaps


def _resource_stats(snapshots: Sequence[Dict[str, Any]], hours: float) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for resource_id in RESOURCE_IDS:
        values = [
            float((snapshot.get("resources") or {}).get("amounts", {}).get(resource_id, 0.0))
            for snapshot in snapshots
        ]
        if not values:
            continue
        delta = values[-1] - values[0]
        result[resource_id] = {
            "first": values[0],
            "last": values[-1],
            "delta": delta,
            "per_hour": delta / hours if hours > 0 else 0.0,
            "min": min(values),
            "max": max(values),
        }
    return result


def _production_stats(snapshot: Dict[str, Any]) -> Dict[str, float]:
    resources = (snapshot.get("production") or {}).get("resources") or {}
    return {
        resource_id: float(resources.get(resource_id, {}).get("total", 0.0))
        for resource_id in RESOURCE_IDS
        if resource_id in resources
    }


def _queue_stats(snapshots: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for kind in QUEUE_KINDS:
        lengths = [
            len(((snapshot.get("queues") or {}).get(kind)) or [])
            for snapshot in snapshots
        ]
        if not lengths:
            continue
        busy = sum(1 for length in lengths if length > 0)
        result[kind] = {
            "occupancy": round(busy / len(lengths), 4),
            "max": max(lengths),
            "last": lengths[-1],
        }
    return result


def _research_progress(snapshots: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    first = (snapshots[0].get("research") or {}).get("levels") or {}
    last = (snapshots[-1].get("research") or {}).get("levels") or {}
    completed = []
    for element_id, level in last.items():
        old = int(first.get(element_id, 0))
        if int(level) > old:
            completed.append({"element_id": element_id, "from": old, "to": int(level)})
    return completed


def _event_stats(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    by_kind: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    total = 0
    for event in events:
        total += 1
        kind = str(event.get("kind", "unknown"))
        severity = str(event.get("severity", "info"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
    return {"total": total, "by_kind": by_kind, "by_severity": by_severity}


def summarize_snapshots(
    snapshots: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]] = (),
    *,
    gap_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Summarise ``snapshots`` (oldest first) and ``events``."""

    if not snapshots:
        return {
            "count": 0,
            "first": None,
            "last": None,
            "span_seconds": 0,
            "median_interval": None,
            "gaps": [],
            "resources": {},
            "production": {},
            "queues": {},
            "messages": {},
            "research": {"completed": []},
            "errors": 0,
            "events": _event_stats(events),
        }

    times = [time for time in (_parse_time(item.get("captured_at")) for item in snapshots) if time]
    first_time = times[0] if times else None
    last_time = times[-1] if times else None
    span_seconds = (last_time - first_time).total_seconds() if first_time and last_time else 0.0
    hours = span_seconds / 3600.0

    interval = _median_interval(times)
    # Use the fastest observed cadence as the reference: the median can be
    # skewed by a single long outage, which would hide that very outage.
    cadence = _min_interval(times) or interval
    threshold = gap_seconds if gap_seconds is not None else max(3.0 * (cadence or 0.0), 900.0)

    unread = [
        int((snapshot.get("messages") or {}).get("unread", 0)) for snapshot in snapshots
    ]

    return {
        "count": len(snapshots),
        "first": first_time.isoformat() if first_time else None,
        "last": last_time.isoformat() if last_time else None,
        "span_seconds": int(span_seconds),
        "median_interval": interval,
        "gaps": _gaps(times, threshold) if len(times) > 1 else [],
        "resources": _resource_stats(snapshots, hours),
        "production": _production_stats(snapshots[-1]),
        "queues": _queue_stats(snapshots),
        "messages": {
            "unread_last": unread[-1] if unread else 0,
            "unread_max": max(unread) if unread else 0,
        },
        "research": {"completed": _research_progress(snapshots)},
        "errors": sum(1 for snapshot in snapshots if snapshot.get("errors")),
        "events": _event_stats(events),
    }

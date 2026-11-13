#!/usr/bin/env python3
"""Report trends from the JSONL snapshots written by ``collect_state.py``.

The window ends at the newest snapshot (not at wall clock), so the report also
works on archived files.

Examples::

    .venv/bin/python scripts/state_report.py --hours 24
    .venv/bin/python scripts/state_report.py --file state/snapshots.jsonl --hours 6 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from newstar_sdk.report import summarize_snapshots


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", default="state/snapshots.jsonl", help="snapshot JSONL file")
    parser.add_argument(
        "--events",
        default=None,
        help="events JSONL file (default: events.jsonl next to --file)",
    )
    parser.add_argument("--hours", type=float, default=24.0, help="window length in hours")
    parser.add_argument("--json", action="store_true", help="print the raw summary as JSON")
    parser.add_argument(
        "--gap-seconds",
        type=float,
        default=None,
        help="override the capture-gap threshold (default: max(3 x median interval, 15 min))",
    )
    return parser.parse_args(argv)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
    return items


def _time(item: Dict[str, Any], key: str = "captured_at") -> Optional[datetime]:
    value = item.get(key)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def filter_window(
    items: List[Dict[str, Any]], hours: float, key: str = "captured_at"
) -> List[Dict[str, Any]]:
    """Keep the last ``hours`` of records, anchored at the newest timestamp."""

    stamped = [(item, _time(item, key)) for item in items]
    stamped = [(item, when) for item, when in stamped if when is not None]
    if not stamped:
        return []
    end = max(when for _, when in stamped)
    start = end - timedelta(hours=hours)
    return [item for item, when in stamped if when >= start]


def _format_number(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value:,.0f}"
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def render(summary: Dict[str, Any]) -> str:
    if not summary["count"]:
        return "no snapshots in the selected window"

    lines: List[str] = []
    span = summary["span_seconds"]
    interval = summary["median_interval"]
    interval_text = f"{interval:.0f}s" if interval is not None else "-"
    lines.append(
        f"window  {summary['first']} -> {summary['last']}  "
        f"({summary['count']} snapshots, median interval {interval_text})"
    )

    for resource_id, label in (("901", "metal"), ("902", "crystal"), ("903", "deuterium")):
        stats = (summary.get("resources") or {}).get(resource_id)
        if not stats:
            continue
        lines.append(
            f"  {label:<9} {_format_number(stats['first'])} -> {_format_number(stats['last'])}  "
            f"delta {stats['delta']:+,.0f}  ({stats['per_hour']:+,.0f}/h)"
        )

    production = summary.get("production") or {}
    if production:
        lines.append(
            "  prod/h   "
            + "  ".join(
                f"{label} {_format_number(production.get(rid, 0))}"
                for rid, label in (("901", "metal"), ("902", "crystal"), ("903", "deuterium"))
            )
        )

    queues = summary.get("queues") or {}
    if queues:
        parts = []
        for kind in ("building", "research", "shipyard"):
            stats = queues.get(kind)
            if not stats:
                continue
            parts.append(f"{kind} {stats['occupancy'] * 100:.0f}% (max {stats['max']})")
        lines.append("  queues   " + "  ".join(parts))

    messages = summary.get("messages") or {}
    lines.append(
        f"  unread   {messages.get('unread_last', 0)} (peak {messages.get('unread_max', 0)})"
    )

    completed = (summary.get("research") or {}).get("completed") or []
    if completed:
        lines.append(
            "  research "
            + ", ".join(f"{item['element_id']}: {item['from']}->{item['to']}" for item in completed)
        )

    errors = summary.get("errors", 0)
    lines.append(f"  errors   {errors} snapshot(s) with section errors")

    gaps = summary.get("gaps") or []
    if gaps:
        formatted = ", ".join(_format_duration(gap["seconds"]) for gap in gaps)
        lines.append(f"  gaps     {len(gaps)} ({formatted})")

    events = summary.get("events") or {}
    if events.get("total"):
        kinds = ", ".join(f"{kind}×{count}" for kind, count in events["by_kind"].items())
        severities = ", ".join(
            f"{severity}:{count}" for severity, count in events["by_severity"].items()
        )
        lines.append(f"  events   {events['total']} ({kinds}); severity {severities}")

    if span:
        lines.append(f"  span     {_format_duration(span)}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    path = Path(args.file)
    snapshots = filter_window(load_jsonl(path), args.hours)
    if not snapshots:
        print(f"no snapshots in {path} for the last {args.hours:g}h", file=sys.stderr)
        return 2

    events_path = Path(args.events) if args.events else path.with_name("events.jsonl")
    events = (
        filter_window(load_jsonl(events_path), args.hours, key="at")
        if events_path.exists()
        else []
    )

    summary = summarize_snapshots(snapshots, events, gap_seconds=args.gap_seconds)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

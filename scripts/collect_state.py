#!/usr/bin/env python3
"""Append one read-only state snapshot to a JSONL file.

This is P0 step 2/3 of the automation project: turn the SDK getters into a
cheap, repeatable observation.  One run = one line of JSON, with per-section
error capture, so a transient broken page does not lose the whole record.

Run it from cron / systemd timer, e.g. every 5 minutes::

    */5 * * * * cd /path/to/auto-xlol && .venv/bin/python scripts/collect_state.py

It only reads; it never posts and never changes account state.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import List, Optional, Sequence

from local_config import load_config

from newstar_sdk import NewStarClient
from newstar_sdk.events import diff_snapshots
from newstar_sdk.snapshot import build_snapshot


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", help="path to .env or passwd.txt (default: autodetect)")
    parser.add_argument("--base-url", help="override game base url")
    parser.add_argument("--username", help="override username")
    parser.add_argument("--password", help="override password")
    parser.add_argument("--universe", help="override universe name/id")
    parser.add_argument("--no-verify-ssl", action="store_true", help="skip TLS verification")
    parser.add_argument("--interval", type=float, default=0.4, help="seconds between requests")
    parser.add_argument(
        "--out",
        default="state/snapshots.jsonl",
        help="JSONL file to append to",
    )
    parser.add_argument(
        "--print",
        dest="print_json",
        action="store_true",
        help="print the snapshot to stdout instead of a summary line",
    )
    parser.add_argument("--no-research", action="store_true", help="skip the research page")
    parser.add_argument("--no-queues", action="store_true", help="skip the three queue pages")
    parser.add_argument("--no-fleets", action="store_true", help="skip the fleet table")
    parser.add_argument("--no-messages", action="store_true", help="skip the message counters")
    parser.add_argument("--no-bonuses", action="store_true", help="skip the bonus summary")
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="do not diff against the previous snapshot",
    )
    parser.add_argument(
        "--events-out",
        default=None,
        help="events JSONL file (default: events.jsonl next to --out)",
    )
    return parser.parse_args(argv)


def _last_snapshot(path: Path) -> Optional[dict]:
    """Read the last complete JSON line of a (possibly large) JSONL file."""

    if not path.exists():
        return None
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 1_000_000))
        data = handle.read()
    for line in reversed([line for line in data.splitlines() if line.strip()]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def _append(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def _summary(snapshot: dict) -> str:
    planet = snapshot.get("planet") or {}
    resources = (snapshot.get("resources") or {}).get("amounts", {})
    queues = snapshot.get("queues") or {}
    queue_counts = {key: len(value) for key, value in queues.items()}
    messages = snapshot.get("messages") or {}
    research = snapshot.get("research") or {}
    production = (snapshot.get("production") or {}).get("resources") or {}

    parts: List[str] = [
        f"{planet.get('name', '?')} {planet.get('coordinates', '?')}",
        f"metal={resources.get('901', 0):.0f} crystal={resources.get('902', 0):.0f} "
        f"deut={resources.get('903', 0):.0f} energy={resources.get('911', 0):.0f}",
        "prod="
        + "/".join(
            f"{production.get(str(rid), {}).get('total', 0):.0f}"
            for rid in (901, 902, 903)
        ),
        f"queues={queue_counts or '-'}",
        f"fleets={len(snapshot.get('fleets') or [])}",
        f"unread={messages.get('unread', 0)}",
        f"expedition_slots={research.get('expedition_slots', '-')}",
    ]
    errors = snapshot.get("errors") or []
    if errors:
        parts.append("errors=" + ", ".join(item["section"] for item in errors))
    return " | ".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    client = NewStarClient(
        config.base_url,
        config.username,
        config.password,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=config.interval,
    )
    try:
        client.login()
        snapshot = build_snapshot(
            client,
            include_research=not args.no_research,
            include_queues=not args.no_queues,
            include_fleets=not args.no_fleets,
            include_messages=not args.no_messages,
            include_bonuses=not args.no_bonuses,
        )
        if args.print_json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
            return 0

        path = Path(args.out)
        events = []
        if not args.no_events:
            events = diff_snapshots(_last_snapshot(path), snapshot)
        _append(path, snapshot)

        events_path = Path(args.events_out) if args.events_out else path.with_name("events.jsonl")
        for event in events:
            _append(events_path, {"at": snapshot["captured_at"], **event.as_dict()})

        print(f"{snapshot['captured_at']} -> {path}")
        print(_summary(snapshot))
        if events:
            kinds = ", ".join(f"{item.kind}" for item in events)
            print(f"events: {len(events)} -> {events_path} ({kinds})")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())

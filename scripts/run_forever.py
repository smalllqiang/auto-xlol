#!/usr/bin/env python3
"""Unattended loop: plan, optionally act, journal, sleep.

Safety properties:

* single instance -- a lock file with the pid; a stale lock is taken over;
* kill switch -- ``state/STOP`` stops the loop at the next tick;
* dry run by default -- ``--execute`` plus ``--allow`` is required to mutate;
* exponential backoff -- consecutive failures widen the delay up to a cap;
* graceful shutdown on SIGINT / SIGTERM.

Each cycle appends one line to the journal.  Every ``--snapshot-every`` cycles
it also writes a state snapshot and the diffed events (0 disables).
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from local_config import load_config

from autoplay.daemon import DaemonConfig, backoff_delay, run_cycle
from autoplay.expedition import build_expedition_report
from autoplay.expedition_cycle import expedition_due, run_expedition_cycle
from autoplay.journal import append_jsonl
from newstar_sdk import NewStarClient
from newstar_sdk.constants import BUILDINGS, TECHNOLOGIES
from newstar_sdk.events import diff_snapshots
from newstar_sdk.exceptions import NewStarError
from newstar_sdk.snapshot import build_snapshot

_NAME_INDEX: Dict[str, int] = {}
for _element_id, _element in BUILDINGS.items():
    _NAME_INDEX.setdefault(_element.name_zh, int(_element_id))
for _element_id, _element in TECHNOLOGIES.items():
    _NAME_INDEX.setdefault(_element.name_zh, int(_element_id))


def resolve_allow(values: Sequence[str]) -> List[int]:
    resolved: List[int] = []
    for value in values:
        text = str(value).strip()
        if text.isdigit():
            resolved.append(int(text))
        elif text in _NAME_INDEX:
            resolved.append(_NAME_INDEX[text])
        else:
            raise SystemExit(f"unknown element in --allow: {value!r}")
    return resolved


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
    parser.add_argument("--interval", type=float, default=300.0, help="seconds between cycles")
    parser.add_argument("--execute", action="store_true", help="actually act (default: dry run)")
    parser.add_argument(
        "--allow", action="append", default=[], metavar="ID|NAME", help="repeatable allow-list"
    )
    parser.add_argument("--max-cost", type=float, default=None, help="per-action budget")
    parser.add_argument("--max-cycles", type=int, default=0, help="stop after N cycles (0 = never)")
    parser.add_argument("--max-failures", type=int, default=10, help="exit after N consecutive errors")
    parser.add_argument("--backoff-base", type=float, default=30.0)
    parser.add_argument("--backoff-max", type=float, default=900.0)
    parser.add_argument("--journal", default="state/journal.jsonl")
    parser.add_argument("--snapshots", default="state/snapshots.jsonl")
    parser.add_argument("--events", default="state/events.jsonl")
    parser.add_argument(
        "--pause-on-alert",
        action="store_true",
        help="skip execution for a cycle when an alert event is seen",
    )
    parser.add_argument(
        "--rotate-mb",
        type=float,
        default=10.0,
        help="rotate journal/snapshot/event files above this size (0 disables)",
    )
    parser.add_argument(
        "--snapshot-every",
        type=int,
        default=12,
        help="write a full snapshot every N cycles (0 disables)",
    )
    parser.add_argument("--stop-file", default="state/STOP")
    parser.add_argument("--lock-file", default="state/daemon.lock")
    parser.add_argument("--once", action="store_true", help="run exactly one cycle")
    parser.add_argument(
        "--expedition",
        action="store_true",
        help="run the expedition cycle every --expedition-every cycles (requires --execute)",
    )
    parser.add_argument(
        "--expedition-allow",
        action="append",
        default=[],
        metavar="ID",
        help="repeatable ship id allow-list for expedition dispatch/build",
    )
    parser.add_argument(
        "--expedition-every",
        type=int,
        default=12,
        help="run expedition cycle every N plan cycles (default: 12)",
    )
    parser.add_argument(
        "--expedition-max-dispatch",
        type=int,
        default=1,
        help="max expedition groups per cycle (default: 1)",
    )
    parser.add_argument(
        "--expedition-auto-build",
        action="store_true",
        help="build one recommended batch when a free slot has no group",
    )
    parser.add_argument("--expedition-build-count", type=int, default=1)
    parser.add_argument("--expedition-build-timeout", type=float, default=300.0)
    parser.add_argument("--expedition-ship", type=int, help="force a ship id for auto-build")
    parser.add_argument("--expedition-target", help="override expedition target coordinates")
    parser.add_argument("--expedition-speed", type=int, default=10)
    parser.add_argument("--expedition-stay-time", type=int, default=1)
    return parser.parse_args(argv)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lock(path: Path) -> int:
    """Create a pid lock file; take over a stale one."""

    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except FileExistsError:
            try:
                pid = int(path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = 0
            if pid and _alive(pid):
                raise SystemExit(
                    f"another daemon is already running (pid {pid})"
                ) from None
            path.unlink(missing_ok=True)
    raise SystemExit(f"could not acquire lock {path}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.allow:
        print("--execute requires at least one --allow element", file=sys.stderr)
        return 2

    if args.expedition and not args.execute:
        print("--expedition requires --execute; expedition scheduling disabled", file=sys.stderr)
        args.expedition = False
    if args.expedition and args.expedition_every < 1:
        print("--expedition-every must be at least 1", file=sys.stderr)
        return 2
    if args.expedition and not args.expedition_allow:
        print("--expedition requires at least one --expedition-allow ship id", file=sys.stderr)
        return 2
    try:
        expedition_allowed = [int(item) for item in args.expedition_allow] if args.expedition else []
    except ValueError:
        print("--expedition-allow values must be ship element ids", file=sys.stderr)
        return 2

    stop_file = Path(args.stop_file)
    lock_path = Path(args.lock_file)
    lock_fd = acquire_lock(lock_path)

    config = load_config(args)
    if not config.verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    daemon_config = DaemonConfig(
        dry_run=not args.execute,
        allowed_elements=resolve_allow(args.allow) if args.allow else None,
        max_cost_equivalent=args.max_cost,
        interval_seconds=args.interval,
        backoff_base=args.backoff_base,
        backoff_max=args.backoff_max,
        max_failures=args.max_failures,
        pause_on_alert=args.pause_on_alert,
    )
    rotate_bytes = int(max(0.0, args.rotate_mb) * 1024 * 1024)

    stop = {"requested": False}

    def handle_signal(signum, frame):  # noqa: ANN001, ARG001
        stop["requested"] = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    client = NewStarClient(
        config.base_url,
        config.username,
        config.password,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=0.4,
    )
    journal_path = Path(args.journal)
    snapshots_path = Path(args.snapshots)
    events_path = Path(args.events)
    previous_snapshot: Optional[Dict[str, Any]] = None
    previous_counters: Optional[Dict[int, Dict[str, Any]]] = None
    failures = 0
    cycles = 0
    max_cycles = 1 if args.once else args.max_cycles

    try:
        while not stop["requested"]:
            if stop_file.exists():
                print(f"stop file present ({stop_file}); exiting", file=sys.stderr)
                break
            if max_cycles and cycles >= max_cycles:
                break

            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                if failures == 0:
                    client.ensure_login()
                result = run_cycle(
                    client, daemon_config, previous_counters=previous_counters
                )
                if result.counters:
                    previous_counters = result.counters
                failures = 0
                cycles += 1
                entry = {
                    "at": now,
                    "kind": "cycle",
                    "mode": "execute" if args.execute else "dry_run",
                    **result.as_dict(),
                }
                append_jsonl(journal_path, entry, max_bytes=rotate_bytes)
                action = result.action["name"] if result.action else "-"
                print(
                    f"[{now}] {result.phase} | {result.status}: {action} | "
                    f"queues={result.queues}",
                    flush=True,
                )
                for alert in result.alerts:
                    print(f"    ALERT {alert['message']}", flush=True)

                if args.expedition and expedition_due(cycles, args.expedition_every):
                    try:
                        expedition_result = run_expedition_cycle(
                            client,
                            dry_run=False,
                            allowed_elements=expedition_allowed,
                            max_dispatch=args.expedition_max_dispatch,
                            speed=args.expedition_speed,
                            stay_time=args.expedition_stay_time,
                            target=args.expedition_target,
                            auto_build=args.expedition_auto_build,
                            build_count=args.expedition_build_count,
                            build_timeout=args.expedition_build_timeout,
                            report_loader=lambda current: build_expedition_report(
                                current, ship=args.expedition_ship
                            ),
                        )
                        append_jsonl(
                            journal_path,
                            {
                                "at": now,
                                "kind": "expedition",
                                "mode": "execute",
                                **expedition_result.as_dict(),
                            },
                            max_bytes=rotate_bytes,
                        )
                        print(
                            f"    expedition {expedition_result.status}: "
                            f"{expedition_result.reason}",
                            flush=True,
                        )
                    except NewStarError as exc:
                        append_jsonl(
                            journal_path,
                            {
                                "at": now,
                                "kind": "expedition_error",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                            max_bytes=rotate_bytes,
                        )
                        print(
                            f"[{now}] expedition error: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )

                if args.snapshot_every and cycles % args.snapshot_every == 0:
                    snapshot = build_snapshot(client)
                    events = diff_snapshots(previous_snapshot, snapshot)
                    append_jsonl(snapshots_path, snapshot, max_bytes=rotate_bytes)
                    for event in events:
                        append_jsonl(
                            events_path,
                            {"at": snapshot["captured_at"], **event.as_dict()},
                            max_bytes=rotate_bytes,
                        )
                    previous_snapshot = snapshot
                    print(f"    snapshot written ({len(events)} events)", flush=True)
            except NewStarError as exc:
                failures += 1
                append_jsonl(
                    journal_path,
                    {
                        "at": now,
                        "kind": "error",
                        "failures": failures,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    max_bytes=rotate_bytes,
                )
                print(f"[{now}] error ({failures}): {exc}", file=sys.stderr, flush=True)
                if failures >= daemon_config.max_failures:
                    print("too many consecutive failures; exiting", file=sys.stderr)
                    return 1

            if max_cycles and cycles >= max_cycles:
                break

            delay = (
                daemon_config.interval_seconds
                if failures == 0
                else backoff_delay(
                    failures, daemon_config.backoff_base, daemon_config.backoff_max
                )
            )
            deadline = time.monotonic() + delay
            while not stop["requested"] and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))
        return 0
    finally:
        client.close()
        try:
            os.close(lock_fd)
        finally:
            lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())

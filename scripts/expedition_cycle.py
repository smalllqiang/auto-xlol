#!/usr/bin/env python3
"""Preview or run one bounded multi-slot expedition cycle.

Default mode previews the first available group.  Real dispatch needs
``--execute`` plus a ship allow-list and is capped by ``--max-dispatch``.
``--auto-build`` may build one recommended batch when a free slot has no group.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional, Sequence

from local_config import load_config

from autoplay import build_expedition_report, run_expedition_cycle
from newstar_sdk import NewStarClient


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
    parser.add_argument("--interval", type=float, default=0.3, help="seconds between requests")
    parser.add_argument("--target", help="target coordinates (default: current system position 16)")
    parser.add_argument("--speed", type=int, default=10, help="fleet speed 1..10")
    parser.add_argument("--stay-time", type=int, default=1, help="expedition stay option")
    parser.add_argument("--max-dispatch", type=int, default=1, help="max groups in this cycle")
    parser.add_argument(
        "--auto-build",
        action="store_true",
        help="build one batch of the recommended ship when no group is available",
    )
    parser.add_argument("--build-count", type=int, default=1, help="ships to build in auto-build")
    parser.add_argument(
        "--build-timeout", type=float, default=300.0, help="seconds to wait for the ship queue"
    )
    parser.add_argument("--ship", type=int, help="force one ship id for the report/auto-build")
    parser.add_argument("--execute", action="store_true", help="actually send (default: preview)")
    parser.add_argument(
        "--allow", action="append", default=[], metavar="ID", help="repeatable ship id allow-list"
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser.parse_args(argv)


def render(result) -> str:
    lines = [
        f"结果      {result.status}",
        f"原因      {result.reason}",
    ]
    if result.build is not None:
        lines.append(
            f"自动造舰  {result.build.status}  {result.build.name}×{result.build.count}"
            f"  {result.build.reason}"
        )
    lines.append(f"派出      {len(result.dispatches)} 队")
    for index, item in enumerate(result.dispatches, 1):
        group = item.group
        ships = ""
        if group is not None:
            ships = "、".join(f"{ship_id}×{count}" for ship_id, count in group.ships.items())
        lines.append(
            f"  {index}. {item.status} -> {item.target or '-'}"
            f"  舰船 {ships or '-'}  积分 {group.score if group is not None else '-'}"
        )
        lines.append(f"     {item.reason}")
        if item.preview is not None:
            lines.append(
                f"     燃料 {item.preview.consumption if item.preview.consumption is not None else '-'}"
                f"  停留选项 {item.preview.stay_times or '-'}"
            )
        if item.dispatch is not None:
            lines.append(
                f"     到达 {item.dispatch.arrival_time or '-'}"
                f"  任务 {item.dispatch.mission} {item.dispatch.mission_name}"
            )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.allow:
        print("--execute requires at least one --allow ship id", file=sys.stderr)
        return 2
    if args.execute and Path("state/STOP").exists():
        print("state/STOP exists; refusing to dispatch", file=sys.stderr)
        return 0

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
        result = run_expedition_cycle(
            client,
            dry_run=not args.execute,
            allowed_elements=[int(item) for item in args.allow] if args.allow else None,
            max_dispatch=args.max_dispatch,
            speed=args.speed,
            stay_time=args.stay_time,
            target=args.target,
            auto_build=args.auto_build,
            build_count=args.build_count,
            build_timeout=args.build_timeout,
            report_loader=lambda current: build_expedition_report(
                current, ship=args.ship
            ),
        )
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(result))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())

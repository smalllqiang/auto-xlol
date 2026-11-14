#!/usr/bin/env python3
"""Preview or dispatch one expedition group.

Default mode only runs fleetStep1 + fleetStep2 (no fleet).  Real dispatch needs
both ``--execute`` and an explicit ``--allow`` ship id allow-list.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional, Sequence

from local_config import load_config

from autoplay import build_expedition_report, dispatch_expedition
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
    parser.add_argument("--group", type=int, default=1, help="1-based expedition group index")
    parser.add_argument("--target", help="target coordinates (default: current system position 16)")
    parser.add_argument("--speed", type=int, default=10, help="fleet speed 1..10")
    parser.add_argument("--stay-time", type=int, default=1, help="expedition stay option")
    parser.add_argument("--execute", action="store_true", help="actually send (default: preview)")
    parser.add_argument(
        "--allow", action="append", default=[], metavar="ID", help="repeatable ship id allow-list"
    )
    parser.add_argument(
        "--target-score",
        type=float,
        default=None,
        help="per-fleet no-deuterium score target (only affects the report)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser.parse_args(argv)


def render(report, result, *, execute: bool) -> str:
    group = result.group
    lines = [
        f"探险航道  {report.slots}（空闲 {report.free_slots}）",
        f"目标      {result.target or '-'}",
        f"编队      {group.index if group is not None else '-'}"
        f"  积分 {group.score:,.1f}" if group is not None else "编队      -",
        f"模式      {'execute' if execute else 'preview'}",
        f"结果      {result.status}",
        f"原因      {result.reason}",
    ]
    if group is not None and group.ships:
        ships = "、".join(
            f"{report.ship_names.get(ship_id, f'#{ship_id}')}×{count}"
            for ship_id, count in group.ships.items()
        )
        lines.append(f"舰船      {ships}")

    preview = result.preview
    if preview is not None:
        lines.append(
            f"燃料      {preview.consumption if preview.consumption is not None else '-'}"
        )
        lines.append(
            "可用任务  " + "、".join(f"{m.id}={m.name}" for m in preview.available_missions)
        )
        lines.append("停留选项  " + ("、".join(str(x) for x in preview.stay_times) or "-"))

    dispatch = result.dispatch
    if dispatch is not None:
        lines.append(f"到达时间  {dispatch.arrival_time or '-'}")
        lines.append(
            "确认任务  "
            f"{dispatch.mission} {dispatch.mission_name}".strip()
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
        report = build_expedition_report(
            client,
            target_score=(
                args.target_score if args.target_score is not None else 500_000.0
            ),
        )
        result = dispatch_expedition(
            client,
            report,
            group_index=args.group,
            target=args.target,
            speed=args.speed,
            stay_time=args.stay_time,
            dry_run=not args.execute,
            allowed_elements=[int(item) for item in args.allow] if args.allow else None,
        )
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(report, result, execute=args.execute))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())

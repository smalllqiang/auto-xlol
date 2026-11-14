#!/usr/bin/env python3
"""Build the ship recommended by the expedition report.

Default mode only shows the plan.  Real building needs ``--execute`` and an
explicit ``--allow`` ship id allow-list; each call builds at most ``--max-count``.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path
from typing import Optional, Sequence

from local_config import load_config

from autoplay import build_expedition_report, build_recommended_ships
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
    parser.add_argument("--ship", type=int, help="force one ship id instead of the report choice")
    parser.add_argument("--count", type=int, default=1, help="ships to build (default: 1)")
    parser.add_argument("--max-count", type=int, default=100, help="safety cap per call")
    parser.add_argument("--execute", action="store_true", help="actually build (default: dry-run)")
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


def render(report, result) -> str:
    rec = report.recommendation
    lines = [
        f"编队目标  {report.target or '-'}",
        f"推荐舰船  {rec.name if rec is not None else '-'}"
        f"（{rec.element_id if rec is not None else '-'}）",
    ]
    if rec is not None:
        lines.append(f"单舰积分  {rec.score_per_ship:,.1f}")
        lines.append(f"目标缺口  {report.score_gap:,.0f}")
        lines.append(f"建议数量  {rec.needed}")
        lines.append(f"前置满足  {'是' if rec.requirements_met else '否'}")
        if rec.requirements:
            lines.append("前置条件  " + "；".join(rec.requirements))
        for action in rec.blockers:
            target = action.required_level or action.target_level
            lines.append(
                f"  - {action.name} {action.current_level}->{target}"
                f"（还差 {action.steps} 级）"
            )
    lines += [
        f"模式      {'execute' if result.status == 'built' else 'preview'}",
        f"结果      {result.status}",
        f"数量      {result.count}",
        f"原因      {result.reason}",
        f"船坞队列  {result.queues.get('shipyard', '-')}",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.execute and not args.allow:
        print("--execute requires at least one --allow ship id", file=sys.stderr)
        return 2
    if args.execute and Path("state/STOP").exists():
        print("state/STOP exists; refusing to build", file=sys.stderr)
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
            ship=args.ship,
        )
        result = build_recommended_ships(
            client,
            report,
            count=args.count,
            max_count=args.max_count,
            dry_run=not args.execute,
            allowed_elements=[int(item) for item in args.allow] if args.allow else None,
        )
        if args.json:
            print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(report, result))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())

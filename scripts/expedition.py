#!/usr/bin/env python3
"""Print a read-only expedition readiness report.

Nothing is built or sent.  The report covers expedition technology, slot usage,
current no-deuterium fleet score, a target coordinates suggestion, and a ship
build recommendation for a configurable score target.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from typing import Optional, Sequence

from local_config import load_config

from autoplay.expedition import DEFAULT_TARGET_SCORE, build_expedition_report
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
    parser.add_argument(
        "--target-score",
        type=float,
        default=DEFAULT_TARGET_SCORE,
        help="target no-deuterium fleet score (default: 500000)",
    )
    parser.add_argument("--ship", type=int, help="force one ship element id for the recommendation")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser.parse_args(argv)


def render(report) -> str:
    lines = [
        f"探险技术  {report.expedition_tech}",
        f"探险航道  {report.slots}（占用 {report.active_expeditions}，空闲 {report.free_slots}）",
        f"当前积分  {report.current_score:,.1f} / 目标 {report.target_score:,.1f}",
        f"目标坐标  {report.target or '-'}",
        f"可派遣    {report.can_send}",
    ]

    if report.available_ships:
        ships = "、".join(
            f"{report.ship_names.get(ship_id, f'#{ship_id}')}×{count}"
            for ship_id, count in report.available_ships.items()
            if count > 0
        )
        lines.append(f"现有舰船  {ships}")
    else:
        lines.append("现有舰船  -")

    recommendation = report.recommendation
    if recommendation is not None:
        cost = "、".join(
            f"{resource}:{amount:,.0f}" for resource, amount in recommendation.total_cost.items()
        )
        lines.append(
            f"建造建议  {recommendation.name}（{recommendation.element_id}）"
            f" 还需 {recommendation.needed} 艘"
            f"  单舰积分 {recommendation.score_per_ship:,.1f}"
        )
        if cost:
            lines.append(f"总成本    {cost}")
        lines.append(
            f"前置满足  {recommendation.requirements_met}  "
            f"资源足够  {recommendation.affordable}"
        )
        if recommendation.requirements:
            lines.append(f"前置条件  {'；'.join(recommendation.requirements)}")

    if report.active_fleets:
        lines.append("在途探险  " + "；".join(
            f"{item.get('mission') or '探险'} -> {item.get('target') or '-'}"
            for item in report.active_fleets
        ))

    if report.notes:
        lines.append("备注:")
        for note in report.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)


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
        report = build_expedition_report(
            client,
            target_score=args.target_score,
            ship=args.ship,
        )
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(render(report))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())

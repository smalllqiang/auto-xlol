#!/usr/bin/env python3
"""Empire overview: every colony from one request (``page=control``).

Read-only.  Useful before multi-planet scheduling exists: one GET shows each
colony's resources, hourly output, building levels, ships and defenses.

Examples::

    .venv/bin/python scripts/empire.py --no-verify-ssl
    .venv/bin/python scripts/empire.py --no-verify-ssl --json
    .venv/bin/python scripts/empire.py --no-verify-ssl --planet 1:1:8
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from local_config import load_config

from newstar_sdk import NewStarClient
from newstar_sdk.exceptions import NewStarError
from newstar_sdk.models import Coordinates

RESOURCE_NAMES = ("901", "902", "903", "911")


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
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--planet", help="only show this planet, e.g. 1:1:8")
    return parser.parse_args(argv)


def _print_planet(planet, *, title: Optional[str] = None) -> None:
    header = title or f"{planet.name} {planet.coordinates or ''}".strip()
    if planet.fields_max:
        header += f"  面积 {planet.fields_used}/{planet.fields_max}"
    print(header)
    resources = "  ".join(
        f"{name}={planet.resources.get(int(name), 0):,.0f}"
        for name in RESOURCE_NAMES
    )
    print(f"  资源  {resources}")
    production = "  ".join(
        f"{name}={planet.production.get(int(name), 0):,.0f}/h"
        for name in RESOURCE_NAMES[:3]
    )
    print(f"  产能  {production}")
    buildings = ", ".join(
        f"#{element}:{level}" for element, level in sorted(planet.buildings.items()) if level
    )
    print(f"  建筑  {buildings or '-'}")
    ships = sum(planet.ships.values())
    defenses = sum(planet.defenses.values())
    print(f"  舰只  {ships} 件（{len([v for v in planet.ships.values() if v])} 种）")
    print(f"  防御  {defenses} 件（{len([v for v in planet.defenses.values() if v])} 种）")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    config = load_config(args)
    if not config.verify_ssl:
        import warnings

        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    with NewStarClient(
        config.base_url,
        config.username,
        config.password,
        universe=config.universe,
        verify_ssl=config.verify_ssl,
        timeout=config.timeout,
        request_interval=config.interval,
    ) as client:
        try:
            report = client.get_control()
        except NewStarError as exc:
            print(f"读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        return 0

    slots = report.colony_slots
    print(f"行星 {len(report.planets)} 颗，殖民位 {slots[0]}/{slots[1]}" if slots else f"行星 {len(report.planets)} 颗")
    if args.planet:
        try:
            wanted = Coordinates.parse(args.planet)
        except ValueError:
            print(f"--planet 不是坐标：{args.planet}", file=sys.stderr)
            return 2
        planet = report.planet(wanted)
        if planet is None:
            print(f"没有这颗行星：{args.planet}", file=sys.stderr)
            return 2
        _print_planet(planet)
        return 0

    for planet in report.planets:
        _print_planet(planet)
    if report.total is not None:
        _print_planet(report.total, title="Σ 全账号合计")
    if report.unresolved:
        for category, names in sorted(report.unresolved.items()):
            print(
                f"{category} 里 SDK 未内置 {len(names)} 项（mod 专有单位）："
                f"{', '.join(names[:5])}…"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())

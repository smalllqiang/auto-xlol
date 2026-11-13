#!/usr/bin/env python3
"""Send a fleet (example).

The account must already have ships.  Use ``--yes`` to actually dispatch.

Example:
    python examples/send_fleet.py \
        --target 3:505:9 --mission transport \
        --ship small_cargo=10 --metal 1000 --crystal 500 --yes
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

from newstar_sdk import Coordinates, NewStarClient


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="G:S:P")
    parser.add_argument("--type", type=int, default=1, help="1 planet, 2 debris, 3 moon")
    parser.add_argument("--mission", default="transport")
    parser.add_argument("--ship", action="append", default=[], metavar="ID=COUNT")
    parser.add_argument("--speed", type=int, default=10)
    parser.add_argument("--metal", type=float, default=0)
    parser.add_argument("--crystal", type=float, default=0)
    parser.add_argument("--deuterium", type=float, default=0)
    parser.add_argument("--staytime", default="0")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    base_url = os.getenv("NEWSTAR_BASE_URL")
    username = os.getenv("NEWSTAR_USERNAME")
    password = os.getenv("NEWSTAR_PASSWORD")
    if not all((base_url, username, password)):
        print("Set NEWSTAR_BASE_URL, NEWSTAR_USERNAME and NEWSTAR_PASSWORD.", file=sys.stderr)
        return 2

    ships = {}
    for item in args.ship:
        if "=" not in item:
            parser.error(f"expected --ship ID=COUNT, got {item!r}")
        key, value = item.split("=", 1)
        ships[key] = int(value)
    if not ships:
        parser.error("at least one --ship ID=COUNT is required")

    verify_ssl = env_bool("NEWSTAR_VERIFY_SSL", True)
    if not verify_ssl:
        warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    client = NewStarClient(
        base_url,
        username,
        password,
        universe=os.getenv("NEWSTAR_UNIVERSE"),
        verify_ssl=verify_ssl,
    )
    try:
        client.login()
        available = client.get_available_ships()
        print("available ships:", {sid: ship.count for sid, ship in available.items()})
        target = Coordinates.parse(args.target, type=args.type)
        print(
            f"dispatching {ships} to {target} mission={args.mission} "
            f"speed={args.speed} resources=({args.metal}, {args.crystal}, {args.deuterium})"
        )
        if not args.yes:
            print("dry run only; pass --yes to dispatch")
            return 0
        result = client.send_fleet(
            target,
            ships,
            args.mission,
            speed=args.speed,
            resources={
                "metal": args.metal,
                "crystal": args.crystal,
                "deuterium": args.deuterium,
            },
            stay_time=args.staytime,
            target_type=args.type,
        )
        print("sent:", result)
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

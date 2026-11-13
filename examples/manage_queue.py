#!/usr/bin/env python3
"""Safely exercise the queue -> cancel cycle for a building and a technology.

This picks the cheapest currently affordable/available element, queues one
level, then cancels it again.  Use --yes to actually perform the requests.

Usage:
    export NEWSTAR_BASE_URL=... NEWSTAR_USERNAME=... NEWSTAR_PASSWORD=...
    python examples/manage_queue.py --yes
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

from newstar_sdk import ActionRejected, GameError, NewStarClient


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="actually perform the actions")
    args = parser.parse_args()

    base_url = os.getenv("NEWSTAR_BASE_URL")
    username = os.getenv("NEWSTAR_USERNAME")
    password = os.getenv("NEWSTAR_PASSWORD")
    if not all((base_url, username, password)):
        print("Set NEWSTAR_BASE_URL, NEWSTAR_USERNAME and NEWSTAR_PASSWORD.", file=sys.stderr)
        return 2

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
        building = _cheapest_ready(client.get_buildings())
        if building is None:
            print("no affordable building available")
        else:
            print(f"building: {building.name} level {building.level}")
            if args.yes:
                _try_cycle(
                    "building",
                    lambda: client.upgrade_building(building.id, 1),
                    lambda: client.cancel_building_queue(0),
                )

        technology = _cheapest_ready(client.get_research())
        if technology is None:
            print("no available technology")
        else:
            print(f"technology: {technology.name} level {technology.level}")
            if args.yes:
                _try_cycle(
                    "research",
                    lambda: client.research(technology.id, 1),
                    lambda: client.cancel_research_queue(0),
                )
        return 0
    finally:
        client.close()


def _cheapest_ready(items):
    candidates = [
        item
        for item in items
        if item.requirements_met
        and item.level < item.max_level
        and not item.missing_resources
        and item.cost
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: sum(item.cost.values()))


def _try_cycle(label: str, start, cancel) -> None:
    try:
        queue = start()
        print(f"{label} queued:", queue)
        try:
            print(f"{label} after cancel:", cancel())
        except GameError as exc:
            if "queue is empty" not in str(exc):
                raise
            print(f"{label} completed before it could be cancelled")
    except (GameError, ActionRejected, ValueError) as exc:
        print(f"{label} cycle failed:", exc)


if __name__ == "__main__":
    raise SystemExit(main())

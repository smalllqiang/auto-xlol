#!/usr/bin/env python3
"""Manual smoke test / demo for the New-Star SDK.

Read-only by default.  Use ``--mutate`` to test a safe action cycle
(queue one building level and cancel it again, then queue/cancel research).

Environment variables:
    NEWSTAR_BASE_URL, NEWSTAR_USERNAME, NEWSTAR_PASSWORD, NEWSTAR_UNIVERSE,
    NEWSTAR_VERIFY_SSL
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path

from newstar_sdk import NewStarClient
from newstar_sdk.exceptions import ActionRejected, GameError


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutate", action="store_true", help="test queue/cancel actions")
    parser.add_argument(
        "--save-session",
        metavar="PATH",
        help="save the resulting cookies to PATH (contains a session cookie)",
    )
    args = parser.parse_args()

    base_url = os.getenv("NEWSTAR_BASE_URL")
    username = os.getenv("NEWSTAR_USERNAME")
    password = os.getenv("NEWSTAR_PASSWORD")
    if not all((base_url, username, password)):
        print(
            "Set NEWSTAR_BASE_URL, NEWSTAR_USERNAME and NEWSTAR_PASSWORD first.",
            file=sys.stderr,
        )
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
        request_interval=0.5,
    )
    try:
        client.login()
        overview = client.get_overview()
        print(
            f"login ok: {overview.username} @ {overview.universe} "
            f"({overview.game_name}, {overview.engine_version})"
        )
        print(
            f"planet: {overview.planet.name} {overview.planet.coordinates} "
            f"fields={overview.planet.fields_used}/{overview.planet.fields_max}"
        )
        print(
            f"resources: metal={overview.resources.metal:.1f} "
            f"crystal={overview.resources.crystal:.1f} "
            f"deuterium={overview.resources.deuterium:.1f} "
            f"DM={overview.resources.dark_matter:.0f}"
        )
        print(
            f"activity: research={overview.status.research} "
            f"building={overview.status.building} shipyard={overview.status.shipyard}"
        )

        buildings = client.get_buildings()
        research = client.get_research()
        ships = client.get_ships()
        defenses = client.get_defenses()
        print(
            f"elements: {len(buildings)} buildings, {len(research)} technologies, "
            f"{len(ships)} ships, {len(defenses)} defences"
        )
        print("building queue:", client.get_building_queue())
        print("research queue:", client.get_research_queue())

        coords = overview.planet.coordinates
        if coords:
            galaxy = client.get_galaxy(coords.galaxy, coords.system)
            print(f"galaxy {galaxy.galaxy}:{galaxy.system}: {len(galaxy.slots)} slots")
        print("fleets:", client.get_fleets())
        print("available ships:", client.get_available_ships())

        if args.mutate:
            _mutate(client)

        if args.save_session:
            path = Path(args.save_session)
            client.save_session(path)
            print(f"session saved to {path}")
        return 0
    finally:
        client.close()


def _mutate(client: NewStarClient) -> None:
    print("\n--- mutation test: building queue/cancel ---")
    buildings = {item.id: item for item in client.get_buildings()}
    candidates = [
        item
        for item in buildings.values()
        if item.requirements_met and item.level < item.max_level and not item.missing_resources
    ]
    if not candidates:
        print("no affordable building to test")
    else:
        item = sorted(candidates, key=lambda x: sum(x.cost.values()))[0]
        print(f"queueing {item.name} (level {item.level} -> {item.level + 1})")
        try:
            queue = client.upgrade_building(item.id, 1)
            print("queue:", queue)
            try:
                print("cancel:", client.cancel_building_queue(0))
            except GameError as exc:
                if "queue is empty" not in str(exc):
                    raise
                updated = {b.id: b for b in client.get_buildings()}[item.id]
                print(
                    "queue already completed before cancel; "
                    f"level is now {updated.level}"
                )
        except (GameError, ActionRejected, ValueError) as exc:
            print("building action test failed:", exc)

    print("\n--- mutation test: research queue/cancel ---")
    research = {item.id: item for item in client.get_research()}
    candidates = [
        item
        for item in research.values()
        if item.requirements_met and item.level < item.max_level and not item.missing_resources
    ]
    if not candidates:
        print("no available technology to test")
    else:
        item = sorted(candidates, key=lambda x: sum(x.cost.values()))[0]
        print(f"queueing {item.name} (level {item.level} -> {item.level + 1})")
        try:
            queue = client.research(item.id, 1)
            print("queue:", queue)
            print("cancel:", client.cancel_research_queue(0))
        except (GameError, ActionRejected, ValueError) as exc:
            print("research action test failed:", exc)


if __name__ == "__main__":
    raise SystemExit(main())

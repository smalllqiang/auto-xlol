#!/usr/bin/env python3
"""Read account state and print a compact summary.

Usage:
    export NEWSTAR_BASE_URL=https://ol.lstyxl.com/
    export NEWSTAR_USERNAME=...
    export NEWSTAR_PASSWORD=...
    export NEWSTAR_UNIVERSE=CQBX8573
    export NEWSTAR_VERIFY_SSL=0
    python examples/read_state.py
"""

from __future__ import annotations

import os
import sys
import warnings

from newstar_sdk import NewStarClient


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off", ""}


def main() -> int:
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
        overview = client.get_overview()
        print(f"player: {overview.username} @ {overview.universe}")
        print(f"planet: {overview.planet.name} {overview.planet.coordinates}")
        print(
            f"resources: metal={overview.resources.metal:.0f} "
            f"crystal={overview.resources.crystal:.0f} "
            f"deuterium={overview.resources.deuterium:.0f} "
            f"DM={overview.resources.dark_matter:.0f}"
        )
        print("-" * 60)
        for building in client.get_buildings():
            print(f"building {building.id:>4} {building.name:<24} level={building.level}")
        print("-" * 60)
        for tech in client.get_research():
            print(f"tech     {tech.id:>4} {tech.name:<24} level={tech.level}")
        print("-" * 60)
        for ship in client.get_ships():
            print(
                f"ship     {ship.id:>4} {ship.name:<24} have={ship.available} "
                f"max={ship.max_buildable}"
            )
        print("-" * 60)
        print("building queue:", client.get_building_queue())
        print("research queue:", client.get_research_queue())
        print("shipyard queue:", client.get_ship_queue())
        print("fleets:", client.get_fleets())

        coordinates = overview.planet.coordinates
        if coordinates:
            galaxy = client.get_galaxy(coordinates.galaxy, coordinates.system)
            for slot in galaxy.slots:
                marker = "*" if slot.is_own else " "
                print(
                    f" {marker} {slot.position:>2} {slot.planet_name or '-':<24} "
                    f"{slot.player_name or '-':<16} {slot.actions}"
                )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

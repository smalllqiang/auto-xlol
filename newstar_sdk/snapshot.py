"""Aggregate read-only game state into one JSON-able snapshot.

The SDK's individual getters are optimised for interactive use: each call
fetches one page.  A long running automation loop wants the opposite -- one
call that returns everything it needs to make a decision, with per-section
error capture so a single broken page does not lose the whole snapshot.

:func:`build_snapshot` only reads.  It never posts, and it never decides
anything; it just reports facts and a few derived values (energy, storage
usage, expedition slots).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .constants import ResourceId, message_category_name
from .models import Coordinates, Overview, Resources

SCHEMA_VERSION = 1

#: 探险技术 / Expedition Technology.
EXPEDITION_TECH_ID = 124


def _coords(value: Optional[Coordinates]) -> Optional[str]:
    return str(value) if value is not None else None


def _resource_map(values: Dict[int, float]) -> Dict[str, float]:
    return {str(int(key)): float(value) for key, value in sorted(values.items())}


def _usage(resources: Resources) -> Dict[str, float]:
    """Amount / capacity per resource id (901 metal, 902 crystal, 903 deuterium)."""

    usage: Dict[str, float] = {}
    for resource_id in (ResourceId.METAL, ResourceId.CRYSTAL, ResourceId.DEUTERIUM):
        amount = resources.amount(resource_id)
        capacity = resources.capacity(resource_id)
        usage[str(int(resource_id))] = (
            round(amount / capacity, 4) if capacity > 0 else 0.0
        )
    return usage


def _planet(overview: Overview) -> Dict[str, Any]:
    planet = overview.planet
    return {
        "id": planet.id,
        "name": planet.name,
        "coordinates": _coords(planet.coordinates),
        "diameter_km": planet.diameter_km,
        "fields_used": planet.fields_used,
        "fields_max": planet.fields_max,
        "temp_min": planet.temp_min,
        "temp_max": planet.temp_max,
        "points": planet.points,
        "rank": planet.rank,
        "total_rank": planet.total_rank,
    }


def _overview_section(overview: Overview) -> Dict[str, Any]:
    resources = overview.resources
    return {
        "account": {
            "username": overview.username,
            "universe": overview.universe,
            "game_name": overview.game_name,
            "engine_version": overview.engine_version,
            "language": overview.language,
        },
        "server_time": overview.server_time.isoformat() if overview.server_time else None,
        "planet": _planet(overview),
        "planets": [
            {
                "id": item.id,
                "name": item.name,
                "coordinates": _coords(item.coordinates),
                "current": item.is_current,
            }
            for item in overview.planets
        ],
        "resources": {
            "amounts": _resource_map(resources.amounts),
            "capacities": _resource_map(resources.capacities),
            "production": _resource_map(resources.production),
            "usage": _usage(resources),
        },
        "energy": {
            "available": resources.amount(ResourceId.ENERGY),
            "production": resources.hourly(ResourceId.ENERGY),
        },
        "activity": {
            "building": overview.status.building,
            "research": overview.status.research,
            "shipyard": overview.status.shipyard,
        },
    }


def _bonuses_section(bonuses) -> Dict[str, Dict[str, float]]:
    return {
        item.name: {
            "percent": item.percent,
            "multiplier": item.multiplier,
        }
        for item in bonuses
    }


def _research_section(research: List[Any]) -> Dict[str, Any]:
    levels = {str(item.id): int(item.level) for item in research if item.level}
    expedition_level = int(levels.get(str(EXPEDITION_TECH_ID), 0))
    return {
        "levels": levels,
        "expedition_tech": expedition_level,
        "expedition_slots": int(math.isqrt(expedition_level)),
    }


def _queue_section(entries: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "index": entry.index,
            "element_id": entry.element_id,
            "name": entry.name,
            "amount": entry.amount,
            "target_level": entry.target_level,
            "remaining_seconds": entry.remaining_seconds,
            "end_time": entry.end_time,
            "active": entry.active,
        }
        for entry in entries
    ]


def _fleets_section(fleets: List[Any]) -> List[Dict[str, Any]]:
    return [
        {
            "id": fleet.id,
            "mission": fleet.mission,
            "mission_id": fleet.mission_id,
            "amount": fleet.amount,
            "start": _coords(fleet.start_coordinates),
            "target": _coords(fleet.target_coordinates),
            "arrival_time": fleet.arrival_time,
            "returning": fleet.returning,
            "can_recall": fleet.can_recall,
        }
        for fleet in fleets
    ]


def _production_section(report: Any) -> Dict[str, Any]:
    return {
        "planet": report.planet,
        "levels": {str(key): value for key, value in report.levels.items()},
        "ratios": {str(key): value for key, value in report.ratios.items()},
        "resources": {
            str(resource_id): {
                "base": item.base,
                "bonus": item.bonus,
                "capacity": item.capacity,
                "total": item.total,
                "daily": item.daily,
                "weekly": item.weekly,
                "buildings": {str(k): v for k, v in item.buildings.items()},
            }
            for resource_id, item in report.resources.items()
        },
    }


def _messages_section(counters: Dict[int, Any]) -> Dict[str, Any]:
    items = [
        {
            "category": counter.category,
            "name": message_category_name(counter.category),
            "unread": counter.unread,
            "total": counter.total,
        }
        for counter in sorted(counters.values(), key=lambda item: item.category)
    ]
    # Category 100 aggregates the inbox; summing every tab would double count.
    inbox = counters.get(100)
    outbox = counters.get(999)
    return {
        "counters": items,
        "unread": inbox.unread if inbox is not None else sum(i["unread"] for i in items),
        "total": inbox.total if inbox is not None else sum(i["total"] for i in items),
        "outbox": outbox.total if outbox is not None else 0,
    }


def build_snapshot(
    client: Any,
    *,
    include_research: bool = True,
    include_queues: bool = True,
    include_fleets: bool = True,
    include_messages: bool = True,
    include_bonuses: bool = True,
    include_production: bool = True,
) -> Dict[str, Any]:
    """Read the whole account state through ``client``.

    Sections are collected independently; a failure is recorded in
    ``errors`` and the remaining sections are still returned.  The result is
    JSON serialisable.
    """

    snapshot: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "errors": [],
    }

    def collect(name: str, func, key: str) -> None:
        try:
            snapshot[key] = func()
        except Exception as exc:  # noqa: BLE001 - a snapshot is best effort
            snapshot["errors"].append({"section": name, "error": f"{type(exc).__name__}: {exc}"})

    overview = client.get_overview()
    snapshot.update(_overview_section(overview))

    if include_research:
        collect("research", lambda: _research_section(client.get_research()), "research")
    if include_bonuses:
        collect("bonuses", lambda: _bonuses_section(client.get_bonuses()), "bonuses")
    if include_production:
        collect(
            "production",
            lambda: _production_section(client.get_production()),
            "production",
        )
    if include_queues:
        collect(
            "queues",
            lambda: {
                "building": _queue_section(client.get_building_queue()),
                "research": _queue_section(client.get_research_queue()),
                "shipyard": _queue_section(client.get_ship_queue(mode="fleet")),
            },
            "queues",
        )
    if include_fleets:
        collect("fleets", lambda: _fleets_section(client.get_fleets()), "fleets")
    if include_messages:
        collect("messages", lambda: _messages_section(client.get_message_counters()), "messages")

    return snapshot

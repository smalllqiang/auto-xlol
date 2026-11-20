"""CLI/default and planet-selection tests for the expedition farm script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from newstar_sdk.models import (
    Coordinates,
    FleetShips,
    Overview,
    PlanetInfo,
    PlanetSummary,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "expedition_farm.py"


def _module():
    if str(SCRIPT.parent) not in sys.path:
        sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("expedition_farm_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_defaults_match_the_requested_behavior():
    args = _module().parse_args([])

    assert args.ships == "207=8621"
    assert args.check_hours == pytest.approx(10.0)
    assert args.return_delay == pytest.approx(5.0)
    assert args.speed == 10
    assert args.stay_time == 1
    assert args.execute is False
    assert args.once is False
    assert args.lock_file == "state/daemon.lock"


def test_execute_requires_allow_list_covering_the_composition():
    module = _module()

    assert module._check_args(module.parse_args(["--execute"]), {207: 8621}) == 2
    assert (
        module._check_args(
            module.parse_args(["--execute", "--allow", "203"]), {207: 8621}
        )
        == 2
    )
    assert (
        module._check_args(
            module.parse_args(["--execute", "--allow", "207"]), {207: 8621}
        )
        is None
    )


def test_resolve_planet_defaults_to_first_and_accepts_index_coordinates_and_id():
    module = _module()
    first = PlanetSummary(id=1, name="母星", coordinates=Coordinates(3, 505, 8))
    second = PlanetSummary(id=2, name="二星", coordinates=Coordinates(3, 506, 9))
    overview = Overview(
        planets=[first, second],
        planet=PlanetInfo(id=2, name="二星", coordinates=Coordinates(3, 506, 9)),
    )

    assert module.resolve_planet(overview, None) is first
    assert module.resolve_planet(overview, "1") is first
    assert module.resolve_planet(overview, "2") is second
    assert module.resolve_planet(overview, "3:506:9") is second
    assert module.resolve_planet(overview, "2") is second
    assert module.resolve_planet(overview, None, saved_id=1) is first
    with pytest.raises(ValueError):
        module.resolve_planet(overview, "9:9:9")


def test_resolve_target_defaults_to_the_sixteenth_position():
    module = _module()

    assert module.resolve_target(Coordinates(3, 505, 8), None) == "3:505:16"
    assert module.resolve_target(Coordinates(3, 505, 8), "4:4:4") == "4:4:4"
    with pytest.raises(ValueError):
        module.resolve_target(None, None)


class _OverviewClient:
    """Minimal client for one `run_tick` without touching the network."""

    def __init__(self):
        self.fleets = []
        self.sent = []
        self.tech = 16

    def get_overview(self):
        from datetime import datetime

        return Overview(
            server_time=datetime.now(),
            planets=[PlanetSummary(id=22828, name="母星", coordinates=Coordinates(3, 505, 8))],
            planet=PlanetInfo(
                id=22828, name="母星", coordinates=Coordinates(3, 505, 8)
            ),
        )

    def get_research(self):
        from newstar_sdk.models import ResearchInfo

        return [ResearchInfo(id=124, name="探险技术", level=self.tech)]

    def get_fleets(self):
        return list(self.fleets)

    def get_available_ships(self):
        return {207: FleetShips(id=207, name="战列舰", count=100_000)}

    def send_fleet(self, target, ships, mission=None, speed=10, stay_time=0):
        from datetime import datetime, timedelta

        from newstar_sdk.models import FleetDispatch, FleetInfo

        wall = (datetime.now() + timedelta(hours=2)).strftime(
            "%Y年%m月%d日, %H:%M:%S"
        )
        self.fleets.append(
            FleetInfo(
                mission="探险",
                mission_id=15,
                target_coordinates=Coordinates(3, 505, 16),
                target_time=wall,
                arrival_time="02h 00m 00s",
                ships={"战列舰": 8621},
            )
        )
        self.sent.append((target, dict(ships), speed, stay_time))
        return FleetDispatch(
            mission=15,
            mission_name="探险",
            target_coordinates=Coordinates(3, 505, 16),
            arrival_time=wall,
        )


def test_run_tick_fills_every_lane_and_records_expected_returns(tmp_path):
    module = _module()
    client = _OverviewClient()
    args = module.parse_args(["--timetable", str(tmp_path / "timetable.json")])
    timetable = module.ExpeditionTimetable(path=tmp_path / "timetable.json")

    tech, max_slots, slots, fill, next_wake, clock = module.run_tick(
        client,
        plan=module.ExpeditionPlan(
            target="3:505:16", ships={207: 8621}, speed=10, stay_time=1
        ),
        timetable=timetable,
        clock=module.GameClock(),
        planet_id=22828,
        args=args,
    )

    assert (tech, max_slots) == (16, 4)
    assert fill.status == "dispatched"
    assert len(client.sent) == 4
    assert len(timetable.slots) == 4
    assert all(slot.expected_return_at is not None for slot in timetable.slots)
    assert next_wake > 0

    tech, max_slots, slots, fill, next_wake, clock = module.run_tick(
        client,
        plan=module.ExpeditionPlan(
            target="3:505:16", ships={207: 8621}, speed=10, stay_time=1
        ),
        timetable=timetable,
        clock=clock,
        planet_id=22828,
        args=args,
    )
    assert fill.status == "skipped"
    assert len(client.sent) == 4
    assert len(timetable.slots) == 4

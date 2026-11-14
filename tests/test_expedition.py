import json
import math
from types import SimpleNamespace

import pytest

from autoplay.expedition import (
    ExpeditionReport,
    build_expedition_report,
    compute_expedition_report,
)
from newstar_sdk.models import Coordinates, FleetInfo, PlanetInfo, ResearchInfo, Resources, ShipInfo

BATTLESHIP = ShipInfo(
    id=207,
    name="战列舰",
    cost={901: 41000.0, 902: 17000.0},
    build_seconds=20.0,
)
SMALL_CARGO = ShipInfo(
    id=202,
    name="轻型运输舰",
    cost={901: 2000.0, 902: 2000.0},
    build_seconds=5.0,
)
RESOURCES = {901: 1_000_000_000, 902: 1_000_000_000, 903: 1_000_000_000}


def _planet() -> PlanetInfo:
    return PlanetInfo(name="母星", coordinates=Coordinates(3, 505, 8))


def test_report_slots_score_target_and_recommendation():
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=9)],
        shipyard=[BATTLESHIP, SMALL_CARGO],
        available_ships={207: 1, 202: 2},
        fleets=[
            FleetInfo(
                mission="远征",
                mission_id=15,
                target_coordinates=Coordinates(3, 505, 16),
            )
        ],
        planet=_planet(),
        resources=RESOURCES,
        target_score=500_000,
    )

    assert report.expedition_tech == 9
    assert report.slots == 3
    assert report.active_expeditions == 1
    assert report.free_slots == 2
    assert report.current_score == pytest.approx(58.0 + 2 * 4.0)
    assert report.target_slots == 2
    assert report.required_score == pytest.approx(1_000_000.0)
    assert report.score_gap == pytest.approx(1_000_000.0 - report.current_score)
    assert report.target == "3:505:16"
    assert report.can_send is True

    recommendation = report.recommendation
    assert recommendation is not None
    assert recommendation.element_id == 207
    assert recommendation.name == "战列舰"
    assert recommendation.score_per_ship == pytest.approx(58.0)
    expected = math.ceil(report.score_gap / 58.0)
    assert recommendation.needed == expected
    assert recommendation.total_cost[901] == pytest.approx(41000.0 * expected)
    assert recommendation.affordable is True
    assert any("补足目标" in note for note in report.notes)
    assert any("每队目标" in note for note in report.notes)
    json.dumps(report.as_dict(), ensure_ascii=False)


def test_groups_split_ships_evenly():
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=4)],
        shipyard=[BATTLESHIP, SMALL_CARGO],
        available_ships={207: 2, 202: 4},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
        target_score=500_000,
    )

    assert len(report.fleet_groups) == 2
    assert [group.score for group in report.fleet_groups] == pytest.approx([66.0, 66.0])
    for group in report.fleet_groups:
        assert group.ships == {207: 1, 202: 2}
    assert sum(group.score for group in report.fleet_groups) == pytest.approx(
        report.current_score
    )


def test_groups_use_fewer_when_not_enough_ships():
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=9)],
        shipyard=[BATTLESHIP],
        available_ships={207: 1},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
    )

    assert report.free_slots == 3
    assert len(report.fleet_groups) == 1
    assert any("只够编成 1 队" in note for note in report.notes)


def test_report_treats_returning_expeditions_as_occupied():
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=4)],
        shipyard=[BATTLESHIP],
        available_ships={207: 1},
        fleets=[
            FleetInfo(mission_id=15, mission="远征", returning=False),
            FleetInfo(mission_id=15, mission="远征", returning=True),
        ],
        planet=_planet(),
        resources=RESOURCES,
    )

    assert report.slots == 2
    assert report.active_expeditions == 2
    assert report.free_slots == 0
    assert report.target_slots == 0
    assert report.required_score == pytest.approx(0.0)
    assert report.recommendation is None
    assert report.can_send is False
    assert any("航道已满" in note for note in report.notes)


def test_report_without_expedition_tech():
    report = compute_expedition_report(
        research=[],
        shipyard=[BATTLESHIP],
        available_ships={},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
    )

    assert report.expedition_tech == 0
    assert report.slots == 0
    assert report.free_slots == 0
    assert report.target_slots == 1
    assert report.required_score == pytest.approx(500_000.0)
    assert report.recommendation is not None
    assert report.can_send is False
    assert any("未解锁" in note for note in report.notes)
    assert any("按 1 队目标估算" in note for note in report.notes)


def test_recommendation_marks_unmet_requirements():
    locked = ShipInfo(
        id=207,
        name="战列舰",
        cost={901: 41000.0, 902: 17000.0},
        requirements_met=False,
        requirements=["星际船坞 等级 5 (0/5)"],
    )
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=9)],
        shipyard=[locked],
        available_ships={},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
    )

    assert report.recommendation is not None
    assert report.recommendation.requirements_met is False
    assert report.recommendation.affordable is False
    assert any("前置未满足" in note for note in report.notes)


def test_recommendation_prefers_no_deuterium_and_high_score():
    destroyer = ShipInfo(
        id=225,
        name="毁灭者",
        cost={901: 900_000.0, 902: 700_000.0, 903: 200_000.0},
    )
    report = compute_expedition_report(
        research=[ResearchInfo(id=124, name="探险技术", level=9)],
        shipyard=[SMALL_CARGO, destroyer, BATTLESHIP],
        available_ships={},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
        target_score=500_000,
    )

    assert report.recommendation is not None
    assert report.recommendation.element_id == 207


def test_build_report_falls_back_to_shipyard_availability():
    cargo = ShipInfo(
        id=202,
        name="轻型运输舰",
        cost={901: 2000.0, 902: 2000.0},
        available=3,
    )

    class FakeClient:
        def get_research(self):
            return [ResearchInfo(id=124, name="探险技术", level=9)]

        def get_buildings(self):
            return []

        def get_shipyard(self, mode="fleet"):
            assert mode == "fleet"
            return [cargo]

        def get_available_ships(self):
            return {}

        def get_fleets(self):
            return []

        def get_overview(self):
            return SimpleNamespace(
                planet=_planet(),
                resources=Resources(amounts=RESOURCES),
            )

    report = build_expedition_report(FakeClient())

    assert isinstance(report, ExpeditionReport)
    assert report.available_ships == {202: 3}
    assert report.current_score == pytest.approx(12.0)
    assert report.ship_names == {202: "轻型运输舰"}


def test_recommendation_expands_ship_prerequisites_to_blockers():
    locked = ShipInfo(
        id=207,
        name="战列舰",
        cost={901: 41000.0, 902: 17000.0},
        requirements_met=False,
        requirements=["星际船坞 等级 5 (0/5)", "武器技术 等级 7 (0/7)"],
    )
    shipyard_building = SimpleNamespace(
        id=21,
        name="星际船坞",
        level=0,
        requirements=["自律工厂 等级 2 (0/2)"],
        requirements_met=False,
        cost={901: 100.0, 902: 100.0},
        factor=2.0,
    )
    robot_factory = SimpleNamespace(
        id=14,
        name="自律工厂",
        level=0,
        requirements=[],
        requirements_met=True,
        cost={901: 100.0, 902: 100.0},
        factor=2.0,
    )
    weapons = SimpleNamespace(
        id=109,
        name="武器技术",
        level=0,
        requirements=[],
        requirements_met=True,
        cost={901: 100.0, 902: 100.0},
        factor=2.0,
    )

    report = compute_expedition_report(
        research=[
            ResearchInfo(id=124, name="探险技术", level=9),
            weapons,
        ],
        shipyard=[locked],
        buildings=[shipyard_building, robot_factory],
        available_ships={},
        fleets=[],
        planet=_planet(),
        resources=RESOURCES,
    )

    recommendation = report.recommendation
    assert recommendation is not None
    assert [action.name for action in recommendation.blockers] == [
        "武器技术",
        "自律工厂",
    ]
    assert recommendation.blockers[1].depth == 1
    assert recommendation.as_dict()["blockers"][0]["name"] == "武器技术"

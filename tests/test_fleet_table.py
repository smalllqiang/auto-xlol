"""Fleet-lane counters and the one-request fleet table (``page=fleetTable``)."""

import json
from pathlib import Path

from newstar_sdk.models import Coordinates
from newstar_sdk.parsers import parse_fleet_slots, parse_fleet_table

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_reads_lane_and_expedition_counters():
    slots = parse_fleet_slots(_fixture("fleet_table_colonize.html"))

    assert slots.active == 2
    assert slots.max_slots == 11
    assert slots.active_expeditions == 1
    assert slots.max_expeditions == 3
    assert slots.free == 9
    assert slots.non_expedition_active == 1


def test_empty_lane_banner_is_zero_safe():
    slots = parse_fleet_slots(_fixture("fleet_table_empty.html"))

    assert (slots.active, slots.max_slots) == (0, 1)
    assert (slots.active_expeditions, slots.max_expeditions) == (0, 0)
    assert slots.as_dict()["free"] == 1


def test_english_lane_labels():
    html = """
    <div class="gray_flettab">
      <div class="transparent">Fleets 3 / 8
        <span style="float:right">2 / 4 expedition</span>
      </div>
    </div>
    """

    slots = parse_fleet_slots(html)

    assert (slots.active, slots.max_slots) == (3, 8)
    assert (slots.active_expeditions, slots.max_expeditions) == (2, 4)


def test_page_without_banner_reports_zero_instead_of_raising():
    slots = parse_fleet_slots("<html><body>no fleets here</body></html>")

    assert (slots.active, slots.max_slots) == (0, 0)
    assert parse_fleet_table("<html><body>nothing</body></html>").fleets == []


def test_one_request_returns_lanes_fleets_and_selectable_ships():
    table = parse_fleet_table(_fixture("fleet_table_colonize.html"))

    colonize = table.fleets[0]
    assert colonize.mission_id == 7
    assert colonize.mission == "殖民"
    assert colonize.id == 43
    assert colonize.can_recall is True
    assert colonize.target_coordinates == Coordinates(1, 2, 3)
    assert colonize.start_time == "2026-11-22 12:30"

    expedition = table.fleets[1]
    assert expedition.mission_id == 15

    assert table.ships_here(208) == 4
    assert table.ships_here(207) == 0

    payload = json.dumps(table.as_dict(), ensure_ascii=False)
    assert '"max_slots": 11' in payload
    assert '"target": "[1:2:3]"' in payload

"""Tests for the empire overview parser (``page=control``)."""

from pathlib import Path

from newstar_sdk.models import Coordinates
from newstar_sdk.parsers import parse_control

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str = "control.html") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parses_the_only_planet():
    report = parse_control(_fixture())

    assert len(report.planets) == 1
    planet = report.planets[0]
    assert planet.name == "母星"
    assert planet.coordinates == Coordinates(1, 1, 8)
    assert (planet.fields_used, planet.fields_max) == (13, 273)


def test_parses_resources_and_point_pools():
    planet = parse_control(_fixture()).planets[0]

    assert planet.resources[901] == 100_000_017_609
    assert planet.resources[902] == 100_000_015_127
    assert planet.resources[903] == 50_000_009_790
    assert planet.resources[911] == 5_856
    # 建造点/研发点/船坞点/防御点/导弹点 share the resource section.
    assert planet.resources[912] == 500
    assert planet.resources[913] == 750
    assert planet.resources[914] == 0


def test_parses_hourly_production():
    planet = parse_control(_fixture()).planets[0]

    assert planet.production == {901: 3_619, 902: 2_413, 903: 0}


def test_parses_building_levels():
    planet = parse_control(_fixture()).planets[0]

    assert planet.buildings[1] == 4
    assert planet.buildings[2] == 4
    assert planet.buildings[4] == 4
    assert planet.buildings[31] == 1
    assert planet.buildings[81] == 0


def test_parses_ships_and_defenses_as_zero():
    planet = parse_control(_fixture()).planets[0]

    assert planet.ships
    assert planet.defenses
    assert all(value == 0 for value in planet.ships.values())
    assert all(value == 0 for value in planet.defenses.values())
    # Known base defenses resolve; mod-only ones are reported as unresolved.
    assert 402 in planet.defenses  # 轻型激光塔
    unresolved = parse_control(_fixture()).unresolved
    assert "轻型激光塔" not in unresolved.get("defenses", [])
    assert "盲目级炮塔" in unresolved.get("defenses", [])
    assert unresolved.get("ships")


def test_reads_the_sigma_column_and_colony_slots():
    report = parse_control(_fixture())

    assert report.total is not None
    assert report.total.resources[901] == 100_000_017_609
    assert report.total.buildings[1] == 4
    assert report.colony_slots == (1, 9)


def test_planet_lookup_by_coordinates():
    report = parse_control(_fixture())

    assert report.planet(Coordinates(1, 1, 8)) is report.planets[0]
    assert report.planet(Coordinates(9, 9, 9)) is None
    assert report.planet() is report.planets[0]


def test_missing_panel_returns_empty_report():
    report = parse_control("<html><body>no empire here</body></html>")

    assert report.planets == []
    assert report.total is None
    assert report.colony_slots is None


def test_report_is_json_serialisable():
    import json

    payload = json.dumps(parse_control(_fixture()).as_dict(), ensure_ascii=False)

    assert '"planets"' in payload
    assert '"colony_slots": [1, 9]' in payload


def test_label_column_found_without_the_chinese_header():
    # Same fixture with the header renamed: the fallback picks the widest column.
    html = _fixture().replace("星球名称", "Planet name")

    report = parse_control(html)

    assert report.planets
    assert report.planets[0].resources[901] == 100_000_017_609

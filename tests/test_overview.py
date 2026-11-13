from datetime import datetime

from newstar_sdk.parsers import parse_overview, parse_resources, parse_server_time


def test_overview_account_and_planet(fixture_html):
    overview = parse_overview(fixture_html("overview.html"))
    assert overview.username == "TestUser"
    assert overview.universe == "CQBX8573"
    assert overview.game_name == "战争宇宙：猛犸"
    assert overview.engine_version == "New-Star 2.8.1"
    assert overview.language == "zh"
    assert overview.server_time == datetime(2026, 11, 13, 12, 11, 35)

    assert overview.planet.id == 10001
    assert overview.planet.name == "母星"
    assert overview.planet.coordinates is not None
    assert (overview.planet.coordinates.galaxy, overview.planet.coordinates.system,
            overview.planet.coordinates.planet) == (1, 1, 8)
    assert overview.planet.diameter_km == 16522
    assert overview.planet.fields_used == 9
    assert overview.planet.fields_max == 273
    assert (overview.planet.temp_min, overview.planet.temp_max) == (-3, 37)
    assert overview.planet.points == 2
    assert overview.planet.rank == 22
    assert overview.planet.total_rank == 49

    assert len(overview.planets) == 1
    assert overview.planets[0].id == 10001
    assert overview.planets[0].is_current is True


def test_overview_resources(fixture_html):
    resources = parse_resources(fixture_html("overview.html"))
    assert abs(resources.metal - 10460.280546) < 1e-6
    assert abs(resources.crystal - 10557.785346) < 1e-6
    assert abs(resources.deuterium - 9990.0) < 1e-6
    assert resources.energy == 3993
    assert resources.amounts[912] == 1000
    assert resources.amounts[913] == 0
    assert resources.amounts[921] == 981
    assert resources.amounts[922] == 100
    assert resources.amounts[924] == 3

    assert resources.capacities[901] == 50000
    assert resources.capacities[902] == 50000
    assert resources.capacities[903] == 50000
    assert resources.capacities[911] == 4000
    assert resources.capacities[912] == 500
    assert 921 not in resources.capacities  # dark matter has no storage cap

    assert abs(resources.metal_per_hour - 2467.674) < 1e-6
    assert abs(resources.crystal_per_hour - 1645.116) < 1e-6
    assert resources.deuterium_per_hour == 0


def test_overview_status(fixture_html):
    overview = parse_overview(fixture_html("overview.html"))
    assert overview.status.research == "空闲"
    assert overview.status.building == "空闲"
    assert overview.status.shipyard == "空闲"
    assert overview.status.research_idle
    assert overview.status.building_idle
    assert overview.status.shipyard_idle


def test_server_time(fixture_html):
    assert parse_server_time(fixture_html("overview.html")) == datetime(2026, 11, 13, 12, 11, 35)
    assert parse_server_time("<html></html>") is None

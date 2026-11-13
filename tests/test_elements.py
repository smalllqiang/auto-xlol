from newstar_sdk.parsers import parse_buildings, parse_research, parse_shipyard


def test_buildings(fixture_html):
    items = parse_buildings(fixture_html("buildings.html"))
    assert len(items) == 20
    by_id = {item.id: item for item in items}

    metal = by_id[1]
    assert metal.name == "合金冶炼厂"
    assert metal.level == 3
    assert metal.level_to_build == 3
    assert metal.next_level == 4
    assert metal.max_level == 255
    assert metal.factor == 1.5
    assert metal.cost == {901: 202.5, 902: 50.625}
    assert metal.build_seconds == 1
    assert metal.requirements_met is True
    assert metal.requirements == []

    university = by_id[6]
    assert university.requirements_met is False
    assert any("科研实验室" in req for req in university.requirements)
    assert university.cost[901] == 100000000

    lab = by_id[31]
    assert lab.name == "科研实验室"
    assert lab.requirements_met is True
    assert lab.cost == {901: 200, 902: 400, 903: 200}


def test_research(fixture_html):
    items = parse_research(fixture_html("research.html"))
    assert len(items) == 19
    by_id = {item.id: item for item in items}

    computer = by_id[108]
    assert computer.name == "计算机技术"
    assert computer.level == 0
    assert computer.level_to_build == 0
    assert computer.cost == {902: 400, 903: 600}
    assert computer.research_seconds == 51
    assert computer.requirements_met is True

    espionage = by_id[106]
    assert espionage.requirements_met is False
    assert any("科研实验室" in req for req in espionage.requirements)

    graviton = by_id[199]
    assert graviton.name == "引力子技术"
    assert graviton.cost == {911: 300000}


def test_shipyard_fleet(fixture_html):
    items = parse_shipyard(fixture_html("shipyard_fleet.html"), "fleet")
    assert len(items) == 25
    by_id = {item.id: item for item in items}

    small_cargo = by_id[202]
    assert small_cargo.name == "轻型运输舰"
    assert small_cargo.category == "fleet"
    assert small_cargo.available == 0
    assert small_cargo.max_buildable == 5
    assert small_cargo.cost == {901: 2000, 902: 2000}
    assert small_cargo.requirements_met is False
    assert any("星际船坞" in req for req in small_cargo.requirements)
    assert small_cargo.build_seconds is not None and small_cargo.build_seconds > 0

    probe = by_id[210]
    assert probe.name == "间谍探针"
    assert probe.cost == {902: 1500}
    assert probe.max_buildable == 7


def test_shipyard_defense(fixture_html):
    items = parse_shipyard(fixture_html("shipyard_defense.html"), "defense")
    assert len(items) == 21
    by_id = {item.id: item for item in items}
    light_laser = by_id[402]
    assert light_laser.name == "轻型激光塔"
    assert light_laser.category == "defense"
    assert light_laser.cost == {901: 1500, 902: 500}
    assert light_laser.max_buildable == 6
    assert light_laser.is_defense is True
    assert 502 in by_id and 503 in by_id

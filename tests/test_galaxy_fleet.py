from newstar_sdk.parsers import (
    parse_fleet_dispatch_result,
    parse_fleet_selection,
    parse_fleet_step1,
    parse_fleet_step2,
    parse_fleets,
    parse_galaxy,
    parse_game_error,
)


def test_galaxy(fixture_html):
    view = parse_galaxy(
        fixture_html("galaxy.html"), current_planet_id=10001, username="TestUser"
    )
    assert view.galaxy == 1
    assert view.system == 1
    assert len(view.slots) == 15

    by_pos = {slot.position: slot for slot in view.slots}
    own = by_pos[8]
    assert own.planet_id == 10001
    assert own.planet_name == "母星 (*)"
    assert own.player_name == "TestUser"
    assert own.is_own is True
    assert own.coordinates is not None and own.coordinates.planet == 8
    assert own.actions[3] == "运输"
    assert own.actions[4] == "派遣"

    empty = by_pos[7]
    assert empty.abandoned is True
    assert empty.planet_name == "被遗弃的殖民地"
    assert empty.is_own is False

    slot1 = by_pos[1]
    assert slot1.area == 96
    assert slot1.temperature == 260
    assert slot1.actions[7] == "殖民"


def test_empty_fleet_table(fixture_html):
    assert parse_fleets(fixture_html("fleet_table_empty.html")) == []
    assert parse_fleet_selection(fixture_html("fleet_table_empty.html")) == {}


def test_error_page(fixture_html):
    assert parse_game_error(fixture_html("error_no_ships.html")) == "NFCoosen"
    assert parse_game_error(fixture_html("overview.html")) is None
    assert (
        parse_game_error(
            '<html><head><title>信息 - game</title></head>'
            '<body><div class="card-text">module disabled</div></body></html>'
        )
        == "module disabled"
    )
    assert parse_game_error('<div class="card-text">normal content</div>') is None


def test_fleet_step_parsing(fixture_html):
    token, max_speed = parse_fleet_step1(fixture_html("fleet_step1.html"))
    assert token == "tok-123"
    assert max_speed == 5000

    token, missions = parse_fleet_step2(fixture_html("fleet_step2.html"))
    assert token == "tok-123"
    assert [m.id for m in missions] == [3, 4, 6]
    assert missions[0].name == "运输"


def test_fleet_dispatch_result(fixture_html):
    result = parse_fleet_dispatch_result(fixture_html("fleet_dispatch_success.html"))
    assert result is not None
    assert result.mission == 3
    assert result.mission_name == "运输"
    assert result.from_coordinates is not None
    assert result.from_coordinates.planet == 8
    assert result.target_coordinates is not None
    assert result.target_coordinates.planet == 9
    assert result.distance == 5000
    assert result.consumption == 123
    assert result.ships == {202: 1}
    assert result.arrival_time


def test_fleet_table_with_flying_and_returning():
    html = """
    <div class="gray_flettab">舰队 2 / 11</div>
    <table class="tablesorter ally_ranks">
      <tr><td>number</td><td>mission</td><td>amount</td><td>from</td><td>start</td><td>to</td><td>target</td><td>arrival</td><td>order</td></tr>
      <tr>
        <td>1</td>
        <td>运输<br><a title="fl_onway">fl_a</a></td>
        <td><a data-tooltip-content="&lt;table&gt;&lt;tr&gt;&lt;td class='transparent'&gt;轻型运输舰:&lt;/td&gt;&lt;td&gt;10&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;">10</a></td>
        <td><a>[3:505:8]</a></td>
        <td>2026-11-13 12:00</td>
        <td><a>[3:505:9]</a></td>
        <td>2026-11-13 12:10</td>
        <td>2026-11-13 12:20</td>
        <td><form action="game.php?page=fleetTable&amp;action=sendfleetback"><input name="fleetID" value="42"></form></td>
      </tr>
      <tr>
        <td>2</td>
        <td>运输<br><a title="fl_returning">fl_r</a></td>
        <td><a data-tooltip-content="&lt;table&gt;&lt;tr&gt;&lt;td class='transparent'&gt;轻型运输舰:&lt;/td&gt;&lt;td&gt;5&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;">5</a></td>
        <td><a>[3:505:9]</a></td>
        <td>2026-11-13 11:00</td>
        <td><a>[3:505:8]</a></td>
        <td>2026-11-13 11:10</td>
        <td>2026-11-13 11:20</td>
        <td>-</td>
      </tr>
    </table>
    """
    fleets = parse_fleets(html)
    assert len(fleets) == 2
    outbound = fleets[0]
    assert outbound.id == 42
    assert outbound.mission == "运输"
    assert outbound.mission_id == 3
    assert outbound.amount == 10
    assert outbound.can_recall is True
    assert outbound.returning is False
    assert outbound.ships == {"轻型运输舰": 10}
    assert outbound.start_coordinates is not None and outbound.start_coordinates.planet == 8
    assert outbound.target_coordinates is not None and outbound.target_coordinates.planet == 9

    returning = fleets[1]
    assert returning.id is None
    assert returning.can_recall is False
    assert returning.returning is True
    assert returning.ships == {"轻型运输舰": 5}


def test_error_page_needs_titled_container():
    # ``.ally_contents`` is also used by normal settings/notification cards;
    # only the titled error container counts.
    settings_like = (
        "<html><head><title>设置 - game</title></head><body>"
        '<div class="ally_contents">头像 用户名称</div>'
        '<div class="card-text">0 (0%)</div></body></html>'
    )
    assert parse_game_error(settings_like) is None

    player_card = (
        "<html><head><title>玩家信息 - game</title></head><body>"
        '<div class="card-text">0 (0%)</div></body></html>'
    )
    assert parse_game_error(player_card) is None

    error_like = (
        "<html><head><title>信息 - game</title></head><body>"
        '<div id="ally_content"><div class="gray_stripe">信息</div>'
        '<div class="ally_contents">模块已禁用</div></div></body></html>'
    )
    assert parse_game_error(error_like) == "模块已禁用"

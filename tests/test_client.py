import json

import pytest

from newstar_sdk.client import NewStarClient
from newstar_sdk.exceptions import ActionRejected, ConfigurationError, GameError
from newstar_sdk.models import Coordinates, Resources


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php"):
        self.text = text
        self.url = url
        self.status_code = 200


def _make_client() -> NewStarClient:
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    return client


def test_send_fleet_flow(fixture_html, monkeypatch):
    client = _make_client()
    calls = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        page = (params or {}).get("page")
        calls.append((method, path, dict(params or {}), dict(data or {})))
        if page == "fleetStep1":
            return FakeResponse(fixture_html("fleet_step1.html"))
        if page == "fleetStep2":
            return FakeResponse(fixture_html("fleet_step2.html"))
        if page == "fleetStep3":
            return FakeResponse(fixture_html("fleet_dispatch_success.html"))
        raise AssertionError(f"unexpected page {page}")

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.send_fleet(
        "3:505:9",
        {"small_cargo": 1},
        mission="transport",
        speed=8,
        resources={"metal": 100, "crystal": 20, "deuterium": 0},
    )

    assert result.mission == 3
    assert result.target_coordinates == Coordinates(1, 1, 9)
    assert result.ships == {202: 1}
    assert len(calls) == 3

    step1 = calls[0][3]
    assert step1["galaxy"] == "3"
    assert step1["system"] == "505"
    assert step1["planet"] == "9"
    assert step1["target_mission"] == "3"
    assert step1["ship202"] == "1"

    step2 = calls[1][3]
    assert step2["token"] == "tok-123"
    assert step2["speed"] == "8"
    assert step2["target_mission"] == "3"

    step3 = calls[2][3]
    assert step3["token"] == "tok-123"
    assert step3["mission"] == "3"
    assert step3["metal"] == "100"
    assert step3["crystal"] == "20"
    assert step3["deuterium"] == "0"


def test_send_fleet_unavailable_mission(fixture_html, monkeypatch):
    client = _make_client()

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        page = (params or {}).get("page")
        if page == "fleetStep1":
            return FakeResponse(fixture_html("fleet_step1.html"))
        if page == "fleetStep2":
            return FakeResponse(fixture_html("fleet_step2.html"))
        raise AssertionError(f"unexpected page {page}")

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(ActionRejected, match="not available"):
        client.send_fleet(
            "3:505:9",
            {"small_cargo": 1},
            mission="expedition",
            resources={"metal": 1},
        )


def test_transport_requires_resources():
    client = _make_client()
    with pytest.raises(ValueError, match="at least one resource"):
        client.send_fleet("3:505:9", {"small_cargo": 1}, mission="transport")


def test_coerce_resources():
    resources = Resources(amounts={901: 10, 902: 20, 903: 30})
    assert NewStarClient._coerce_resources(resources) == {901: 10, 902: 20, 903: 30}
    assert NewStarClient._coerce_resources({"metal": 1, "crystal": 2, "deut": 3}) == {
        901: 1,
        902: 2,
        903: 3,
    }
    assert NewStarClient._coerce_resources((1, 2, 3)) == {901: 1, 902: 2, 903: 3}
    with pytest.raises(ValueError):
        NewStarClient._coerce_resources({"unobtainium": 1})


def test_universe_resolution(fixture_html):
    from newstar_sdk.parsers import parse_login_universes

    universes = parse_login_universes(fixture_html("login.html"))
    assert universes == [("1", "CQBX8573"), ("2", "CQBX9999")]

    client = _make_client()
    client._universe = None
    assert client._resolve_universe(universes) == "1"
    client._universe = "CQBX9999"
    assert client._resolve_universe(universes) == "2"
    client._universe = "1"
    assert client._resolve_universe(universes) == "1"
    client._universe = "does-not-exist"
    with pytest.raises(ConfigurationError):
        client._resolve_universe(universes)


def test_save_and_load_session(tmp_path):
    client = _make_client()
    client._session.cookies.set("2Moons", "session-value", domain="example.test", path="/")
    path = client.save_session(tmp_path / "session.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "password" not in payload
    assert payload["username"] == "user"
    assert payload["cookies"][0]["name"] == "2Moons"

    other = NewStarClient("https://example.test/", verify_ssl=False)
    assert other.load_session(path, validate=False) is True
    cookie = other._session.cookies.get("2Moons", domain="example.test")
    assert cookie == "session-value"


def _session_file(tmp_path, expires):
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "base_url": "https://example.test/",
                "username": "user",
                "cookies": [
                    {
                        "name": "2Moons",
                        "value": "abc",
                        "domain": "example.test",
                        "path": "/",
                        "secure": True,
                        "expires": expires,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_default_client_keeps_cookie_expiry(tmp_path):
    path = _session_file(tmp_path, 4102444800)
    client = NewStarClient("https://example.test/", verify_ssl=False)

    assert client.load_session(path, validate=False) is True
    assert next(iter(client._session.cookies)).expires == 4102444800

    out = client.save_session(tmp_path / "out.json")
    assert json.loads(out.read_text(encoding="utf-8"))["cookies"][0]["expires"] == 4102444800


def test_ignore_cookie_expiry_loads_without_expiry(tmp_path):
    path = _session_file(tmp_path, 4102444800)
    client = NewStarClient(
        "https://example.test/", verify_ssl=False, ignore_cookie_expiry=True
    )

    assert client.load_session(path, validate=False) is True
    assert next(iter(client._session.cookies)).expires is None

    out = client.save_session(tmp_path / "out.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["cookies"][0]["expires"] is None


def test_ignore_cookie_expiry_clears_after_each_response(monkeypatch):
    client = NewStarClient(
        "https://example.test/", verify_ssl=False, ignore_cookie_expiry=True
    )
    client._session.cookies.set(
        "2Moons", "abc", domain="example.test", path="/", expires=4102444800
    )
    monkeypatch.setattr(
        client._session, "request", lambda *a, **k: FakeResponse("<html></html>")
    )

    client._raw_request("GET", "game.php")

    assert next(iter(client._session.cookies)).expires is None


def test_select_planet_rejects_unknown(fixture_html, monkeypatch):
    client = _make_client()
    client._current_planet_id = 10001

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        return FakeResponse(fixture_html("overview.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(GameError, match="was not selected"):
        client.select_planet(9999)


def test_fleet_table_reads_lanes_and_ships_in_one_request(fixture_html, monkeypatch):
    client = _make_client()

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        assert (params or {}).get("page") == "fleetTable"
        return FakeResponse(fixture_html("fleet_table_colonize.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    table = client.get_fleet_table()

    assert table.slots.max_slots == 11
    assert table.ships_here(208) == 4
    assert [fleet.mission_id for fleet in table.fleets] == [7, 15]


def test_abandon_planet_posts_the_password_and_verifies(fixture_html, monkeypatch):
    client = _make_client()
    client._current_planet_id = 10001
    calls = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        query = dict(params or {})
        # ``cp`` is injected by ``_request``, which is replaced here.
        calls.append((method, client._current_planet_id, dict(data or {})))
        if query.get("page") == "overview" and method == "POST":
            return FakeResponse('{"ok": true, "message": "行星已放弃"}')
        if query.get("page") == "overview":
            return FakeResponse(fixture_html("overview.html"))
        raise AssertionError(f"unexpected page {query.get('page')}")

    monkeypatch.setattr(client, "_request", fake_request)
    result = client.abandon_planet(10002)

    assert result.ok is True
    assert result.planet_id == 10002
    method, planet_id, data = calls[0]
    assert method == "POST"
    assert planet_id == 10002
    assert data == {"mode": "delete", "password": "pass"}
    # The client must not stay pointed at the planet it just abandoned.
    assert client.planet_id == 10001


def test_abandon_planet_surfaces_the_game_message(monkeypatch):
    client = _make_client()
    client._current_planet_id = 10001

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        return FakeResponse('{"ok": false, "message": "密码错误"}')

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(ActionRejected, match="密码错误"):
        client.abandon_planet(10002)


def test_abandon_planet_needs_a_password():
    client = NewStarClient("https://example.test/", "user", None, verify_ssl=False)
    client._logged_in = True
    with pytest.raises(ConfigurationError, match="account password"):
        client.abandon_planet(10002)


def test_build_ships_flow(monkeypatch):
    client = _make_client()
    shipyard_page = """
    <html><body>
    <script>
    DatatList = {
      "202":{"id":"202","available":"0","costResources":{"901":2000,"902":2000,},
             "elementTime":10,"buyable":true,"maxBuildable":"5",
             "AlreadyBuild":false,"AlreadyBuildOne":false},
    };
    </script>
    <div class="build_box" id="s_202">
      <div class="head"><a class="title">轻型运输舰</a></div>
      <div class="content_box"><form class="build_form"></form></div>
    </div>
    </body></html>
    """
    queued_page = """
    <html><body>
    <div id="buildlist">
      <div class="element_row active_row"
           style="background:url(./styles/theme/nsc/gebaeude/202.gif)">
        <div class="right_hand">
          <span class="onlistretit">轻型运输舰</span>
          <div id="time" data-time="10"></div>
          <span class="onlistremov"><span id="auftr0">1</span></span>
          <span class="onlistrenum">1</span>
        </div>
      </div>
    </div>
    <script>
    DatatList = {
      "202":{"id":"202","available":"0","costResources":{"901":2000,"902":2000,},
             "elementTime":10,"buyable":true,"maxBuildable":"5",
             "AlreadyBuild":false,"AlreadyBuildOne":false},
    };
    </script>
    <div class="build_box" id="s_202">
      <div class="head"><a class="title">轻型运输舰</a></div>
      <div class="content_box"><form class="build_form"></form></div>
    </div>
    </body></html>
    """
    calls = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        calls.append((method, dict(params or {}), dict(data or {})))
        if method == "GET":
            return FakeResponse(shipyard_page)
        return FakeResponse(queued_page)

    monkeypatch.setattr(client, "_request", fake_request)
    queue = client.build_ships({"small_cargo": 1})

    assert queue and queue[0].element_id == 202
    assert queue[0].amount == 1
    assert calls[0][0] == "GET"
    assert calls[0][1]["page"] == "shipyard"
    assert calls[0][1]["mode"] == "fleet"
    assert calls[1][0] == "POST"
    assert calls[1][2]["fmenge[202]"] == "1"


def test_local_research_rejection_is_marked_before_post(monkeypatch):
    from newstar_sdk.models import ResearchInfo

    client = _make_client()
    item = ResearchInfo(
        id=199,
        name="引力子技术",
        level=0,
        level_to_build=0,
        cost={911: 300000.0},
        cost_overflow={911: 102456.0},
    )
    monkeypatch.setattr(client, "get_research", lambda: [item])

    with pytest.raises(ActionRejected) as caught:
        client.research(199)

    assert caught.value.before_post is True
    assert "not enough resources" in str(caught.value)


def test_action_rejected_defaults_to_post_rejection():
    assert ActionRejected("queue full").before_post is False


def test_preview_fleet_runs_only_the_first_two_steps(fixture_html, monkeypatch):
    client = _make_client()
    pages = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        page = (params or {}).get("page")
        pages.append(page)
        if page == "fleetStep1":
            return FakeResponse(fixture_html("fleet_step1.html"))
        if page == "fleetStep2":
            return FakeResponse(fixture_html("fleet_step2.html"))
        raise AssertionError(f"unexpected page {page}")

    monkeypatch.setattr(client, "_request", fake_request)
    preview = client.preview_fleet(
        "3:505:9",
        {"small_cargo": 1},
        mission="transport",
        speed=8,
        resources={"metal": 1},
    )

    assert pages == ["fleetStep1", "fleetStep2"]
    assert preview.mission == 3
    assert preview.mission_name == "运输"
    assert preview.target_coordinates == Coordinates(3, 505, 9)
    assert preview.consumption == pytest.approx(123)
    assert preview.max_speed == pytest.approx(5000)
    assert [option.id for option in preview.available_missions] == [3, 4, 6]
    assert preview.stay_times == []


def test_preview_fleet_exposes_expedition_stay_times(fixture_html, monkeypatch):
    client = _make_client()

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        page = (params or {}).get("page")
        if page == "fleetStep1":
            return FakeResponse(fixture_html("fleet_step1.html"))
        if page == "fleetStep2":
            return FakeResponse(fixture_html("fleet_step2_expedition.html"))
        raise AssertionError(f"unexpected page {page}")

    monkeypatch.setattr(client, "_request", fake_request)
    preview = client.preview_fleet(
        "3:505:16",
        {"small_cargo": 1},
        mission="expedition",
        speed=10,
    )

    assert preview.mission == 15
    assert preview.mission_name == "探险"
    assert preview.consumption == pytest.approx(3)
    assert preview.stay_times == [1]


def test_send_fleet_rejects_invalid_expedition_stay_time(fixture_html, monkeypatch):
    client = _make_client()
    pages = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        page = (params or {}).get("page")
        pages.append(page)
        if page == "fleetStep1":
            return FakeResponse(fixture_html("fleet_step1.html"))
        if page == "fleetStep2":
            return FakeResponse(fixture_html("fleet_step2_expedition.html"))
        raise AssertionError(f"fleetStep3 should not be called, got {page}")

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(ActionRejected, match="stay_time"):
        client.send_fleet(
            "3:505:16",
            {"small_cargo": 1},
            mission="expedition",
            speed=10,
            stay_time=0,
        )
    assert pages == ["fleetStep1", "fleetStep2"]



def _ratio_html(html: str, element_id: int, picked: int) -> str:
    """Return ``html`` with the selected option of ``prod[element_id]`` changed."""

    marker = f'name="prod[{element_id}]"'
    start = html.index(marker)
    select_end = html.index("</select>", start)
    block = html[start:select_end]
    block = block.replace(' selected="selected"', "")
    target = f'value="{picked}"'
    index = block.index(target)
    block = block[:index] + 'selected="selected" ' + block[index:]
    return html[:start] + block + html[select_end:]


def test_set_production_ratios_posts_the_whole_form(fixture_html, monkeypatch):
    client = _make_client()
    html = fixture_html("production.html")
    calls = []

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        calls.append((method, dict(params or {}), dict(data or {})))
        if method == "POST":
            return FakeResponse(_ratio_html(html, 1, 9))
        return FakeResponse(html)

    monkeypatch.setattr(client, "_request", fake_request)
    report = client.set_production_ratios({1: 90})

    assert len(calls) == 2
    method, params, data = calls[-1]
    assert method == "POST"
    assert params["page"] == "resources"
    assert data["mode"] == "send"
    assert data["prod[1]"] == "9"      # 90% -> tenth scale
    assert data["prod[2]"] == "10"     # untouched values are still posted
    assert data["prod[31]"] == "10"
    assert report.ratios[1] == 90


def test_set_production_ratios_verifies_the_readback(fixture_html, monkeypatch):
    client = _make_client()
    html = fixture_html("production.html")

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        return FakeResponse(html)  # unchanged page: the change did not stick

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(ActionRejected) as info:
        client.set_production_ratios({1: 90})
    assert info.value.before_post is False


def test_set_production_ratios_rejects_bad_input(fixture_html, monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        client, "_request", lambda *a, **k: FakeResponse(fixture_html("production.html"))
    )

    with pytest.raises(ValueError):
        client.set_production_ratios({1: 95})
    with pytest.raises(ValueError):
        client.set_production_ratios({1: 110})
    with pytest.raises(ValueError):
        client.set_production_ratios({})
    with pytest.raises(ActionRejected) as info:
        client.set_production_ratios({999: 90})
    assert info.value.before_post is True


def test_set_session_cookie_accepts_bare_value():
    client = NewStarClient("https://example.test/", verify_ssl=False)
    client.set_session_cookie("vg03jr0oaiocba68e0b9jeq5r2")

    assert client._session.cookies.get("2Moons") == "vg03jr0oaiocba68e0b9jeq5r2"


def test_set_session_cookie_accepts_header_line():
    client = NewStarClient("https://example.test/", verify_ssl=False)
    client.set_session_cookie("Cookie: lang=zh; 2Moons=abc123def; other=1")

    cookie = client._session.cookies.get("2Moons")
    assert cookie == "abc123def"
    assert client._session.cookies.get("lang") is None


def test_set_session_cookie_sets_https_flag_and_rejects_empty():
    client = NewStarClient("https://example.test/", verify_ssl=False)
    client.set_session_cookie("abc")

    jar_entry = next(iter(client._session.cookies))
    assert jar_entry.secure is True
    assert jar_entry.domain == "example.test"

    with pytest.raises(ConfigurationError):
        client.set_session_cookie("   ")

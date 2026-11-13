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


def test_select_planet_rejects_unknown(fixture_html, monkeypatch):
    client = _make_client()
    client._current_planet_id = 10001

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        return FakeResponse(fixture_html("overview.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(GameError, match="was not selected"):
        client.select_planet(9999)


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

import pytest

from newstar_sdk.client import NewStarClient
from newstar_sdk.parsers import parse_production


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/game.php"):
        self.text = text
        self.url = url
        self.status_code = 200


def _make_client() -> NewStarClient:
    client = NewStarClient("https://example.test/", "user", "pass", verify_ssl=False)
    client._logged_in = True
    return client


def test_parse_production(fixture_html):
    report = parse_production(fixture_html("production.html"))

    assert report.planet == "母星"
    assert report.levels == {1: 4, 2: 4, 4: 4, 31: 1}
    assert report.ratios == {1: 100, 2: 100, 4: 100, 31: 100}

    metal = report.get("metal")
    assert metal is not None
    assert metal.resource_id == 901
    assert metal.total == pytest.approx(3600)
    assert metal.daily == pytest.approx(86900)
    assert metal.weekly == pytest.approx(608000)
    assert metal.capacity == pytest.approx(50000)
    assert metal.bonus == pytest.approx(105)
    assert metal.buildings[1] == pytest.approx(3500)


def test_parse_production_energy_column(fixture_html):
    report = parse_production(fixture_html("production.html"))

    energy = report.get(911)
    assert energy is not None
    assert energy.capacity is None
    assert energy.buildings[4] == pytest.approx(5900)
    assert energy.buildings[1] == pytest.approx(-2900)


def test_production_ratio_lookup(fixture_html):
    report = parse_production(fixture_html("production.html"))

    assert report.ratio("metal") == 100
    assert report.ratio("research_lab") == 100
    assert report.ratio("crystal", default=42) == 100


def test_parse_production_empty_html():
    report = parse_production("<html><body></body></html>")
    assert report.resources == {}
    assert report.ratios == {}


def test_client_get_production(fixture_html, monkeypatch):
    client = _make_client()

    def fake_request(method, path, *, params=None, data=None, **kwargs):
        assert (params or {}).get("page") == "resources"
        return FakeResponse(fixture_html("production.html"))

    monkeypatch.setattr(client, "_request", fake_request)
    report = client.get_production()
    assert report.get("metal").total == pytest.approx(3600)

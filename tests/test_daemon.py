
from autoplay.daemon import DaemonConfig, backoff_delay, run_cycle
from autoplay.planner import compute_plan
from newstar_sdk.parsers import (
    parse_buildings,
    parse_overview,
    parse_production,
    parse_research,
)


class FakeClient:
    def __init__(self, building_queue=None, research_queue=None):
        self.building_queue = list(building_queue or [])
        self.research_queue = list(research_queue or [])
        self.calls = []

    def get_building_queue(self):
        return list(self.building_queue)

    def get_research_queue(self):
        return list(self.research_queue)

    def upgrade_building(self, element, levels=1):
        self.calls.append(("building", element, levels))
        return [object()]

    def research(self, element, levels=1):
        self.calls.append(("research", element, levels))
        return [object()]


def _install_plan(fixture_html):
    buildings = parse_buildings(fixture_html("buildings.html"))
    research = parse_research(fixture_html("research.html"))
    production = parse_production(fixture_html("production.html"))
    resources = parse_overview(fixture_html("overview.html")).resources.amounts
    plan = compute_plan(buildings, research, production, resources)
    return plan


def test_backoff_delay_grows_and_caps():
    assert backoff_delay(0) == 0
    assert backoff_delay(1, base=30, maximum=900) == 30
    assert backoff_delay(2, base=30, maximum=900) == 60
    assert backoff_delay(3, base=30, maximum=900) == 120
    assert backoff_delay(10, base=30, maximum=900) == 900


def test_run_cycle_dry_run(fixture_html, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda _client: _install_plan(fixture_html))

    result = run_cycle(client, DaemonConfig(dry_run=True))

    assert result.status == "dry_run"
    assert result.phase == "pre_gravity"
    assert result.action is not None
    assert client.calls == []


def test_run_cycle_executes_when_allowed(fixture_html, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda _client: _install_plan(fixture_html))

    result = run_cycle(
        client,
        DaemonConfig(dry_run=False, allowed_elements=[14]),
    )

    assert result.status == "executed"
    assert client.calls == [("building", 14, 1)]


def test_run_cycle_respects_busy_queue(fixture_html, monkeypatch):
    client = FakeClient(building_queue=[object()])
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda _client: _install_plan(fixture_html))

    result = run_cycle(
        client,
        DaemonConfig(dry_run=False, allowed_elements=[14, 108]),
    )

    assert result.status == "executed"
    assert client.calls == [("research", 108, 1)]


def test_run_cycle_without_allow_list_skips(fixture_html, monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("autoplay.daemon.build_plan", lambda _client: _install_plan(fixture_html))

    result = run_cycle(client, DaemonConfig(dry_run=False))

    assert result.status == "skipped"
    assert client.calls == []

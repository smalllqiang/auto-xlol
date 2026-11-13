import json

from newstar_sdk.models import MessageCounter, ResearchInfo
from newstar_sdk.parsers import (
    parse_bonuses,
    parse_building_queue,
    parse_fleets,
    parse_overview,
    parse_production,
)
from newstar_sdk.snapshot import build_snapshot


class FakeClient:
    def __init__(self, fixture_html, research=None):
        self._fixture = fixture_html
        self._research = research or []

    def get_overview(self):
        return parse_overview(self._fixture("overview.html"))

    def get_research(self):
        return self._research

    def get_bonuses(self):
        return parse_bonuses(self._fixture("infobonus.html"))

    def get_production(self):
        return parse_production(self._fixture("production.html"))

    def get_building_queue(self):
        return parse_building_queue(self._fixture("buildings_queue.html"))

    def get_research_queue(self):
        return []

    def get_ship_queue(self, mode="fleet"):
        return []

    def get_fleets(self):
        return parse_fleets(self._fixture("fleet_table_empty.html"))

    def get_message_counters(self):
        return {
            0: MessageCounter(0, unread=1, total=2),
            15: MessageCounter(15, unread=0, total=3),
        }


def test_snapshot_shape(fixture_html):
    snapshot = build_snapshot(FakeClient(fixture_html))

    assert snapshot["schema"] == 1
    assert snapshot["errors"] == []
    assert snapshot["account"]["username"] == "TestUser"
    assert snapshot["planet"]["coordinates"] == "[1:1:8]"
    assert snapshot["planets"][0]["current"] is True
    assert snapshot["resources"]["amounts"]["901"] > 0
    assert set(snapshot["resources"]["usage"]) == {"901", "902", "903"}
    assert snapshot["resources"]["usage"]["901"] > 0
    assert "bonuses" in snapshot and snapshot["bonuses"]
    assert snapshot["production"]["resources"]["901"]["total"] == 3600
    assert snapshot["production"]["ratios"]["1"] == 100
    assert snapshot["messages"]["unread"] == 1
    assert snapshot["messages"]["total"] == 5
    assert len(snapshot["queues"]["building"]) == 1
    assert snapshot["queues"]["building"][0]["element_id"] == 31
    assert snapshot["queues"]["building"][0]["target_level"] == 1
    assert snapshot["fleets"] == []
    # must be JSON serialisable as-is
    json.dumps(snapshot, ensure_ascii=False)


def test_snapshot_expedition_slots(fixture_html):
    research = [ResearchInfo(id=124, name="探险技术", level=9)]
    snapshot = build_snapshot(FakeClient(fixture_html, research=research))
    assert snapshot["research"]["levels"] == {"124": 9}
    assert snapshot["research"]["expedition_tech"] == 9
    assert snapshot["research"]["expedition_slots"] == 3


def test_snapshot_captures_section_errors(fixture_html):
    class Broken(FakeClient):
        def get_research(self):
            raise RuntimeError("research page changed")

    snapshot = build_snapshot(Broken(fixture_html))

    assert snapshot["errors"] == [
        {"section": "research", "error": "RuntimeError: research page changed"}
    ]
    # other sections still present
    assert snapshot["resources"]["amounts"]["901"] > 0
    assert "bonuses" in snapshot


def test_snapshot_can_skip_sections(fixture_html):
    snapshot = build_snapshot(
        FakeClient(fixture_html),
        include_research=False,
        include_queues=False,
        include_fleets=False,
        include_messages=False,
        include_bonuses=False,
        include_production=False,
    )
    assert "research" not in snapshot
    assert "queues" not in snapshot
    assert "fleets" not in snapshot
    assert "messages" not in snapshot
    assert "bonuses" not in snapshot
    assert "production" not in snapshot
    assert snapshot["resources"]["amounts"]["901"] > 0


def test_snapshot_message_totals_use_inbox_aggregate(fixture_html):
    class WithAggregate(FakeClient):
        def get_message_counters(self):
            return {
                15: MessageCounter(15, unread=1, total=4),
                100: MessageCounter(100, unread=3, total=9),
                999: MessageCounter(999, total=2),
            }

    snapshot = build_snapshot(
        WithAggregate(fixture_html),
        include_research=False,
        include_queues=False,
        include_fleets=False,
        include_bonuses=False,
    )
    assert snapshot["messages"]["unread"] == 3
    assert snapshot["messages"]["total"] == 9
    assert snapshot["messages"]["outbox"] == 2

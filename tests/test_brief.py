from pathlib import Path

from autoplay.brief import build_brief, render_brief
from autoplay.planner import compute_plan
from newstar_sdk.parsers import (
    parse_bonuses,
    parse_building_queue,
    parse_buildings,
    parse_fleets,
    parse_overview,
    parse_production,
    parse_research,
)
from newstar_sdk.snapshot import build_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeClient:
    def get_overview(self):
        return parse_overview(_html("overview.html"))

    def get_research(self):
        return parse_research(_html("research.html"))

    def get_bonuses(self):
        return parse_bonuses(_html("infobonus.html"))

    def get_production(self):
        return parse_production(_html("production.html"))

    def get_building_queue(self):
        return parse_building_queue(_html("buildings_queue.html"))

    def get_research_queue(self):
        return []

    def get_ship_queue(self, mode="fleet"):
        return []

    def get_fleets(self):
        return parse_fleets(_html("fleet_table_empty.html"))

    def get_message_counters(self):
        return {}


def _plan():
    return compute_plan(
        parse_buildings(_html("buildings.html")),
        parse_research(_html("research.html")),
        parse_production(_html("production.html")),
        parse_overview(_html("overview.html")).resources.amounts,
    )


def _snapshot():
    return build_snapshot(FakeClient())


def _brief(**kwargs):
    return build_brief(_snapshot(), _plan(), **kwargs)


def test_brief_projects_account_and_resources():
    brief = _brief()

    assert brief["schema"] == 1
    assert brief["account"]["phase"] == "pre_gravity"
    assert brief["account"]["goal"] == "建成资源处理单元（0/1）"
    assert set(brief["resources"]) == {"901", "902", "903"}
    assert brief["resources"]["901"]["hourly"] > 0
    assert brief["energy"]["source"] == "production.total"
    assert brief["queues"]["building"]["count"] == 1


def test_candidates_keep_plan_order_and_numbers():
    plan = _plan()
    brief = _brief()

    assert [item["name"] for item in brief["candidates"]] == [
        action.name for action in plan.actions
    ]
    assert brief["limits"]["candidates_total"] == len(plan.actions)
    first = brief["candidates"][0]
    assert first["category"] == "blocker"
    assert first["required_level"] == 5
    assert first["steps"] == 5


def test_plan_notes_are_not_mistaken_for_unknowns():
    brief = _brief()

    assert brief["unknowns"] == []
    assert any("引力子技术" in note for note in brief["notes"])


def test_snapshot_errors_become_unknowns():
    snapshot = _snapshot()
    snapshot["errors"] = [{"section": "production", "error": "ParseError: boom"}]

    brief = build_brief(snapshot, _plan())

    assert brief["unknowns"][0]["section"] == "production"
    assert "ParseError" in brief["unknowns"][0]["detail"]


def test_overflow_is_flagged_from_capacity():
    snapshot = _snapshot()
    # The fixture planet is well below capacity; push metal over it.
    snapshot["resources"]["amounts"]["901"] = snapshot["resources"]["capacities"]["901"] + 1

    brief = build_brief(snapshot, _plan())

    assert brief["resources"]["901"]["overflowed"] is True
    assert brief["resources"]["902"]["overflowed"] is False


def test_delta_is_first_decision_then_reports_changes():
    first = _brief()
    assert first["delta"] == {"first_decision": True}

    snapshot = _snapshot()
    snapshot["resources"]["amounts"]["901"] = 1_000_000
    second = build_brief(snapshot, _plan(), previous=first)

    assert second["delta"]["resources"]["901"] > 0


def test_render_brief_is_readable_and_budgeted():
    brief = _brief()
    text = render_brief(brief)

    assert "# 决策简报" in text
    assert "## 候选" in text
    assert "自律工厂" in text

    tight = render_brief(brief, max_chars=600)
    assert len(tight) < len(text)
    assert "未展示" in tight or "省略" in tight

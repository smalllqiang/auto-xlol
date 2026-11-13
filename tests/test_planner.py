import json

import pytest

from autoplay.economy import (
    equivalent_cost,
    payback_hours,
    production_delta,
    scale_cost,
)
from autoplay.phases import EXPEDITION, EXPEDITION_PREP, PRE_GRAVITY, detect_phase
from autoplay.planner import compute_plan, parse_requirements
from newstar_sdk.parsers import (
    parse_buildings,
    parse_overview,
    parse_production,
    parse_research,
)


def _state(fixture_html):
    return (
        parse_buildings(fixture_html("buildings.html")),
        parse_research(fixture_html("research.html")),
        parse_production(fixture_html("production.html")),
        parse_overview(fixture_html("overview.html")).resources.amounts,
    )


def test_equivalent_cost():
    assert equivalent_cost({901: 1, 902: 2, 903: 4}) == pytest.approx(21)
    assert equivalent_cost({}) == 0


def test_payback_hours():
    assert payback_hours(100, 50) == pytest.approx(2)
    assert payback_hours(100, 0) is None
    assert payback_hours(100, None) is None


def test_production_delta():
    # 1.1 * (3+1)/3 - 1
    assert production_delta(1000, 3) == pytest.approx(466.666, rel=1e-3)
    assert production_delta(1000, 0) is None
    assert production_delta(0, 3) is None


def test_scale_cost():
    assert scale_cost({901: 100}, 2.0, 1) == {901: 100}
    # 1 + 2 + 4 = 7 levels worth of cost
    assert scale_cost({901: 100}, 2.0, 3) == {901: 700}
    assert scale_cost({901: 100}, None, 2) == {901: 200}


def test_parse_requirements():
    requirements = parse_requirements(
        ["科研实验室 等级 12 (1/12)", "引力子技术 等级 1 (0/1)", "garbage"]
    )
    assert len(requirements) == 2
    lab, graviton = requirements
    assert (lab.name, lab.kind, lab.element_id) == ("科研实验室", "building", 31)
    assert (lab.required, lab.current, lab.met) == (12, 1, False)
    assert (graviton.kind, graviton.element_id) == ("research", 199)
    assert graviton.met is False


def test_detect_phase(fixture_html):
    buildings, research, _, _ = _state(fixture_html)
    assert detect_phase(buildings, research) is PRE_GRAVITY

    class Item:
        def __init__(self, id, level):
            self.id = id
            self.level = level

    buildings = [Item(81, 1)]
    assert detect_phase(buildings, [Item(124, 3)]) is EXPEDITION_PREP
    assert detect_phase(buildings, [Item(124, 9)]) is EXPEDITION


def test_plan_blockers(fixture_html):
    plan = compute_plan(*_state(fixture_html))

    assert plan.phase == "pre_gravity"
    assert "资源处理单元" in plan.goal_status
    assert plan.notes and "引力子技术" in plan.notes[0]

    blockers = [action for action in plan.actions if action.category == "blocker"]
    names = [action.name for action in blockers]
    assert names[:3] == ["自律工厂", "计算机技术", "能源技术"]
    # The lab is one level deeper: it unlocks graviton, which unlocks the processor.
    lab = next(action for action in blockers if action.name == "科研实验室")
    assert lab.depth == 1
    assert all(action.affordable for action in blockers)


def test_blocker_total_cost(fixture_html):
    plan = compute_plan(*_state(fixture_html))

    robot = next(action for action in plan.actions if action.name == "自律工厂")
    assert robot.steps == 5
    assert robot.required_level == 5
    assert robot.total_cost_equivalent > robot.cost_equivalent
    # Fixture account can afford one level but not the whole chain.
    assert robot.affordable is True
    assert robot.affordable_total is False


def test_plan_roi_sorted_by_payback(fixture_html):
    plan = compute_plan(*_state(fixture_html))

    roi = [action for action in plan.actions if action.category == "roi"]
    assert roi
    assert roi[0].name == "幽能发电站"
    paybacks = [action.payback_hours for action in roi if action.payback_hours is not None]
    assert paybacks == sorted(paybacks)
    deut = next(action for action in roi if action.name == "重氢精炼厂")
    assert deut.payback_hours is None


def test_plan_is_json_serialisable(fixture_html):
    plan = compute_plan(*_state(fixture_html))
    payload = json.dumps(plan.as_dict(), ensure_ascii=False)
    decoded = json.loads(payload)
    assert decoded["phase"] == "pre_gravity"
    assert decoded["actions"][0]["category"] == "blocker"


def test_plan_marks_unaffordable(fixture_html):
    buildings, research, production, _ = _state(fixture_html)
    plan = compute_plan(buildings, research, production, {901: 0, 902: 0, 903: 0})
    assert all(action.affordable is False for action in plan.actions)

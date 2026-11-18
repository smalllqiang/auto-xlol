"""Golden baseline of the default strategy (AI-0).

The phase goals in :mod:`autoplay.phases` are the *default directive*: what the
bot aims at when nobody injects anything.  AI-1 will make that directive
replaceable; this file freezes what the default currently produces on the
fixture account so the refactor can prove field-by-field equivalence instead of
"the tests still pass".  See ``docs/doctrine.md`` and ``docs/roadmap.md``.

If a deliberate strategy change moves these values, update the expectation in
the same commit and say why in the commit message.
"""

from pathlib import Path

from autoplay.planner import compute_plan
from newstar_sdk.parsers import (
    parse_buildings,
    parse_overview,
    parse_production,
    parse_research,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _default_plan():
    return compute_plan(
        parse_buildings(_fixture("buildings.html")),
        parse_research(_fixture("research.html")),
        parse_production(_fixture("production.html")),
        parse_overview(_fixture("overview.html")).resources.amounts,
    )


#: (category, name, current_level, target_level, has_payback, priority)
GOLDEN_ACTIONS = [
    ("blocker", "自律工厂", 0, 1, False, 0),
    ("blocker", "计算机技术", 0, 1, False, 0),
    ("blocker", "能源技术", 0, 1, False, 0),
    ("blocker", "科研实验室", 0, 1, False, 0),
    ("roi", "合金冶炼厂", 3, 4, True, 0),
    ("roi", "水晶催化厂", 3, 4, True, 0),
    ("roi", "重氢精炼厂", 0, 1, False, 0),
    ("roi", "幽能发电站", 3, 4, False, 0),
]

GOLDEN_NOTES = ["引力子技术需要 300,000 幽能，当前净可用约 3,993（还差 296,007）"]


def test_default_plan_matches_golden():
    plan = _default_plan()

    assert plan.phase == "pre_gravity"
    assert plan.phase_name == "种田初起"
    assert plan.goal == "建成资源处理单元"
    assert plan.goal_status == "建成资源处理单元（0/1）"

    actual = [
        (
            action.category,
            action.name,
            action.current_level,
            action.target_level,
            action.payback_hours is not None,
            action.priority,
        )
        for action in plan.actions
    ]
    assert actual == GOLDEN_ACTIONS
    assert plan.notes == GOLDEN_NOTES


def test_default_plan_keeps_blockers_before_roi():
    plan = _default_plan()

    categories = [action.category for action in plan.actions]
    assert categories == sorted(categories, key=lambda name: name != "blocker")

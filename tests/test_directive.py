"""Tests for the strategic directive (AI-1)."""

import json
from types import SimpleNamespace

import pytest

from autoplay.directive import (
    DEFAULT_CONSTRAINTS,
    Directive,
    DirectiveError,
    default_directive,
    load_directive,
    parse_directive,
    save_directive,
    validate_phases,
)
from autoplay.phases import (
    EXPEDITION,
    EXPEDITION_PREP,
    PHASES,
    PRE_GRAVITY,
    Phase,
    detect_phase,
)
from autoplay.planner import compute_plan


def _payload(**overrides):
    payload = {
        "version": 1,
        "phases": [
            {
                "key": "goal",
                "name": "自定目标",
                "goal": "把探险技术升到 5",
                "goal_kind": "research",
                "goal_element": 124,
                "goal_level": 5,
            },
            {
                "key": "done",
                "name": "终局",
                "goal": "里程碑",
                "goal_kind": "milestone",
                "goal_element": 0,
                "goal_level": 0,
            },
        ],
        "constraints": {"one_action_per_round": True},
        "risk_posture": "conservative",
        "expires_when": "阶段目标达成",
    }
    payload.update(overrides)
    return payload


def _research(element_id, level, name="探险技术"):
    return SimpleNamespace(
        id=element_id,
        name=name,
        level=level,
        requirements=[],
        requirements_met=True,
        cost={901: 100},
        cost_overflow={},
        factor=2.0,
        max_level=255,
    )


def test_default_directive_is_the_builtin_phases():
    directive = default_directive()

    assert directive.phases == PHASES
    assert directive.source == "default"
    assert directive.constraints == DEFAULT_CONSTRAINTS
    assert directive.errors == ()
    assert directive.age_seconds() is None
    assert [phase.key for phase in directive.phases] == [
        "pre_gravity",
        "expedition_prep",
        "expedition",
    ]


def test_detect_phase_follows_directive():
    processor = SimpleNamespace(id=81, name="资源处理单元", level=1)
    assert detect_phase([], [], None) is PRE_GRAVITY
    assert detect_phase([processor], [_research(124, 3)], None) is EXPEDITION_PREP
    assert detect_phase([processor], [_research(124, 9)], None) is EXPEDITION

    custom = Directive(
        phases=(
            Phase("custom", "自定", "探险技术 5", "research", 124, 5),
            Phase("done", "终局", "里程碑", "milestone", 0, 0),
        )
    )
    # 81 / 124 checks are gone: only the injected phases decide.
    assert detect_phase([], [_research(124, 4)], custom).key == "custom"
    assert detect_phase([], [_research(124, 5)], custom).key == "done"


def test_parse_accepts_a_valid_document():
    directive = parse_directive(_payload(), path="x.json")

    assert directive.source == "file"
    assert directive.path == "x.json"
    assert [phase.key for phase in directive.phases] == ["goal", "done"]
    assert directive.phases[0].goal_element == 124
    assert directive.constraints == {"one_action_per_round": True}


@pytest.mark.parametrize(
    "payload, needle",
    [
        (["not", "an", "object"], "JSON 对象"),
        (_payload(version=99), "不支持的 directive 版本"),
        (_payload(version="1"), "version 必须是整数"),
        (_payload(phases=[]), "phases 必须是非空数组"),
        (_payload(phases="nope"), "phases 必须是非空数组"),
        (_payload(constraints=[]), "constraints 必须是 JSON 对象"),
        (_payload(risk_posture=1), "risk_posture 必须是字符串"),
        (_payload(expires_when=[]), "expires_when 必须是字符串"),
        (
            _payload(
                phases=[
                    {
                        "key": "bad",
                        "name": "坏",
                        "goal": "x",
                        "goal_kind": "spaceship",
                        "goal_element": 1,
                        "goal_level": 1,
                    }
                ]
            ),
            "goal_kind 必须是",
        ),
        (
            _payload(
                phases=[
                    {
                        "key": "missing",
                        "name": "缺字段",
                        "goal_kind": "research",
                    }
                ]
            ),
            "缺少字段 goal",
        ),
        (
            _payload(
                phases=[
                    {
                        "key": "noelement",
                        "name": "缺元素",
                        "goal": "x",
                        "goal_kind": "building",
                        "goal_element": 0,
                        "goal_level": 1,
                    }
                ]
            ),
            "goal_element 必须是正整数",
        ),
    ],
)
def test_parse_rejects_invalid_documents(payload, needle):
    with pytest.raises(DirectiveError) as info:
        parse_directive(payload)
    assert needle in str(info.value)


def test_validate_keeps_milestone_last():
    phases = (
        Phase("done", "终局", "里程碑", "milestone", 0, 0),
        Phase("goal", "目标", "探险技术 5", "research", 124, 5),
    )

    errors = validate_phases(phases)

    assert any("milestone" in error and "最后一段" in error for error in errors)


def test_validate_rejects_duplicate_keys_and_bad_levels():
    phases = (
        Phase("a", "A", "目标", "research", 124, 0),
        Phase("a", "B", "目标", "research", 124, 5),
    )

    errors = validate_phases(phases)

    assert any("重复" in error for error in errors)
    assert any("goal_level" in error for error in errors)


def test_load_missing_file_uses_default(tmp_path):
    directive = load_directive(str(tmp_path / "absent.json"))

    assert directive.source == "default"
    assert directive.errors == ()
    assert directive.phases == PHASES


def test_load_broken_json_falls_back_with_reason(tmp_path):
    path = tmp_path / "directive.json"
    path.write_text("{ not json", encoding="utf-8")

    directive = load_directive(str(path))

    assert directive.source == "default"
    assert directive.phases == PHASES
    assert directive.path == str(path)
    assert any("不是合法 JSON" in error for error in directive.errors)


def test_load_invalid_phase_falls_back_with_reason(tmp_path):
    path = tmp_path / "directive.json"
    payload = _payload()
    payload["phases"][0]["goal_kind"] = "spaceship"
    path.write_text(json.dumps(payload), encoding="utf-8")

    directive = load_directive(str(path))

    assert directive.source == "default"
    assert directive.phases == PHASES
    assert any("校验失败" in error for error in directive.errors)


def test_load_valid_file_keeps_phases_and_age(tmp_path):
    path = tmp_path / "directive.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    directive = load_directive(str(path))

    assert directive.source == "file"
    assert directive.errors == ()
    assert [phase.key for phase in directive.phases] == ["goal", "done"]
    assert directive.age_seconds(directive.file_mtime + 120) == pytest.approx(120)


def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "nested" / "directive.json"

    save_directive(str(path), default_directive())
    loaded = load_directive(str(path))

    assert loaded.source == "file"
    assert loaded.phases == PHASES
    assert loaded.constraints == DEFAULT_CONSTRAINTS
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1
    # Atomic write: no temp files left next to the target.
    leftovers = [item.name for item in path.parent.iterdir() if item.name != path.name]
    assert leftovers == []


def test_save_replaces_existing_file(tmp_path):
    path = tmp_path / "directive.json"
    path.write_text("old content", encoding="utf-8")

    save_directive(str(path), default_directive())

    assert json.loads(path.read_text(encoding="utf-8"))["phases"][0]["key"] == "pre_gravity"


def test_planner_uses_injected_directive():
    directive = Directive(
        phases=(
            Phase("graviton", "引力子", "研究引力子技术到 1 级", "research", 199, 1),
            Phase("done", "终局", "里程碑", "milestone", 0, 0),
        )
    )
    research = [_research(199, 0, name="引力子技术")]

    plan = compute_plan(
        [],
        research,
        SimpleNamespace(resources={}),
        {901: 1_000_000},
        directive,
    )

    assert plan.phase == "graviton"
    assert plan.goal == "研究引力子技术到 1 级"
    blocker = next(action for action in plan.actions if action.category == "blocker")
    assert blocker.name == "引力子技术"
    assert blocker.target_level == 1


def test_planner_default_matches_no_directive():
    research = [_research(124, 3)]
    production = SimpleNamespace(resources={})

    with_default = compute_plan([], research, production, {}, default_directive())
    without = compute_plan([], research, production, {})

    assert with_default.as_dict() == without.as_dict()

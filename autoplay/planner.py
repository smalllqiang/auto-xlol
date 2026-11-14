"""Deterministic planner: turn current game state into a ranked, read-only plan.

Two kinds of candidates are produced:

* **blockers** -- the current phase's goal and its dependency chain.  These are
  what the game itself says must be built/researched before the milestone.  The
  goal itself is emitted once its prerequisites are satisfied, so it is the
  terminal blocker instead of being silently skipped.
* **roi** -- production buildings ranked by payback time (cost in metal
  equivalents divided by the estimated hourly gain of one more level).

Nothing here talks to the game except :func:`build_plan`; the computation
itself is pure, so it can be replayed over old snapshots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from newstar_sdk.constants import BUILDINGS, TECHNOLOGIES

from .economy import equivalent_cost, payback_hours, production_delta, scale_cost
from .phases import Phase, detect_phase

REQUIREMENT_RE = re.compile(
    r"^(?P<name>.+?)\s*等级\s*(?P<required>\d+)\s*\((?P<current>\d+)/(?P<need>\d+)\)$"
)

#: Which resource each production building produces.
PRODUCER_RESOURCE: Dict[int, int] = {1: 901, 2: 902, 3: 903, 4: 911}

_NAME_INDEX: Dict[str, Tuple[str, int]] = {}
for _element_id, _element in BUILDINGS.items():
    _NAME_INDEX.setdefault(_element.name_zh, ("building", int(_element_id)))
for _element_id, _element in TECHNOLOGIES.items():
    _NAME_INDEX.setdefault(_element.name_zh, ("research", int(_element_id)))


@dataclass(frozen=True)
class Requirement:
    text: str
    name: str
    kind: str
    element_id: Optional[int]
    required: int
    current: int

    @property
    def met(self) -> bool:
        return self.current >= self.required


@dataclass
class Action:
    category: str  # "blocker" | "roi"
    kind: str  # "building" | "research"
    element_id: int
    name: str
    current_level: int
    target_level: int
    cost: Dict[int, float] = field(default_factory=dict)
    cost_equivalent: float = 0.0
    affordable: bool = False
    missing_resources: Dict[int, float] = field(default_factory=dict)
    reason: str = ""
    depth: int = 0
    payback_hours: Optional[float] = None
    required_level: Optional[int] = None
    steps: int = 1
    total_cost: Dict[int, float] = field(default_factory=dict)
    total_cost_equivalent: float = 0.0
    affordable_total: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "kind": self.kind,
            "element_id": self.element_id,
            "name": self.name,
            "current_level": self.current_level,
            "target_level": self.target_level,
            "cost": {str(key): value for key, value in self.cost.items()},
            "cost_equivalent": round(self.cost_equivalent, 1),
            "affordable": self.affordable,
            "missing_resources": {
                str(key): value for key, value in self.missing_resources.items()
            },
            "reason": self.reason,
            "depth": self.depth,
            "payback_hours": (
                round(self.payback_hours, 2) if self.payback_hours is not None else None
            ),
            "required_level": self.required_level,
            "steps": self.steps,
            "total_cost": {str(key): value for key, value in self.total_cost.items()},
            "total_cost_equivalent": round(self.total_cost_equivalent, 1),
            "affordable_total": self.affordable_total,
        }


@dataclass
class Plan:
    phase: str
    phase_name: str
    goal: str
    goal_status: str = ""
    actions: List[Action] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_name": self.phase_name,
            "goal": self.goal,
            "goal_status": self.goal_status,
            "actions": [action.as_dict() for action in self.actions],
            "notes": list(self.notes),
        }


def parse_requirements(raw: Iterable[str]) -> List[Requirement]:
    parsed: List[Requirement] = []
    for text in raw or []:
        match = REQUIREMENT_RE.match(str(text).strip())
        if not match:
            continue
        name = match.group("name").strip()
        kind, element_id = _NAME_INDEX.get(name, ("unknown", None))
        parsed.append(
            Requirement(
                text=str(text),
                name=name,
                kind=kind,
                element_id=element_id,
                required=int(match.group("required")),
                current=int(match.group("current")),
            )
        )
    return parsed


def _missing_resources(
    item: Any,
    cost: Dict[int, float],
    resources: Dict[int, float],
) -> Dict[int, float]:
    """Return the deficit for the next level, preferring the game's own numbers.

    ``costOverflow``/``missing_resources`` on the page is computed by the game
    from the same state the write will be checked against.  The overview's
    energy field is gross production, so comparing it with ``cost[911]`` can
    mark an unaffordable action as affordable.  Use the page value when it is
    present; fall back to a local subtraction only when it is absent.
    """

    overflow = getattr(item, "cost_overflow", None)
    page_missing = {
        int(key): float(value)
        for key, value in (overflow or {}).items()
        if float(value) > 0
    }
    if page_missing:
        return page_missing
    return {
        int(rid): float(amount) - float(resources.get(int(rid), 0.0))
        for rid, amount in cost.items()
        if float(amount) > float(resources.get(int(rid), 0.0))
    }


def _affordable(cost: Dict[int, float], resources: Dict[int, float]) -> bool:
    return all(float(resources.get(int(rid), 0.0)) >= amount for rid, amount in cost.items())


def _cost_of(item: Any) -> Dict[int, float]:
    return {int(key): float(value) for key, value in (getattr(item, "cost", None) or {}).items()}


def _goal_status(phase: Phase, buildings: Dict[int, Any], research: Dict[int, Any]) -> str:
    if phase.goal_kind == "building":
        item = buildings.get(phase.goal_element)
        level = int(getattr(item, "level", 0) or 0) if item else 0
        return f"{phase.goal}（{level}/{phase.goal_level}）"
    if phase.goal_kind == "research":
        item = research.get(phase.goal_element)
        level = int(getattr(item, "level", 0) or 0) if item else 0
        return f"{phase.goal}（{level}/{phase.goal_level}）"
    return phase.goal


def _make_blocker_action(
    *,
    kind: str,
    target: Any,
    level: int,
    required_level: int,
    steps: int,
    reason: str,
    depth: int,
    resources: Dict[int, float],
) -> Action:
    cost = _cost_of(target)
    total_cost = scale_cost(cost, getattr(target, "factor", None), steps)
    missing = _missing_resources(target, cost, resources)
    affordable = not missing
    return Action(
        category="blocker",
        kind=kind,
        element_id=int(target.id),
        name=str(getattr(target, "name", "")),
        current_level=level,
        target_level=level + 1,
        cost=cost,
        cost_equivalent=equivalent_cost(cost),
        affordable=affordable,
        missing_resources=missing,
        reason=reason,
        depth=depth,
        required_level=required_level,
        steps=steps,
        total_cost=total_cost,
        total_cost_equivalent=equivalent_cost(total_cost),
        affordable_total=(
            affordable if steps == 1 else _affordable(total_cost, resources)
        ),
    )


def expand_requirement_actions(
    requirements: Iterable[str],
    buildings: Dict[int, Any],
    research: Dict[int, Any],
    resources: Dict[int, float],
    *,
    parent_name: str = "",
    depth: int = 0,
    notes: Optional[List[str]] = None,
) -> List[Action]:
    """Expand raw requirement strings into actionable building/research blockers.

    This is the reusable part of the phase-goal planner: it never adds the
    parent (a ship, building or technology) itself, only the unmet prerequisites
    needed to unlock it.  Callers such as the expedition planner can use it to
    turn a ship's ``requirements`` into concrete next actions.
    """

    note_list = notes if notes is not None else []
    actions: List[Action] = []
    seen: set = set()

    def lookup(kind: str, element_id: Optional[int]) -> Optional[Any]:
        if element_id is None:
            return None
        if kind == "building":
            return buildings.get(int(element_id))
        if kind == "research":
            return research.get(int(element_id))
        return None

    def walk(item_name: str, raw_requirements: Iterable[str], item_depth: int) -> None:
        for requirement in parse_requirements(raw_requirements):
            if requirement.met:
                continue
            if requirement.element_id is None:
                note_list.append(f"无法解析前置条件：{requirement.text}")
                continue
            key = (requirement.kind, requirement.element_id)
            if key in seen:
                continue
            seen.add(key)

            target = lookup(requirement.kind, requirement.element_id)
            if target is None:
                note_list.append(f"前置「{requirement.name}」未在页面数据中出现")
                continue

            unmet = [
                item
                for item in parse_requirements(getattr(target, "requirements", []))
                if not item.met
            ]
            if unmet:
                walk(
                    str(getattr(target, "name", requirement.name)),
                    getattr(target, "requirements", []),
                    item_depth + 1,
                )
                continue

            level = int(getattr(target, "level", 0) or 0)
            actions.append(
                _make_blocker_action(
                    kind=requirement.kind,
                    target=target,
                    level=level,
                    required_level=requirement.required,
                    steps=max(1, requirement.required - level),
                    reason=(
                        f"为「{item_name}」解锁：需要 "
                        f"{requirement.required} 级（当前 {level}）"
                    ),
                    depth=item_depth,
                    resources=resources,
                )
            )

    walk(parent_name, requirements, depth)
    return _sort_actions(actions)


def _blocker_actions(
    goal: Any,
    goal_kind: str,
    goal_level: int,
    buildings: Dict[int, Any],
    research: Dict[int, Any],
    resources: Dict[int, float],
    notes: List[str],
) -> List[Action]:
    actions: List[Action] = []

    goal_requirements = parse_requirements(getattr(goal, "requirements", []))
    unmet_requirements = [requirement for requirement in goal_requirements if not requirement.met]
    if unmet_requirements:
        actions.extend(
            expand_requirement_actions(
                getattr(goal, "requirements", []),
                buildings,
                research,
                resources,
                parent_name=str(getattr(goal, "name", "")),
                notes=notes,
            )
        )
    else:
        level = int(getattr(goal, "level", 0) or 0)
        if level >= goal_level:
            return actions
        if not getattr(goal, "requirements_met", True):
            notes.append(
                f"「{getattr(goal, 'name', '阶段目标')}」的前置条件未满足，"
                "但页面文本无法解析出具体项"
            )
            return actions

        actions.append(
            _make_blocker_action(
                kind=goal_kind,
                target=goal,
                level=level,
                required_level=goal_level,
                steps=max(1, goal_level - level),
                reason=f"阶段目标：{getattr(goal, 'name', '')} 升到 {goal_level} 级（当前 {level}）",
                depth=0,
                resources=resources,
            )
        )

    return actions


def _roi_actions(
    buildings: Dict[int, Any],
    production: Any,
    resources: Dict[int, float],
    notes: List[str],
) -> List[Action]:
    actions: List[Action] = []
    produced = getattr(production, "resources", {}) or {}

    for element_id, resource_id in PRODUCER_RESOURCE.items():
        building = buildings.get(element_id)
        if building is None or not getattr(building, "requirements_met", True):
            continue
        level = int(getattr(building, "level", 0) or 0)
        cost = _cost_of(building)
        cost_equivalent = equivalent_cost(cost)
        missing = _missing_resources(building, cost, resources)

        item = produced.get(resource_id)
        observed = abs(float(item.buildings.get(element_id, 0.0))) if item else 0.0
        delta = production_delta(observed, level)
        payback = payback_hours(cost_equivalent, delta)

        if level <= 0:
            reason = "尚未建造，补齐该资源产出"
        elif delta is not None:
            reason = f"每升一级约 +{delta:,.0f}/h"
        else:
            reason = "缺少当前产量，无法估算收益"

        actions.append(
            Action(
                category="roi",
                kind="building",
                element_id=element_id,
                name=str(getattr(building, "name", "")),
                current_level=level,
                target_level=level + 1,
                cost=cost,
                cost_equivalent=cost_equivalent,
                affordable=not missing,
                missing_resources=missing,
                reason=reason,
                payback_hours=payback,
            )
        )
    return actions


def _sort_actions(actions: List[Action]) -> List[Action]:
    blockers = sorted(
        (action for action in actions if action.category == "blocker"),
        key=lambda action: (action.depth, action.cost_equivalent),
    )
    roi = sorted(
        (action for action in actions if action.category == "roi"),
        key=lambda action: (
            action.payback_hours is None,
            action.payback_hours if action.payback_hours is not None else 0.0,
        ),
    )
    return blockers + roi


def compute_plan(
    buildings: Iterable[Any],
    research: Iterable[Any],
    production: Any,
    resources: Dict[int, float],
) -> Plan:
    building_map = {int(item.id): item for item in buildings}
    research_map = {int(item.id): item for item in research}
    phase = detect_phase(buildings, research)

    notes: List[str] = []
    actions: List[Action] = []

    if phase.goal_kind == "building":
        goal = building_map.get(phase.goal_element)
    elif phase.goal_kind == "research":
        goal = research_map.get(phase.goal_element)
    else:
        goal = None

    if phase.goal_kind in {"building", "research"}:
        if goal is None:
            notes.append(
                f"页面没有返回阶段目标元素 {phase.goal_element}，无法生成依赖链"
            )
        else:
            actions.extend(
                _blocker_actions(
                    goal,
                    phase.goal_kind,
                    phase.goal_level,
                    building_map,
                    research_map,
                    resources,
                    notes,
                )
            )
    else:
        notes.append(f"阶段「{phase.name}」的规划尚未实现，仅输出产能候选")

    actions.extend(_roi_actions(building_map, production, resources, notes))

    # Graviton needs a huge amount of energy; surface the deficit explicitly.
    graviton = research_map.get(199)
    if graviton is not None:
        cost = _cost_of(graviton)
        missing = _missing_resources(graviton, cost, resources)
        if missing.get(911):
            needed = float(cost.get(911, 0.0))
            available = max(0.0, needed - float(missing[911]))
            notes.append(
                f"引力子技术需要 {needed:,.0f} 幽能，当前净可用约 {available:,.0f}"
                f"（还差 {float(missing[911]):,.0f}）"
            )

    return Plan(
        phase=phase.key,
        phase_name=phase.name,
        goal=phase.goal,
        goal_status=_goal_status(phase, building_map, research_map),
        actions=_sort_actions(actions),
        notes=notes,
    )


def build_plan(client: Any) -> Plan:
    """Fetch the required pages and compute a plan.  Read-only."""

    buildings = client.get_buildings()
    research = client.get_research()
    production = client.get_production()
    resources = client.get_overview().resources.amounts
    return compute_plan(buildings, research, production, resources)

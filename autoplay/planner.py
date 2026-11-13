"""Deterministic planner: turn current game state into a ranked, read-only plan.

Two kinds of candidates are produced:

* **blockers** -- the dependency chain of the current phase's goal.  These are
  what the game itself says must be built/researched before the milestone.
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
from .phases import PRE_GRAVITY, Phase, detect_phase

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


def _blocker_actions(
    goal: Any,
    buildings: Dict[int, Any],
    research: Dict[int, Any],
    resources: Dict[int, float],
    notes: List[str],
) -> List[Action]:
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

    def walk(item: Any, depth: int) -> None:
        for requirement in parse_requirements(getattr(item, "requirements", [])):
            if requirement.met:
                continue
            if requirement.element_id is None:
                notes.append(f"无法解析前置条件：{requirement.text}")
                continue
            key = (requirement.kind, requirement.element_id)
            if key in seen:
                continue
            seen.add(key)

            target = lookup(requirement.kind, requirement.element_id)
            if target is None:
                notes.append(f"前置「{requirement.name}」未在页面数据中出现")
                continue

            unmet = [r for r in parse_requirements(getattr(target, "requirements", [])) if not r.met]
            if unmet:
                walk(target, depth + 1)
                continue

            level = int(getattr(target, "level", 0) or 0)
            cost = _cost_of(target)
            steps = max(1, requirement.required - level)
            total_cost = scale_cost(cost, getattr(target, "factor", None), steps)
            actions.append(
                Action(
                    category="blocker",
                    kind=requirement.kind,
                    element_id=int(target.id),
                    name=str(getattr(target, "name", requirement.name)),
                    current_level=level,
                    target_level=level + 1,
                    cost=cost,
                    cost_equivalent=equivalent_cost(cost),
                    affordable=_affordable(cost, resources),
                    reason=(
                        f"为「{getattr(item, 'name', '')}」解锁：需要 "
                        f"{requirement.required} 级（当前 {level}）"
                    ),
                    depth=depth,
                    required_level=requirement.required,
                    steps=steps,
                    total_cost=total_cost,
                    total_cost_equivalent=equivalent_cost(total_cost),
                    affordable_total=_affordable(total_cost, resources),
                )
            )

    walk(goal, 0)
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
                affordable=_affordable(cost, resources),
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

    if phase is PRE_GRAVITY:
        goal = building_map.get(phase.goal_element)
        if goal is None:
            notes.append("页面没有返回资源处理单元，无法生成依赖链")
        else:
            actions.extend(
                _blocker_actions(goal, building_map, research_map, resources, notes)
            )
    else:
        notes.append(f"阶段「{phase.name}」的规划尚未实现，仅输出产能候选")

    actions.extend(_roi_actions(building_map, production, resources, notes))

    # Graviton needs a huge amount of energy; surface the gap explicitly.
    graviton = research_map.get(199)
    if graviton is not None and not getattr(graviton, "requirements_met", True):
        needed = float(_cost_of(graviton).get(911, 0.0))
        available = float(resources.get(911, 0.0))
        if needed > available:
            notes.append(
                f"引力子技术需要 {needed:,.0f} 幽能，当前只有 {available:,.0f}"
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

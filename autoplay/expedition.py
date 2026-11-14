"""Read-only expedition readiness and fleet-score planning.

The game ties expedition slots to the square root of the expedition technology
level (1 -> 1 slot, 4 -> 2, 9 -> 3, ...).  An expedition fleet is scored by the
metal+crystal cost of its ships divided by 1000; the guide uses 500,000 of that
"no-deuterium score" as the high-resource threshold.  This module reports the
gap and a ship build recommendation, but it never sends a fleet or builds a
ship.  Execution remains a separate, explicit step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from newstar_sdk.constants import Mission
from newstar_sdk.models import Coordinates, FleetInfo, PlanetInfo, ResearchInfo, ShipInfo

EXPEDITION_TECH_ID = 124
EXPEDITION_TARGET_POSITION = 16

#: Default no-deuterium score per expedition fleet from the guide
#: (high-resource threshold).  This is a prior, not a game constant; callers
#: can override it.
DEFAULT_TARGET_SCORE = 500_000.0


@dataclass
class ShipRecommendation:
    element_id: int
    name: str
    score_per_ship: float
    needed: int
    total_cost: Dict[int, float] = field(default_factory=dict)
    affordable: bool = False
    requirements_met: bool = True
    requirements: List[str] = field(default_factory=list)
    build_seconds: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "name": self.name,
            "score_per_ship": round(self.score_per_ship, 3),
            "needed": self.needed,
            "total_cost": {str(key): value for key, value in self.total_cost.items()},
            "affordable": self.affordable,
            "requirements_met": self.requirements_met,
            "requirements": list(self.requirements),
            "build_seconds": self.build_seconds,
        }


@dataclass
class ExpeditionGroup:
    index: int
    ships: Dict[int, int] = field(default_factory=dict)
    score: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "ships": {str(key): value for key, value in self.ships.items()},
            "score": round(self.score, 3),
        }


@dataclass
class ExpeditionReport:
    expedition_tech: int
    slots: int
    active_expeditions: int
    free_slots: int
    current_score: float
    target_score: float
    required_score: float
    score_gap: float
    target: Optional[str]
    available_ships: Dict[int, int]
    ship_names: Dict[int, str] = field(default_factory=dict)
    active_fleets: List[Dict[str, Any]] = field(default_factory=list)
    fleet_groups: List[ExpeditionGroup] = field(default_factory=list)
    recommendation: Optional[ShipRecommendation] = None
    can_send: bool = False
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "expedition_tech": self.expedition_tech,
            "slots": self.slots,
            "active_expeditions": self.active_expeditions,
            "free_slots": self.free_slots,
            "current_score": round(self.current_score, 3),
            "target_score": self.target_score,
            "required_score": self.required_score,
            "score_gap": round(self.score_gap, 3),
            "target": self.target,
            "available_ships": {
                str(key): value for key, value in self.available_ships.items()
            },
            "ship_names": {str(key): value for key, value in self.ship_names.items()},
            "active_fleets": [dict(fleet) for fleet in self.active_fleets],
            "fleet_groups": [group.as_dict() for group in self.fleet_groups],
            "recommendation": (
                self.recommendation.as_dict() if self.recommendation is not None else None
            ),
            "can_send": self.can_send,
            "notes": list(self.notes),
        }


def fleet_score(cost: Mapping[int, float]) -> float:
    """No-deuterium fleet score: (metal + crystal) / 1000."""

    return (float(cost.get(901, 0.0)) + float(cost.get(902, 0.0))) / 1000.0


def _is_expedition(fleet: FleetInfo) -> bool:
    if fleet.mission_id is not None and int(fleet.mission_id) == int(Mission.EXPEDITION):
        return True
    mission = (fleet.mission or "").casefold()
    return "探险" in mission or "远征" in mission or "expedition" in mission


def _fleet_summary(fleet: FleetInfo) -> Dict[str, Any]:
    target = fleet.target_coordinates
    return {
        "mission": fleet.mission,
        "mission_id": fleet.mission_id,
        "target": str(target) if target is not None else None,
        "returning": bool(fleet.returning),
        "ships": dict(fleet.ships),
    }


def _ship_rank(ship: ShipInfo) -> tuple:
    """Prefer no-deuterium ships, then more score per hull, then metal-heavy."""

    cost = ship.cost or {}
    score = fleet_score(cost)
    metal = float(cost.get(901, 0.0))
    crystal = float(cost.get(902, 0.0))
    metal_share = metal / (metal + crystal) if metal + crystal > 0 else 0.0
    build_seconds = ship.build_seconds if ship.build_seconds is not None else math.inf
    return (
        float(cost.get(903, 0.0)) > 0,
        -score,
        -metal_share,
        build_seconds,
    )


def _target_coordinates(planet: Optional[PlanetInfo]) -> Optional[str]:
    if planet is None or planet.coordinates is None:
        return None
    current = planet.coordinates
    target = Coordinates(current.galaxy, current.system, EXPEDITION_TARGET_POSITION)
    return f"{target.galaxy}:{target.system}:{target.planet}"


def _group_fleets(
    available_ships: Mapping[int, int],
    shipyard: Iterable[ShipInfo],
    free_slots: int,
    notes: List[str],
) -> List[ExpeditionGroup]:
    """Split available ships into up to ``free_slots`` balanced groups.

    Counts are distributed without expanding every hull, so a fleet of
    millions of probes stays cheap to plan.  Remainders go to the currently
    weakest groups, which keeps the score gap between groups small.
    """

    if free_slots <= 0:
        return []

    costs = {
        int(item.id): item.cost or {}
        for item in shipyard
        if not item.is_defense
    }
    usable = {
        int(ship_id): int(count)
        for ship_id, count in available_ships.items()
        if int(count) > 0 and int(ship_id) in costs
    }
    total_ships = sum(usable.values())
    if total_ships <= 0:
        return []

    group_count = min(int(free_slots), total_ships)
    if group_count < int(free_slots):
        notes.append(
            f"可派遣舰船只够编成 {group_count} 队（空闲航道 {free_slots}）"
        )

    groups = [ExpeditionGroup(index=index + 1) for index in range(group_count)]
    ordered = sorted(
        usable.items(),
        key=lambda item: -fleet_score(costs[item[0]]),
    )
    for ship_id, count in ordered:
        score = fleet_score(costs[ship_id])
        base, remainder = divmod(count, group_count)
        if base:
            for group in groups:
                group.ships[ship_id] = group.ships.get(ship_id, 0) + base
                group.score += base * score
        if remainder:
            weakest = sorted(groups, key=lambda group: (group.score, group.index))
            for group in weakest[:remainder]:
                group.ships[ship_id] = group.ships.get(ship_id, 0) + 1
                group.score += score

    return [group for group in groups if group.ships]


def _recommend(
    shipyard: Iterable[ShipInfo],
    resources: Mapping[int, float],
    score_gap: float,
    ship: Optional[int],
    notes: List[str],
) -> Optional[ShipRecommendation]:
    ships = [
        item
        for item in shipyard
        if not item.is_defense and fleet_score(item.cost or {}) > 0
    ]
    if ship is not None:
        ships = [item for item in ships if item.id == int(ship)]

    if not ships:
        notes.append("没有可用的舰船数据，无法生成建造建议")
        return None

    eligible = [item for item in ships if item.requirements_met]
    pool = eligible or ships
    best = min(pool, key=_ship_rank)
    score = fleet_score(best.cost)
    gap = max(0.0, float(score_gap))
    needed = math.ceil(gap / score) if gap > 0 and score > 0 else 0
    total_cost = (
        {int(rid): float(amount) * needed for rid, amount in best.cost.items()}
        if needed
        else {}
    )
    affordable = best.requirements_met and all(
        float(resources.get(int(rid), 0.0)) >= amount
        for rid, amount in total_cost.items()
    )
    if needed == 0:
        notes.append("当前积分已达到目标，不需要再建造探险舰船")
    elif not best.requirements_met:
        notes.append(
            f"推荐舰船「{best.name}」前置未满足：{', '.join(best.requirements)}"
        )
    elif not affordable:
        notes.append(f"推荐舰船「{best.name}」的建造资源不足")

    return ShipRecommendation(
        element_id=int(best.id),
        name=str(best.name),
        score_per_ship=score,
        needed=needed,
        total_cost=total_cost,
        affordable=affordable,
        requirements_met=bool(best.requirements_met),
        requirements=list(best.requirements),
        build_seconds=best.build_seconds,
    )


def compute_expedition_report(
    *,
    research: Iterable[ResearchInfo],
    shipyard: Iterable[ShipInfo],
    available_ships: Mapping[int, int],
    fleets: Iterable[FleetInfo],
    planet: Optional[PlanetInfo],
    resources: Mapping[int, float],
    target_score: float = DEFAULT_TARGET_SCORE,
    ship: Optional[int] = None,
) -> ExpeditionReport:
    """Compute a read-only expedition readiness report from parsed page data."""

    research_list = list(research)
    shipyard_list = list(shipyard)
    fleet_list = list(fleets)

    tech = next(
        (int(item.level) for item in research_list if int(item.id) == EXPEDITION_TECH_ID),
        0,
    )
    slots = math.isqrt(tech) if tech > 0 else 0
    active = [fleet for fleet in fleet_list if _is_expedition(fleet)]
    free_slots = max(0, slots - len(active))

    costs = {
        int(item.id): item.cost or {}
        for item in shipyard_list
        if not item.is_defense
    }
    current_score = 0.0
    missing_cost = 0
    for ship_id, count in available_ships.items():
        count = int(count)
        if count <= 0:
            continue
        cost = costs.get(int(ship_id))
        if cost is None:
            missing_cost += 1
            continue
        current_score += count * fleet_score(cost)

    target = _target_coordinates(planet)
    required_score = float(target_score) * free_slots
    score_gap = max(0.0, required_score - current_score)
    available_ids = {int(ship_id) for ship_id in available_ships}
    ship_names = {
        int(item.id): str(item.name)
        for item in shipyard_list
        if int(item.id) in available_ids
    }
    notes: List[str] = []
    if tech < 1:
        notes.append("探险技术未解锁，先研究探险技术 124")
    if slots == 0:
        notes.append("当前没有探险航道")
    elif not free_slots:
        notes.append("探险航道已满，等待在途/返航舰队")
    if not available_ships:
        notes.append("没有可派遣舰船")
    elif missing_cost:
        notes.append(f"有 {missing_cost} 种可派遣舰船缺少造价数据，未计入积分")
    if target is None:
        notes.append("无法从概览确定当前星球坐标")
    if float(resources.get(903, 0.0)) <= 0:
        notes.append("重氢为 0，发送前需确认燃料")
    if free_slots > 0 and score_gap > 0:
        notes.append(
            f"每队目标 {float(target_score):,.0f}，{free_slots} 个空闲航道需要 "
            f"{required_score:,.0f}；当前 {current_score:,.0f}，还差 {score_gap:,.0f}"
        )

    fleet_groups = _group_fleets(
        available_ships,
        shipyard_list,
        free_slots,
        notes,
    )
    short_groups = [
        group.index for group in fleet_groups if group.score < float(target_score)
    ]
    if short_groups:
        notes.append(f"{len(short_groups)} 支编队低于每队目标")

    recommendation = (
        _recommend(shipyard_list, resources, score_gap, ship, notes)
        if free_slots > 0
        else None
    )
    if recommendation is not None and recommendation.needed:
        notes.append(
            f"按「{recommendation.name}」补足目标还需 {recommendation.needed} 艘"
        )

    return ExpeditionReport(
        expedition_tech=tech,
        slots=slots,
        active_expeditions=len(active),
        free_slots=free_slots,
        current_score=current_score,
        target_score=float(target_score),
        required_score=required_score,
        score_gap=score_gap,
        target=target,
        available_ships={int(key): int(value) for key, value in available_ships.items()},
        ship_names=ship_names,
        active_fleets=[_fleet_summary(fleet) for fleet in active],
        fleet_groups=fleet_groups,
        recommendation=recommendation,
        can_send=free_slots > 0 and current_score > 0 and target is not None,
        notes=notes,
    )


def build_expedition_report(
    client: Any,
    *,
    target_score: float = DEFAULT_TARGET_SCORE,
    ship: Optional[int] = None,
) -> ExpeditionReport:
    """Fetch the required pages and compute a report.  Read-only."""

    research = client.get_research()
    shipyard = client.get_shipyard("fleet")
    available = {
        int(ship_id): int(item.count)
        for ship_id, item in client.get_available_ships().items()
        if int(item.count) > 0
    }
    if not available:
        available = {
            int(item.id): int(item.available)
            for item in shipyard
            if int(item.available) > 0
        }
    fleets = client.get_fleets()
    overview = client.get_overview()
    return compute_expedition_report(
        research=research,
        shipyard=shipyard,
        available_ships=available,
        fleets=fleets,
        planet=overview.planet,
        resources=overview.resources.amounts,
        target_score=target_score,
        ship=ship,
    )

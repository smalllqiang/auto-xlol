"""Build the expedition-recommended ship behind an explicit safety gate.

Dispatching an expedition needs hulls.  This module turns a report's ship
recommendation into at most one ``build_ships`` call.  It is dry-run by default,
requires an explicit ship allow-list for real building, and caps the amount per
call; queueing, retries and multi-ship orchestration are left to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set

from newstar_sdk.exceptions import NewStarError

from .expedition import ExpeditionReport


@dataclass
class ShipBuildResult:
    status: str  # "dry_run" | "built" | "skipped" | "error"
    element_id: Optional[int] = None
    name: str = ""
    count: int = 0
    reason: str = ""
    queues: Dict[str, int] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "element_id": self.element_id,
            "name": self.name,
            "count": self.count,
            "reason": self.reason,
            "queues": dict(self.queues),
            "evidence": dict(self.evidence),
        }


def _ship_queue_length(client: Any) -> Optional[int]:
    try:
        return len(client.get_ship_queue())
    except NewStarError:
        return None


def build_recommended_ships(
    client: Any,
    report: ExpeditionReport,
    *,
    count: int = 1,
    max_count: Optional[int] = None,
    dry_run: bool = True,
    allowed_elements: Optional[Iterable[int]] = None,
) -> ShipBuildResult:
    """Build ``count`` of the report's recommended ship, or simulate it."""

    recommendation = report.recommendation
    if recommendation is None:
        return ShipBuildResult(status="skipped", reason="报告没有舰船建造建议")

    requested = int(count)
    if requested < 1:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            reason="建造数量必须至少为 1",
        )
    if max_count is not None and requested > int(max_count):
        requested = int(max_count)
    if requested < 1:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            reason="max_count 必须至少为 1",
        )
    if not recommendation.requirements_met:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason="推荐舰船前置未满足",
            evidence={"blockers": [action.as_dict() for action in recommendation.blockers]},
        )
    if recommendation.blockers:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason="推荐舰船仍有未完成的前置动作",
            evidence={"blockers": [action.as_dict() for action in recommendation.blockers]},
        )

    allowed: Optional[Set[int]] = (
        set(int(item) for item in allowed_elements)
        if allowed_elements is not None
        else None
    )
    if not dry_run and allowed is None:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason="执行模式必须提供舰船允许列表",
        )
    if allowed is not None and recommendation.element_id not in allowed:
        return ShipBuildResult(
            status="skipped",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason="推荐舰船不在允许列表",
        )

    if dry_run:
        return ShipBuildResult(
            status="dry_run",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason=(
                f"计划建造 {recommendation.name} × {requested}"
                f"（{recommendation.element_id}）"
            ),
            queues={"shipyard": _ship_queue_length(client) or 0},
        )

    try:
        queue = client.build_ships({recommendation.element_id: requested})
    except (NewStarError, ValueError) as exc:
        return ShipBuildResult(
            status="error",
            element_id=recommendation.element_id,
            name=recommendation.name,
            count=requested,
            reason=f"{type(exc).__name__}: {exc}",
            queues={"shipyard": _ship_queue_length(client) or 0},
        )

    return ShipBuildResult(
        status="built",
        element_id=recommendation.element_id,
        name=recommendation.name,
        count=requested,
        reason=f"已建造 {recommendation.name} × {requested}",
        queues={"shipyard": _ship_queue_length(client) or 0},
        evidence={"queue_entries": len(queue)},
    )

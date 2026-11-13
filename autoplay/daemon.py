"""One cycle of the unattended loop: plan, then maybe act.

The loop itself (timing, locking, journaling, signals) lives in
``scripts/run_forever.py``.  Keeping the cycle here makes it testable without
sleeping, signals or a real client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Set

from .executor import execute_plan
from .planner import build_plan


@dataclass
class DaemonConfig:
    dry_run: bool = True
    allowed_elements: Optional[Iterable[int]] = None
    max_cost_equivalent: Optional[float] = None
    interval_seconds: float = 300.0
    backoff_base: float = 30.0
    backoff_max: float = 900.0
    max_failures: int = 10


@dataclass
class CycleResult:
    phase: str
    goal: str
    status: str
    reason: str
    queues: Dict[str, int] = field(default_factory=dict)
    action: Optional[Dict[str, Any]] = None
    notes: list = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "goal": self.goal,
            "status": self.status,
            "reason": self.reason,
            "queues": dict(self.queues),
            "action": self.action,
            "notes": list(self.notes),
        }


def backoff_delay(
    failures: int, base: float = 30.0, maximum: float = 900.0
) -> float:
    """Exponential backoff after ``failures`` consecutive errors."""

    if failures <= 0:
        return 0.0
    return min(float(maximum), float(base) * (2 ** (failures - 1)))


def run_cycle(client: Any, config: DaemonConfig) -> CycleResult:
    """Plan and execute at most one action.  Raises if the client fails."""

    plan = build_plan(client)
    allowed: Optional[Set[int]] = (
        set(int(item) for item in config.allowed_elements)
        if config.allowed_elements is not None
        else None
    )
    result = execute_plan(
        client,
        plan,
        dry_run=config.dry_run,
        allowed_elements=allowed,
        max_cost_equivalent=config.max_cost_equivalent,
    )
    return CycleResult(
        phase=plan.phase,
        goal=plan.goal_status,
        status=result.status,
        reason=result.reason,
        queues=dict(result.queues),
        action=result.action.as_dict() if result.action is not None else None,
        notes=list(plan.notes),
    )

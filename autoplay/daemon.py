"""One cycle of the unattended loop: observe, plan, maybe act.

The loop itself (timing, locking, journaling, signals) lives in
``scripts/run_forever.py``.  Keeping the cycle here makes it testable without
sleeping, signals or a real client.

Each cycle reads the message counters (one request) so espionage/battle reports
are noticed even when no full snapshot is due.  Full state diffing stays on the
``--snapshot-every`` cadence in the script.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from newstar_sdk.events import Event, diff_message_counters
from newstar_sdk.exceptions import NewStarError

from .executor import ExecutionResult, execute_plan
from .planner import build_plan
from .reactions import Reaction, has_alert, react


@dataclass
class DaemonConfig:
    dry_run: bool = True
    allowed_elements: Optional[Iterable[int]] = None
    max_cost_equivalent: Optional[float] = None
    interval_seconds: float = 300.0
    backoff_base: float = 30.0
    backoff_max: float = 900.0
    max_failures: int = 10
    track_messages: bool = True
    pause_on_alert: bool = False


@dataclass
class CycleResult:
    phase: str
    goal: str
    status: str
    reason: str
    queues: Dict[str, int] = field(default_factory=dict)
    action: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    counters: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "goal": self.goal,
            "status": self.status,
            "reason": self.reason,
            "queues": dict(self.queues),
            "action": self.action,
            "notes": list(self.notes),
            "events": list(self.events),
            "alerts": list(self.alerts),
        }


def backoff_delay(
    failures: int, base: float = 30.0, maximum: float = 900.0
) -> float:
    """Exponential backoff after ``failures`` consecutive errors."""

    if failures <= 0:
        return 0.0
    return min(float(maximum), float(base) * (2 ** (failures - 1)))


def _counter_map(client: Any) -> Dict[int, Dict[str, Any]]:
    counters = client.get_message_counters()
    return {
        int(counter.category): {
            "category": int(counter.category),
            "name": counter.category_name,
            "unread": int(counter.unread),
            "total": int(counter.total),
        }
        for counter in counters.values()
    }


def _observe(
    client: Any, config: DaemonConfig, previous: Optional[Dict[int, Dict[str, Any]]]
) -> tuple:
    """Return ``(events, reactions, counters)`` for this cycle."""

    if not config.track_messages:
        return [], [], {}

    try:
        current = _counter_map(client)
    except NewStarError as exc:
        event = Event("message_check_failed", "warning", {"error": str(exc)})
        reaction = Reaction("alert", f"消息计数读取失败：{exc}", event)
        return [event], [reaction], {}

    if previous is None:
        # First cycle: establish the baseline instead of alerting on old mail.
        return [], [], current

    events = diff_message_counters(previous, current)
    return events, react(events), current


def run_cycle(
    client: Any,
    config: DaemonConfig,
    *,
    previous_counters: Optional[Dict[int, Dict[str, Any]]] = None,
) -> CycleResult:
    """Observe, plan and execute at most one action.  Raises if planning fails."""

    events, reactions, counters = _observe(client, config, previous_counters)
    alerts = [reaction for reaction in reactions if reaction.kind == "alert"]

    plan = build_plan(client)

    if config.pause_on_alert and has_alert(reactions):
        result = ExecutionResult(
            status="paused",
            reason="存在告警，已暂停执行（--pause-on-alert）",
            queues={},
        )
    else:
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
        events=[event.as_dict() for event in events],
        alerts=[alert.as_dict() for alert in alerts],
        counters=counters,
    )

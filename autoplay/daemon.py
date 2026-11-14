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
    queue_items: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    action: Optional[Dict[str, Any]] = None
    plan_top: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    alerts: List[Dict[str, Any]] = field(default_factory=list)
    counters: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    messages: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "goal": self.goal,
            "status": self.status,
            "reason": self.reason,
            "queues": dict(self.queues),
            "queue_items": {key: list(value) for key, value in self.queue_items.items()},
            "action": self.action,
            "plan_top": list(self.plan_top),
            "notes": list(self.notes),
            "events": list(self.events),
            "alerts": list(self.alerts),
            "messages": dict(self.messages),
            "evidence": dict(self.evidence),
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


def _messages_summary(counters: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Compact unread summary for the journal/dashboard.

    Category 100 aggregates the inbox and 999 is the outbox; summing every tab
    would double count, so use the aggregate when present.
    """

    if not counters:
        return {}
    inbox = counters.get(100) or {}
    normal = [item for key, item in counters.items() if key not in (100, 999)]
    unread = (
        int(inbox.get("unread", 0))
        if inbox
        else sum(int(item.get("unread", 0)) for item in normal)
    )
    total = (
        int(inbox.get("total", 0))
        if inbox
        else sum(int(item.get("total", 0)) for item in normal)
    )
    categories = [
        {
            "category": int(item.get("category", 0)),
            "name": item.get("name") or str(item.get("category", "")),
            "unread": int(item.get("unread", 0)),
        }
        for item in sorted(normal, key=lambda entry: int(entry.get("category", 0)))
        if int(item.get("unread", 0)) > 0
    ]
    return {
        "unread": unread,
        "total": total,
        "outbox": int((counters.get(999) or {}).get("total", 0)),
        "categories": categories,
    }


def _same_action(action: Any, selected: Any) -> bool:
    if selected is None:
        return False
    return (
        int(action.element_id) == int(selected.element_id)
        and action.kind == selected.kind
    )


def _plan_top(plan: Any, selected: Any, limit: int = 6) -> List[Dict[str, Any]]:
    """First few candidates, with the selected one flagged.

    The TUI needs to explain *why* this action and not the others; the chosen
    action is appended if it falls outside the top N.
    """

    items: List[Dict[str, Any]] = []
    for action in list(plan.actions)[:limit]:
        item = action.as_dict()
        item["selected"] = _same_action(action, selected)
        items.append(item)
    if selected is not None and not any(item["selected"] for item in items):
        item = selected.as_dict()
        item["selected"] = True
        items.append(item)
    return items


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
        queue_items={key: list(value) for key, value in result.queue_items.items()},
        action=result.action.as_dict() if result.action is not None else None,
        plan_top=_plan_top(plan, result.action),
        notes=list(plan.notes),
        events=[event.as_dict() for event in events],
        alerts=[alert.as_dict() for alert in alerts],
        counters=counters,
        messages=_messages_summary(counters),
        evidence=dict(result.evidence),
    )

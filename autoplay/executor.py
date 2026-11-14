"""Execute at most one planned action, behind an explicit safety gate.

This is deliberately conservative:

* **dry run by default** -- the caller must pass ``dry_run=False`` to mutate;
* **one action per call** -- no batching, so every step can be inspected;
* **allow-list required** -- an element id must be explicitly allowed;
* **queue aware** -- a busy building/research queue blocks that kind;
* **no blind retry** -- a post-POST rejection is reported and stops the cycle;
  a pre-POST rejection may fall through to the next eligible action.

The executor does not write files; it returns an :class:`ExecutionResult` and
the caller journals it.  That keeps this module testable and replayable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from newstar_sdk.exceptions import ActionRejected, NewStarError

from .planner import Action, Plan

#: 科研实验室 / Research Lab.  The game rejects upgrading it while a research
#: is running (verified on the live instance), so the executor skips it.
RESEARCH_LAB_ID = 31

#: How many candidates may be rejected by a pre-POST check in one cycle.  A
#: rejection here means nothing was queued, so trying a different action is
#: safe; the cap keeps a stale plan from turning into a burst of requests.
MAX_PRECHECK_REJECTIONS = 3


@dataclass
class ExecutionResult:
    status: str  # "dry_run" | "executed" | "skipped" | "error"
    action: Optional[Action] = None
    reason: str = ""
    queues: Dict[str, int] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "reason": self.reason,
            "queues": dict(self.queues),
            "evidence": dict(self.evidence),
        }
        if self.action is not None:
            payload["action"] = self.action.as_dict()
        return payload


def _queue_lengths(client: Any) -> Dict[str, int]:
    return {
        "building": len(client.get_building_queue()),
        "research": len(client.get_research_queue()),
    }


def _action_key(action: Action) -> Tuple[str, int]:
    return (action.kind, int(action.element_id))


def _select(
    plan: Plan,
    *,
    allowed: Optional[Set[int]],
    building_queue: Iterable[Any],
    research_queue: Iterable[Any],
    max_cost_equivalent: Optional[float],
    excluded: Optional[Set[Tuple[str, int]]] = None,
) -> Tuple[Optional[Action], List[str]]:
    """Return ``(action, skip_reasons)`` for the first eligible action."""

    excluded = excluded or set()
    reasons: List[str] = []
    for action in plan.actions:
        if action.category not in {"blocker", "roi"}:
            continue
        if _action_key(action) in excluded:
            continue
        if allowed is not None and action.element_id not in allowed:
            reasons.append(f"{action.name}: 不在允许列表")
            continue
        if not action.affordable:
            reasons.append(f"{action.name}: 资源不足")
            continue
        if max_cost_equivalent is not None and action.cost_equivalent > max_cost_equivalent:
            reasons.append(f"{action.name}: 超过单步预算")
            continue
        if action.kind == "building" and list(building_queue):
            reasons.append(f"{action.name}: 建造队列占用中")
            continue
        if (
            action.kind == "building"
            and action.element_id == RESEARCH_LAB_ID
            and list(research_queue)
        ):
            reasons.append(f"{action.name}: 研究进行中，实验室暂不可升级")
            continue
        if action.kind == "research" and list(research_queue):
            reasons.append(f"{action.name}: 研究队列占用中")
            continue
        return action, reasons
    return None, reasons


def execute_plan(
    client: Any,
    plan: Plan,
    *,
    dry_run: bool = True,
    allowed_elements: Optional[Iterable[int]] = None,
    queues: Optional[Dict[str, int]] = None,
    max_cost_equivalent: Optional[float] = None,
) -> ExecutionResult:
    """Run (or simulate) the first eligible action of ``plan``."""

    building_queue = list(client.get_building_queue())
    research_queue = list(client.get_research_queue())
    queue_lengths = queues or {
        "building": len(building_queue),
        "research": len(research_queue),
    }

    allowed = set(int(item) for item in allowed_elements) if allowed_elements is not None else None

    if not dry_run and allowed is None:
        return ExecutionResult(
            status="skipped",
            reason="执行模式必须提供允许列表",
            queues=queue_lengths,
        )

    excluded: Set[Tuple[str, int]] = set()
    rejected: List[Dict[str, Any]] = []
    last_action: Optional[Action] = None

    while True:
        action, skip_reasons = _select(
            plan,
            allowed=allowed,
            building_queue=building_queue,
            research_queue=research_queue,
            max_cost_equivalent=max_cost_equivalent,
            excluded=excluded,
        )

        if action is None:
            if rejected:
                names = "、".join(item["name"] for item in rejected)
                return ExecutionResult(
                    status="error",
                    action=last_action,
                    reason=f"预检查拒绝后没有更多可执行动作：{names}",
                    queues=queue_lengths,
                    evidence={"rejected": rejected, "skipped": skip_reasons},
                )
            return ExecutionResult(
                status="skipped",
                reason="；".join(skip_reasons) if skip_reasons else "没有可执行的动作",
                queues=queue_lengths,
            )

        last_action = action

        if dry_run:
            return ExecutionResult(
                status="dry_run",
                action=action,
                reason=f"计划执行：{action.name} {action.current_level}->{action.target_level}",
                queues=queue_lengths,
            )

        try:
            if action.kind == "building":
                queue = client.upgrade_building(action.element_id, levels=1)
            elif action.kind == "research":
                queue = client.research(action.element_id, levels=1)
            else:
                return ExecutionResult(
                    status="skipped",
                    action=action,
                    reason=f"不支持的动作类型：{action.kind}",
                    queues=queue_lengths,
                )
        except ActionRejected as exc:
            # A rejection raised before the POST means nothing was queued.
            # Skipping to the next candidate is safe; a rejection after the
            # POST is ambiguous and must stop this cycle.
            if not getattr(exc, "before_post", False):
                return ExecutionResult(
                    status="error",
                    action=action,
                    reason=f"{type(exc).__name__}: {exc}",
                    queues=queue_lengths,
                )
            rejected.append(
                {
                    "element_id": action.element_id,
                    "name": action.name,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            excluded.add(_action_key(action))
            if len(rejected) >= MAX_PRECHECK_REJECTIONS:
                names = "、".join(item["name"] for item in rejected)
                return ExecutionResult(
                    status="error",
                    action=action,
                    reason=f"连续 {len(rejected)} 条动作在预检查阶段被拒：{names}",
                    queues=queue_lengths,
                    evidence={"rejected": rejected, "skipped": skip_reasons},
                )
            continue
        except NewStarError as exc:
            return ExecutionResult(
                status="error",
                action=action,
                reason=f"{type(exc).__name__}: {exc}",
                queues=queue_lengths,
            )

        try:
            after = _queue_lengths(client)
        except NewStarError:
            after = queue_lengths

        reason = f"已执行：{action.name} {action.current_level}->{action.target_level}"
        evidence: Dict[str, Any] = {"queue_entries": len(queue)}
        if rejected:
            names = "、".join(item["name"] for item in rejected)
            reason += f"（跳过预检查被拒：{names}）"
            evidence["rejected"] = rejected
        return ExecutionResult(
            status="executed",
            action=action,
            reason=reason,
            queues=after,
            evidence=evidence,
        )

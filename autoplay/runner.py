"""Execute a typed plan: ordered steps with preconditions, stop on obstacle.

A plan is not prose that has to be compiled into actions (ADR-031).  It is a
list of already-typed steps::

    {"tool": "upgrade_building", "args": {"element": 1},
     "when": {"kind": "queue_empty", "queue": "building"},
     "until": {"kind": "level_at_least", "element": 1, "level": 4},
     "why": "..."}

``when`` is checked *before* the step and stops the run when it does not hold.
``until`` is checked *after* a write and stops the run when the world did not
move as expected.  Both moments hand control back to the model with the facts
it needs to re-plan; the runner never guesses or retries.

Nothing here writes files.  The caller journals the report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from newstar_sdk.exceptions import NewStarError

from .phases import detect_phase
from .tools import ToolRegistry, ToolResult

#: Conditions understood by :func:`check`.  Unknown kinds stop the run instead
#: of being ignored -- a typo must not turn into a skipped guard.
CONDITION_KINDS = ("always", "queue_empty", "level_at_least", "resources_at_least", "phase")


@dataclass
class Step:
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    when: Optional[Dict[str, Any]] = None
    until: Optional[Dict[str, Any]] = None
    why: str = ""

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"tool": self.tool, "args": dict(self.args)}
        if self.when:
            payload["when"] = dict(self.when)
        if self.until:
            payload["until"] = dict(self.until)
        if self.why:
            payload["why"] = self.why
        return payload


@dataclass
class Facts:
    """A cheap, read-only view used to evaluate conditions."""

    levels: Dict[int, int] = field(default_factory=dict)
    research_levels: Dict[int, int] = field(default_factory=dict)
    queues: Dict[str, int] = field(default_factory=dict)
    resources: Dict[int, float] = field(default_factory=dict)
    phase: str = ""
    error: str = ""

    def level(self, element_id: int) -> int:
        return int(self.levels.get(int(element_id), self.research_levels.get(int(element_id), 0)))


@dataclass
class StepResult:
    index: int
    step: Step
    status: str  # "ok" | "blocked" | "failed" | "unexpected" | "dry_run" | "skipped"
    tool_result: Optional[ToolResult] = None
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "step": self.step.as_dict(),
            "status": self.status,
            "message": self.message,
            "result": self.tool_result.as_dict() if self.tool_result is not None else None,
        }


@dataclass
class RunReport:
    status: str  # "completed" | "empty" | "blocked" | "failed" | "unexpected"
    steps: List[StepResult] = field(default_factory=list)
    stopped_at: Optional[int] = None
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "stopped_at": self.stopped_at,
            "reason": self.reason,
            "steps": [step.as_dict() for step in self.steps],
        }


def parse_step(payload: Mapping[str, Any], index: int = 0) -> Step:
    """Validate one model-provided step; raise ``ValueError`` on bad shape."""

    if not isinstance(payload, Mapping):
        raise ValueError(f"第 {index + 1} 步必须是对象")
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise ValueError(f"第 {index + 1} 步缺少 tool")
    args = payload.get("args") or {}
    if not isinstance(args, Mapping):
        raise ValueError(f"第 {index + 1} 步的 args 必须是对象")
    when = payload.get("when")
    until = payload.get("until")
    for key, value in (("when", when), ("until", until)):
        if value is not None and not isinstance(value, Mapping):
            raise ValueError(f"第 {index + 1} 步的 {key} 必须是对象")
    return Step(
        tool=str(tool),
        args=dict(args),
        when=dict(when) if when else None,
        until=dict(until) if until else None,
        why=str(payload.get("why") or ""),
    )


def parse_steps(payload: Iterable[Any]) -> List[Step]:
    steps: List[Step] = []
    for index, item in enumerate(payload or []):
        steps.append(parse_step(item, index))
    return steps


def load_facts(client: Any, directive: Any = None) -> Facts:
    """Read what conditions need.  Failures are recorded, not raised."""

    facts = Facts()
    try:
        buildings = client.get_buildings()
        facts.levels = {int(item.id): int(item.level) for item in buildings}
    except NewStarError as exc:
        facts.error = f"buildings: {exc}"
    try:
        research = client.get_research()
        facts.research_levels = {int(item.id): int(item.level) for item in research}
    except NewStarError as exc:
        facts.error = f"{facts.error}; research: {exc}".lstrip("; ")
    try:
        facts.queues = {
            "building": len(client.get_building_queue()),
            "research": len(client.get_research_queue()),
            # Without this a ``queue_empty: shipyard`` condition passed
            # vacuously, which is worse than not offering the condition.
            "shipyard": len(client.get_ship_queue("fleet")),
        }
    except NewStarError as exc:
        facts.error = f"{facts.error}; queues: {exc}".lstrip("; ")
    try:
        facts.resources = dict(client.get_overview().resources.amounts)
    except NewStarError as exc:
        facts.error = f"{facts.error}; overview: {exc}".lstrip("; ")
    if facts.levels or facts.research_levels:
        try:
            facts.phase = detect_phase(
                [
                    type("Row", (), {"id": key, "level": value})
                    for key, value in facts.levels.items()
                ],
                [
                    type("Row", (), {"id": key, "level": value})
                    for key, value in facts.research_levels.items()
                ],
                directive,
            ).key
        except Exception as exc:  # noqa: BLE001 - a phase guess is not worth a crash
            facts.error = f"{facts.error}; phase: {exc}".lstrip("; ")
    return facts


def check(condition: Optional[Mapping[str, Any]], facts: Facts) -> Tuple[bool, str]:
    """Evaluate one condition.  Returns ``(holds, why_not)``."""

    if not condition:
        return True, ""
    if facts.error and not facts.levels and not facts.research_levels:
        return False, f"无法读取事实：{facts.error}"
    kind = condition.get("kind", "always")
    if kind == "always":
        return True, ""
    if kind == "queue_empty":
        queue = str(condition.get("queue", "building"))
        count = int(facts.queues.get(queue, 0))
        return count == 0, "" if count == 0 else f"{queue} 队列还有 {count} 项"
    if kind == "level_at_least":
        element = int(condition["element"])
        needed = int(condition["level"])
        have = facts.level(element)
        return have >= needed, "" if have >= needed else f"元素 {element} 当前 {have} 级，需要 {needed}"
    if kind == "resources_at_least":
        deficits = []
        for key, needed in dict(condition.get("resources") or {}).items():
            have = float(facts.resources.get(int(key), 0.0))
            if have < float(needed):
                deficits.append(f"{key}: {have:,.0f} < {float(needed):,.0f}")
        return not deficits, "；".join(deficits)
    if kind == "phase":
        wanted = str(condition.get("phase", ""))
        return facts.phase == wanted, "" if facts.phase == wanted else f"阶段是 {facts.phase}，不是 {wanted}"
    return False, f"未知条件类型：{kind}（支持：{', '.join(CONDITION_KINDS)}）"


def _kind_of(registry: ToolRegistry, tool: str) -> str:
    for spec in registry.specs():
        if spec.name == tool:
            return spec.kind
    return "unknown"


def run_steps(
    registry: ToolRegistry,
    steps: Iterable[Step],
    *,
    client: Any = None,
    directive: Any = None,
    max_steps: int = 8,
) -> RunReport:
    """Run steps in order, refreshing facts after every write."""

    plan = list(steps)
    if not plan:
        return RunReport(status="empty", reason="没有步骤")

    facts = load_facts(client, directive) if client is not None else None
    results: List[StepResult] = []

    for index, step in enumerate(plan[: max(1, int(max_steps))]):
        if step.when and facts is not None:
            holds, why_not = check(step.when, facts)
            if not holds:
                results.append(
                    StepResult(index=index, step=step, status="blocked", message=why_not)
                )
                return RunReport(
                    status="blocked",
                    steps=results,
                    stopped_at=index,
                    reason=f"第 {index + 1} 步前提不成立：{why_not}",
                )

        result = registry.call(step.tool, step.args)
        status = "dry_run" if result.status == "dry_run" else result.status
        results.append(
            StepResult(index=index, step=step, status=status, tool_result=result, message=result.message)
        )

        if result.status == "dry_run":
            continue
        # A rejection is data: stop and let the caller re-plan.  "skipped" is a
        # normal no-op (e.g. sign-in already claimed), not a failure.
        if not result.ok and result.status != "skipped":
            return RunReport(
                status="failed",
                steps=results,
                stopped_at=index,
                reason=f"第 {index + 1} 步未成功（{result.code or result.status}）：{result.message}",
            )

        if facts is not None and _kind_of(registry, step.tool) == "write":
            facts = load_facts(client, directive)

        if step.until and facts is not None:
            holds, why_not = check(step.until, facts)
            if not holds:
                results[-1] = StepResult(
                    index=index,
                    step=step,
                    status="unexpected",
                    tool_result=result,
                    message=f"预期未达成：{why_not}",
                )
                return RunReport(
                    status="unexpected",
                    steps=results,
                    stopped_at=index,
                    reason=f"第 {index + 1} 步执行了但世界没变成预期：{why_not}",
                )

    return RunReport(status="completed", steps=results)


__all__ = [
    "CONDITION_KINDS",
    "Facts",
    "RunReport",
    "Step",
    "StepResult",
    "check",
    "load_facts",
    "parse_step",
    "parse_steps",
    "run_steps",
]

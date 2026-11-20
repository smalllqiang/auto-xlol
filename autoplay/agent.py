"""The planning loop: brief -> model -> typed plan -> runner.

The model sees facts (a brief), receives the verb set as tools, and must end
with a typed plan.  Two ways to produce that plan are accepted:

* ``submit_plan`` -- one call with ``{goal, reason, steps}`` (preferred);
* calling write tools directly -- each call is *staged* into the same plan and
  executed after the model stops, so a read after a write does not see a lie.

Read tools are executed inline, because they have no side effects.  When the
provider fails or returns nothing usable, the loop falls back to the
deterministic planner's first candidate: the daemon never stops because a model
is down (ADR-031).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .brief import collect_brief, render_brief
from .directive import Directive
from .llm import ChatReply, Provider, ProviderError
from .prompt import build_system_prompt
from .runner import RunReport, Step, parse_steps, run_steps
from .tools import ToolRegistry, ToolSpec

#: A hard cap on model/tool round trips inside one decision.
DEFAULT_MAX_ROUNDS = 6

SUBMIT_PLAN = ToolSpec(
    name="submit_plan",
    description="提交本轮计划。steps 为空表示本轮等待。",
    parameters={
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "这次计划想达成什么"},
            "reason": {"type": "string", "description": "一两句话说明排序理由，不要复述数字"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string"},
                        "args": {"type": "object"},
                        "when": {"type": "object"},
                        "until": {"type": "object"},
                        "why": {"type": "string"},
                    },
                    "required": ["tool"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["steps"],
        "additionalProperties": False,
    },
    kind="control",
)


def _hash_text(text: str) -> str:
    """Stable fingerprint of a prompt (what the model read, not the path)."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    """Fingerprint a JSON payload independent of key/dict ordering."""

    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(canonical)


@dataclass
class AgentConfig:
    dry_run: bool = True
    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_steps: int = 8
    max_candidates: int = 12
    max_history: int = 8
    max_chars: int = 6000
    stop_file: str = "state/STOP"


@dataclass
class Decision:
    goal: str = ""
    reason: str = ""
    steps: List[Step] = field(default_factory=list)
    submitted: bool = False
    transcript: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    fallback: bool = False
    #: Replay metadata: what the model read, and every tool call it made.
    prompt_hash: str = ""
    tools_hash: str = ""
    brief_hash: str = ""
    max_chars: int = 0
    tool_events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        # ``tool_events`` / the hashes are published at the cycle level
        # (``AgentCycleResult``); the plan itself stays a plan.
        return {
            "goal": self.goal,
            "reason": self.reason,
            "steps": [step.as_dict() for step in self.steps],
            "submitted": bool(self.submitted),
            "fallback": bool(self.fallback),
            "error": self.error,
            "usage": dict(self.usage),
            "transcript": list(self.transcript),
        }


@dataclass
class AgentCycleResult:
    status: str  # "ok" | "stopped" | "failed"
    reason: str = ""
    phase: str = ""
    goal: str = ""
    brief: Dict[str, Any] = field(default_factory=dict)
    decision: Dict[str, Any] = field(default_factory=dict)
    report: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=dict)
    prompt_hash: str = ""
    tools_hash: str = ""
    brief_hash: str = ""
    max_chars: int = 0
    tool_events: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "phase": self.phase,
            "goal": self.goal,
            "brief": dict(self.brief),
            "decision": dict(self.decision),
            "report": dict(self.report),
            "usage": dict(self.usage),
            "prompt_hash": self.prompt_hash,
            "tools_hash": self.tools_hash,
            "brief_hash": self.brief_hash,
            "max_chars": self.max_chars,
            "tool_events": list(self.tool_events),
        }


def _merge_usage(total: Dict[str, int], usage: Mapping[str, Any]) -> None:
    for key, value in usage.items():
        if isinstance(value, int):
            total[str(key)] = total.get(str(key), 0) + value


def _assistant_message(reply: ChatReply) -> Dict[str, Any]:
    """Rebuild the assistant turn so the next request is valid.

    Some providers reject a conversation where the assistant turn that produced
    tool calls is missing from the history.
    """

    message = dict(reply.raw_message or {})
    message["role"] = "assistant"
    if not message.get("content") and reply.content:
        message["content"] = reply.content
    if reply.tool_calls and not message.get("tool_calls"):
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in reply.tool_calls
        ]
    return message


def _tool_message(call_id: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(payload, ensure_ascii=False),
    }


def _tool_event(
    round_index: int,
    call_id: str,
    name: str,
    kind: str,
    args: Optional[Mapping[str, Any]],
    result: Mapping[str, Any],
) -> Dict[str, Any]:
    """One journal-readable row per model tool call (result included)."""

    return {
        "round": int(round_index),
        "id": str(call_id),
        "tool": str(name),
        "kind": kind,
        "args": dict(args) if isinstance(args, Mapping) else None,
        "result": dict(result),
    }


def _carry_trace(target: Decision, source: Decision) -> Decision:
    """Keep what the model already saw when its plan is replaced by a fallback."""

    target.transcript = list(source.transcript)
    target.tool_events = list(source.tool_events)
    target.prompt_hash = source.prompt_hash
    target.tools_hash = source.tools_hash
    target.brief_hash = source.brief_hash
    target.max_chars = source.max_chars
    for key, value in source.usage.items():
        target.usage[key] = target.usage.get(key, 0) + value
    return target


def _spec(registry: ToolRegistry, name: str) -> Optional[ToolSpec]:
    for spec in registry.specs():
        if spec.name == name:
            return spec
    return None


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull one JSON object out of a reply, tolerating code fences."""

    body = (text or "").strip()
    if "```" in body:
        chunks = body.split("```")
        for chunk in chunks:
            cleaned = chunk.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            if cleaned.startswith("{"):
                body = cleaned
                break
    start = body.find("{")
    end = body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _plan_from_payload(payload: Mapping[str, Any]) -> Optional[Decision]:
    raw_steps = payload.get("steps")
    if raw_steps is None:
        return None
    steps = parse_steps(raw_steps if isinstance(raw_steps, list) else [])
    return Decision(
        goal=str(payload.get("goal") or ""),
        reason=str(payload.get("reason") or ""),
        steps=steps,
        submitted=True,
    )


def fallback_decision(brief: Mapping[str, Any], error: str = "") -> Decision:
    """The deterministic planner's first candidate, used when the model fails."""

    for item in brief.get("candidates") or []:
        kind = item.get("kind")
        tool = "upgrade_building" if kind == "building" else "research"
        return Decision(
            goal=str((brief.get("account") or {}).get("goal") or ""),
            reason="模型不可用，回退到确定性规划器的首条候选",
            steps=[Step(tool=tool, args={"element": int(item["element_id"])}, why="fallback")],
            error=error,
            fallback=True,
        )
    return Decision(
        goal=str((brief.get("account") or {}).get("goal") or ""),
        reason="模型不可用且没有候选，本轮等待",
        error=error,
        fallback=True,
    )


def decide(
    provider: Provider,
    brief: Mapping[str, Any],
    registry: ToolRegistry,
    *,
    directive: Any = None,
    system_prompt: str = "",
    brief_text: Optional[str] = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_chars: int = 6000,
) -> Decision:
    """Run the model/tool loop and return a typed plan.

    Every tool call and its result is recorded in ``Decision.tool_events``, and
    the three hashes fingerprint what the model read, so a journal entry can be
    replayed later (docs/usage.md §23).  A provider failure is returned as
    ``Decision.error`` instead of raised, so the trace recorded up to that point
    survives the caller's fallback to the deterministic planner.
    """

    system_content = system_prompt or build_system_prompt(registry, directive)
    user_content = (
        brief_text if brief_text is not None else render_brief(brief, max_chars=max_chars)
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]
    tools = registry.schema() + [SUBMIT_PLAN.as_openai()]

    decision = Decision(
        prompt_hash=_hash_text(system_content),
        tools_hash=_hash_json(tools),
        brief_hash=_hash_text(user_content),
        max_chars=int(max_chars),
    )
    staged: List[Step] = []

    for round_index in range(max(1, int(max_rounds))):
        try:
            reply = provider.complete(messages, tools)
        except ProviderError as exc:
            decision.error = str(exc)
            return decision
        except Exception as exc:  # noqa: BLE001 - provider I/O is not trusted
            decision.error = f"{type(exc).__name__}: {exc}"
            return decision

        _merge_usage(decision.usage, reply.usage)
        decision.transcript.append(
            {
                "role": "assistant",
                "round": round_index,
                "content": reply.content,
                "tool_calls": [
                    {"id": call.id, "name": call.name, "arguments": call.arguments}
                    for call in reply.tool_calls
                ],
            }
        )

        if not reply.tool_calls:
            parsed = _extract_json(reply.content)
            candidate = _plan_from_payload(parsed) if parsed else None
            if candidate is not None:
                decision.goal = candidate.goal
                decision.reason = candidate.reason
                decision.steps = candidate.steps
                decision.submitted = True
            break

        messages.append(_assistant_message(reply))
        for call in reply.tool_calls:
            try:
                payload = call.parsed_args()
            except ProviderError as exc:
                outcome = {"ok": False, "code": "BAD_ARGS", "message": str(exc)}
                decision.tool_events.append(
                    _tool_event(round_index, call.id, call.name, "unknown", None, outcome)
                )
                messages.append(_tool_message(call.id, outcome))
                continue

            if call.name == SUBMIT_PLAN.name:
                try:
                    parsed = _plan_from_payload(payload) or Decision()
                except ValueError as exc:
                    outcome = {"ok": False, "code": "BAD_PLAN", "message": str(exc)}
                    decision.tool_events.append(
                        _tool_event(round_index, call.id, call.name, "control", payload, outcome)
                    )
                    messages.append(_tool_message(call.id, outcome))
                    continue
                decision.goal = parsed.goal
                decision.reason = parsed.reason
                decision.steps = parsed.steps
                decision.submitted = True
                outcome = {"ok": True, "status": "accepted", "steps": len(parsed.steps)}
                decision.tool_events.append(
                    _tool_event(round_index, call.id, call.name, "control", payload, outcome)
                )
                messages.append(_tool_message(call.id, outcome))
                continue

            spec = _spec(registry, call.name)
            if spec is None:
                outcome = {
                    "ok": False,
                    "code": "UNKNOWN_TOOL",
                    "message": f"没有这个工具：{call.name}",
                }
                decision.tool_events.append(
                    _tool_event(round_index, call.id, call.name, "unknown", payload, outcome)
                )
                messages.append(_tool_message(call.id, outcome))
                continue

            if spec.kind == "write":
                # Staged, not executed: the runner owns execution order and the
                # precondition checks (ADR-031).
                staged.append(
                    Step(
                        tool=call.name,
                        args=payload,
                        why="模型直接调用的写工具，已登记进本轮计划",
                    )
                )
                outcome = {
                    "ok": True,
                    "status": "staged",
                    "message": "已登记进本轮计划，规划结束后按顺序执行",
                }
                decision.tool_events.append(
                    _tool_event(round_index, call.id, call.name, "write", payload, outcome)
                )
                messages.append(_tool_message(call.id, outcome))
                continue

            result = registry.call(call.name, payload)
            outcome = result.as_dict()
            decision.tool_events.append(
                _tool_event(round_index, call.id, call.name, spec.kind, payload, outcome)
            )
            messages.append(_tool_message(call.id, outcome))

        if decision.submitted:
            break

    if not decision.submitted and staged:
        decision.steps = staged
        decision.submitted = True
        decision.reason = decision.reason or "由模型直接调用的写工具组成"
    return decision


def run_agent_cycle(
    client: Any,
    provider: Provider,
    *,
    directive: Optional[Directive] = None,
    config: Optional[AgentConfig] = None,
    history: Optional[List[Mapping[str, Any]]] = None,
    previous: Optional[Mapping[str, Any]] = None,
    system_prompt: str = "",
    stop_exists: Optional[bool] = None,
) -> AgentCycleResult:
    """One observe -> decide -> run cycle.  Never raises for a bad model reply."""

    cfg = config or AgentConfig()
    stopped = (
        Path(cfg.stop_file).exists() if stop_exists is None else bool(stop_exists)
    )
    if stopped:
        return AgentCycleResult(status="stopped", reason=f"{cfg.stop_file} 存在，已停止")

    brief = collect_brief(
        client,
        directive,
        history=history,
        previous=previous,
        max_candidates=cfg.max_candidates,
        max_history=cfg.max_history,
    )
    account = brief.get("account") or {}
    registry = ToolRegistry(client, dry_run=cfg.dry_run)
    # Render once: the hash recorded in the journal must fingerprint the exact
    # user message the model read.
    brief_text = render_brief(brief, max_chars=cfg.max_chars)

    error = ""
    decision = Decision()
    try:
        decision = decide(
            provider,
            brief,
            registry,
            directive=directive,
            system_prompt=system_prompt,
            brief_text=brief_text,
            max_rounds=cfg.max_rounds,
            max_chars=cfg.max_chars,
        )
        if decision.error:
            # The provider failed mid-loop: fall back for the plan, but keep the
            # trace so the journal still shows what the model saw before that.
            error = decision.error
            traced = decision
            decision = _carry_trace(fallback_decision(brief, error), traced)
        # ``submitted`` with zero steps is a deliberate "wait this round"; only a
        # reply with no plan at all falls back to the planner.
        elif not decision.submitted:
            traced = decision
            decision = _carry_trace(
                fallback_decision(brief, "模型没有给出可执行步骤"), traced
            )
    except Exception as exc:  # noqa: BLE001 - a bad reply must not kill the loop
        error = f"{type(exc).__name__}: {exc}"
        decision = _carry_trace(fallback_decision(brief, error), decision)

    report: RunReport = run_steps(
        registry,
        decision.steps,
        client=client,
        directive=directive,
        max_steps=cfg.max_steps,
    )

    if error:
        status = "degraded"
    elif report.status in {"failed", "unexpected"}:
        status = "failed"
    else:
        status = "ok"
    return AgentCycleResult(
        status=status,
        reason=report.reason or decision.reason or error,
        phase=str(account.get("phase") or ""),
        goal=str(account.get("goal") or ""),
        brief=brief,
        decision=decision.as_dict(),
        report=report.as_dict(),
        usage=dict(decision.usage),
        prompt_hash=decision.prompt_hash,
        tools_hash=decision.tools_hash,
        brief_hash=decision.brief_hash,
        max_chars=decision.max_chars,
        tool_events=list(decision.tool_events),
    )


__all__ = [
    "AgentConfig",
    "AgentCycleResult",
    "Decision",
    "SUBMIT_PLAN",
    "decide",
    "fallback_decision",
    "run_agent_cycle",
]

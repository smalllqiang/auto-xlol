"""Turn facts into a decision brief for the planning model.

The brief is everything the model gets to know about the account.  It is built
from an already-collected snapshot plus the deterministic plan, so the numbers
in it are computed by code, never by the model (ADR-031).  Two consequences:

* every number here has already been derived once (payback, deficit, queue
  ETA), so the model only has to *choose between* candidates;
* the brief carries ``unknowns``: a page that failed to parse must reach the
  model as "I cannot see this", not as a silent zero.

:func:`build_brief` is pure.  :func:`collect_brief` is the convenience wrapper
that reads the game first; it is the only function here that touches a client.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from newstar_sdk.constants import RESOURCE_NAMES_ZH

from .planner import Action, Plan

SCHEMA_VERSION = 1

#: Resources that drive spending decisions.
RESOURCE_IDS = (901, 902, 903)
#: 幽能 / energy: produced per hour, not stored (see docs/doctrine.md).
ENERGY_ID = 911

DEFAULT_MAX_CANDIDATES = 12
DEFAULT_MAX_HISTORY = 8
DEFAULT_MAX_CHARS = 6000


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _label(resource_id: int) -> str:
    return RESOURCE_NAMES_ZH.get(int(resource_id), str(resource_id))


def _amounts(snapshot: Mapping[str, Any]) -> Dict[int, float]:
    raw = (snapshot.get("resources") or {}).get("amounts") or {}
    return {int(key): _num(value) for key, value in raw.items()}


def _capacities(snapshot: Mapping[str, Any]) -> Dict[int, float]:
    raw = (snapshot.get("resources") or {}).get("capacities") or {}
    return {int(key): _num(value) for key, value in raw.items()}


def _production(snapshot: Mapping[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Per-resource production rows, keyed by resource id.

    ``total`` is the number the game itself reports: net for energy (911), the
    resource's hourly output for 901/902/903.
    """

    rows = (snapshot.get("production") or {}).get("resources") or {}
    return {int(key): dict(value) for key, value in rows.items() if isinstance(value, dict)}


def _queue(snapshot: Mapping[str, Any], kind: str) -> List[Dict[str, Any]]:
    queues = snapshot.get("queues") or {}
    items = queues.get(kind) or []
    return [dict(item) for item in items if isinstance(item, dict)]


def _queue_eta(entries: Sequence[Mapping[str, Any]]) -> Optional[float]:
    seconds = [
        _num(entry.get("remaining_seconds"), -1.0)
        for entry in entries
        if entry.get("remaining_seconds") is not None
    ]
    seconds = [value for value in seconds if value >= 0]
    return max(seconds) if seconds else None


def _resource_rows(snapshot: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    amounts = _amounts(snapshot)
    capacities = _capacities(snapshot)
    production = _production(snapshot)
    rows: Dict[str, Dict[str, Any]] = {}
    for resource_id in RESOURCE_IDS:
        amount = amounts.get(resource_id, 0.0)
        capacity = capacities.get(resource_id, 0.0)
        hourly = _num((production.get(resource_id) or {}).get("total"))
        rows[str(resource_id)] = {
            "name": _label(resource_id),
            "amount": amount,
            "capacity": capacity,
            "usage": round(amount / capacity, 3) if capacity > 0 else None,
            "hourly": hourly,
            "overflowed": capacity > 0 and amount >= capacity,
        }
    return rows


def _energy(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    row = _production(snapshot).get(ENERGY_ID) or {}
    if "total" in row:
        net = _num(row.get("total"))
        source = "production.total"
    else:
        # The overview's energy field is gross/nominal; only fall back to it
        # when the production page did not parse, and say so.
        net = _num((snapshot.get("energy") or {}).get("production"))
        source = "overview.production"
    return {"net": net, "short": net < 0, "source": source}


def _candidate(action: Action) -> Dict[str, Any]:
    # ROI actions only carry a single-level cost; fall back so a brief never
    # reports "0 投入" for a real candidate.
    total_cost = action.total_cost or action.cost
    total_equivalent = float(action.total_cost_equivalent) or float(action.cost_equivalent)
    return {
        "category": action.category,
        "kind": action.kind,
        "element_id": int(action.element_id),
        "name": action.name,
        "current_level": int(action.current_level),
        "target_level": int(action.target_level),
        "required_level": action.required_level,
        "steps": int(action.steps),
        "depth": int(action.depth),
        "cost": {str(key): value for key, value in action.cost.items()},
        "cost_equivalent": round(float(action.cost_equivalent), 1),
        "total_cost": {str(key): value for key, value in total_cost.items()},
        "total_cost_equivalent": round(total_equivalent, 1),
        "payback_hours": (
            round(float(action.payback_hours), 2)
            if action.payback_hours is not None
            else None
        ),
        "priority": int(action.priority),
        "missing": {str(key): value for key, value in action.missing_resources.items()},
        "reason": action.reason,
    }


def _unknowns(snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Sections that failed to parse.  The model must not read them as zero."""

    return [
        {
            "source": "snapshot",
            "section": error.get("section"),
            "detail": error.get("error"),
        }
        for error in snapshot.get("errors") or []
    ]


def _delta(
    resources: Mapping[str, Dict[str, Any]],
    queues: Mapping[str, Mapping[str, Any]],
    energy: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """What changed since the previous decision, for the model's memory."""

    if not previous:
        return {"first_decision": True}
    change: Dict[str, Any] = {}

    old_resources = previous.get("resources") or {}
    resource_delta: Dict[str, float] = {}
    for key, row in resources.items():
        before = _num((old_resources.get(key) or {}).get("amount"))
        after = _num(row.get("amount"))
        if before != after:
            resource_delta[key] = round(after - before, 2)
    if resource_delta:
        change["resources"] = resource_delta

    old_queues = previous.get("queues") or {}
    queue_delta: Dict[str, Dict[str, int]] = {}
    for kind, entry in queues.items():
        before = int((old_queues.get(kind) or {}).get("count", 0))
        after = int(entry.get("count", 0))
        if before != after:
            queue_delta[kind] = {"before": before, "after": after}
    if queue_delta:
        change["queues"] = queue_delta

    old_energy = _num((previous.get("energy") or {}).get("net"))
    new_energy = _num(energy.get("net"))
    if old_energy != new_energy:
        change["energy_net"] = {"before": old_energy, "after": new_energy}

    if not change:
        change["unchanged"] = True
    return change


def build_brief(
    snapshot: Mapping[str, Any],
    plan: Plan,
    *,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    previous: Optional[Mapping[str, Any]] = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> Dict[str, Any]:
    """Project a snapshot + plan into a decision brief (pure, JSON-able)."""

    resources = _resource_rows(snapshot)
    energy = _energy(snapshot)
    building = _queue(snapshot, "building")
    research = _queue(snapshot, "research")
    shipyard = _queue(snapshot, "shipyard")
    queues = {
        "building": {"count": len(building), "eta_seconds": _queue_eta(building)},
        "research": {"count": len(research), "eta_seconds": _queue_eta(research)},
        "shipyard": {"count": len(shipyard), "eta_seconds": _queue_eta(shipyard)},
    }

    planet = snapshot.get("planet") or {}
    actions = list(plan.actions)
    candidates = [_candidate(action) for action in actions[: max(1, int(max_candidates))]]
    shown_history = [dict(item) for item in (history or [])][-max(1, int(max_history)) :]

    return {
        "schema": SCHEMA_VERSION,
        "captured_at": snapshot.get("captured_at"),
        "server_time": snapshot.get("server_time"),
        "account": {
            "username": (snapshot.get("account") or {}).get("username"),
            "planet": planet.get("name"),
            "coordinates": planet.get("coordinates"),
            "phase": plan.phase,
            "phase_name": plan.phase_name,
            "goal": plan.goal_status or plan.goal,
        },
        "resources": resources,
        "energy": energy,
        "queues": queues,
        "candidates": candidates,
        "unknowns": _unknowns(snapshot),
        "notes": [str(note) for note in plan.notes or []],
        "history": shown_history,
        "delta": _delta(resources, queues, energy, previous),
        "limits": {
            "candidates_total": len(actions),
            "candidates_shown": len(candidates),
            "history_total": len(history or []),
            "history_shown": len(shown_history),
        },
    }


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:,.0f}"


def _head_lines(brief: Mapping[str, Any]) -> List[str]:
    account = brief.get("account") or {}
    planet = account.get("planet") or "-"
    coordinates = account.get("coordinates") or ""
    return [
        "# 决策简报",
        f"- 时间：{brief.get('server_time') or brief.get('captured_at') or '-'}",
        f"- 星球：{planet} {coordinates}".rstrip(),
        f"- 阶段：{account.get('phase_name') or '-'}（{account.get('phase') or '-'}）",
        f"- 目标：{account.get('goal') or '-'}",
    ]


def _resource_lines(brief: Mapping[str, Any]) -> List[str]:
    lines = ["## 资源"]
    for row in (brief.get("resources") or {}).values():
        usage = row.get("usage")
        usage_text = f" 占用 {usage:.0%}" if usage is not None else ""
        overflow = "，已爆仓" if row.get("overflowed") else ""
        lines.append(
            f"- {row['name']}: {_fmt(row.get('amount'))} / {_fmt(row.get('capacity'))}"
            f"{usage_text}，{_fmt(row.get('hourly'))}/h{overflow}"
        )
    return lines


def _queue_lines(brief: Mapping[str, Any]) -> List[str]:
    lines = ["## 队列"]
    queues = brief.get("queues") or {}
    for kind, label in (("building", "建造"), ("research", "研究"), ("shipyard", "船坞")):
        entry = queues.get(kind) or {}
        eta = entry.get("eta_seconds")
        eta_text = f"，剩余约 {_fmt(eta)} 秒" if eta is not None else ""
        lines.append(f"- {label}: {int(entry.get('count', 0))} 项{eta_text}")
    return lines


def _delta_lines(brief: Mapping[str, Any]) -> List[str]:
    delta = brief.get("delta") or {}
    if delta.get("first_decision") or delta.get("unchanged"):
        return []
    lines = ["## 相对上次决策"]
    for key, value in (delta.get("resources") or {}).items():
        lines.append(f"- {_label(int(key))}: {value:+,.0f}")
    for key, value in (delta.get("queues") or {}).items():
        lines.append(f"- {key} 队列: {value['before']} → {value['after']}")
    if "energy_net" in delta:
        lines.append(
            f"- 电力净产: {delta['energy_net']['before']:+,.0f} → "
            f"{delta['energy_net']['after']:+,.0f}"
        )
    return lines


def _unknown_lines(brief: Mapping[str, Any]) -> List[str]:
    unknowns = brief.get("unknowns") or []
    if not unknowns:
        return []
    lines = ["## 读不到的页面（不要当成 0 使用）"]
    for item in unknowns:
        section = f"[{item['section']}] " if item.get("section") else ""
        lines.append(f"- {section}{item.get('detail')}")
    return lines


def _note_lines(brief: Mapping[str, Any]) -> List[str]:
    notes = brief.get("notes") or []
    if not notes:
        return []
    return ["## 提示（来自确定性规划器）"] + [f"- {note}" for note in notes]


def _candidate_block(item: Mapping[str, Any], index: int) -> List[str]:
    payback = (
        f"回本 {item['payback_hours']:.2f}h"
        if item.get("payback_hours") is not None
        else "回本不可用"
    )
    required = item.get("required_level")
    if required:
        level_text = (
            f"{item['current_level']}->{item['target_level']}"
            f"（需到 {required} 级，还差 {item['steps']} 级"
        )
    else:
        level_text = f"{item['current_level']}->{item['target_level']}（"
    return [
        f"{index}. [{item['category']}] {item['name']} {level_text}；"
        f"单级等价 {_fmt(item.get('cost_equivalent'))}，"
        f"累计 {_fmt(item.get('total_cost_equivalent'))}，{payback}）",
        f"   理由：{item.get('reason') or '-'}",
    ]


def _history_lines(brief: Mapping[str, Any]) -> List[str]:
    history = brief.get("history") or []
    if not history:
        return []
    lines = ["## 最近决策"]
    for item in history:
        cycle = item.get("cycle")
        choice = item.get("chose") or item.get("action") or "-"
        result = item.get("result") or item.get("status") or ""
        suffix = f" → {result}" if result else ""
        lines.append(f"- #{cycle} {choice}{suffix}")
    return lines


def render_brief(brief: Mapping[str, Any], *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Render a brief as markdown, dropping the least important parts first."""

    sections = [
        _head_lines(brief),
        _resource_lines(brief),
        ["## 电力"]
        + [
            f"- 净产 {_fmt((brief.get('energy') or {}).get('net'))}/h"
            + ("（缺电，采矿会被减产）" if (brief.get("energy") or {}).get("short") else "")
        ],
        _queue_lines(brief),
        _delta_lines(brief),
        _unknown_lines(brief),
        _note_lines(brief),
    ]
    text = "\n\n".join("\n".join(lines) for lines in sections if lines)

    candidates = brief.get("candidates") or []
    total = int((brief.get("limits") or {}).get("candidates_total", len(candidates)))
    shown = 0
    if candidates:
        lines = [f"## 候选（共 {total} 条，按计划顺序）"]
        for index, item in enumerate(candidates, start=1):
            block = _candidate_block(item, index)
            trial = text + "\n\n" + "\n".join(lines + block)
            # Always show at least one candidate; after that, stop at the budget.
            if shown and len(trial) > max_chars:
                break
            lines.extend(block)
            shown = index
        text += "\n\n" + "\n".join(lines)

    history = _history_lines(brief)
    if history:
        trial = "\n\n" + "\n".join(history)
        if len(text) + len(trial) <= max_chars:
            text += trial
        else:
            text += "\n\n（最近决策已省略：超出字数预算）"

    hidden = total - shown
    if hidden > 0:
        text += f"\n\n（另有 {hidden} 条候选未展示：超出字数预算）"
    return text.strip() + "\n"


def collect_brief(
    client: Any,
    directive: Any = None,
    *,
    history: Optional[Sequence[Mapping[str, Any]]] = None,
    previous: Optional[Mapping[str, Any]] = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_history: int = DEFAULT_MAX_HISTORY,
) -> Dict[str, Any]:
    """Read the game once and return a brief (this is the only I/O path here)."""

    from newstar_sdk.snapshot import build_snapshot

    from .planner import build_plan

    snapshot = build_snapshot(client)
    plan = build_plan(client, directive)
    return build_brief(
        snapshot,
        plan,
        history=history,
        previous=previous,
        max_candidates=max_candidates,
        max_history=max_history,
    )


__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_HISTORY",
    "SCHEMA_VERSION",
    "build_brief",
    "collect_brief",
    "render_brief",
]

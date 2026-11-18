"""Power-aware production ratios: lower the biggest consumer when short.

The game lets every producer run at 0-100% in 10% steps (``prod[]``).  When the
net power rate goes negative the mines get a proportional penalty, so the
deterministic response is: lower the largest consumer by one step, then check
again next cycle.  This module plans that change; the write itself goes through
``NewStarClient.set_production_ratios`` behind an allow-list.

Nothing here runs automatically: the daemon only calls it when the operator
turns the feature on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from newstar_sdk.constants import BUILDINGS
from newstar_sdk.exceptions import ActionRejected

#: 幽能 / energy is the resource whose production column carries consumption.
POWER_RESOURCE = 911

#: One step of the game's 10%-granular scale (ratios are percentages).
STEP = 10

ELEMENT_NAMES: Dict[int, str] = {
    element_id: element.name_zh for element_id, element in BUILDINGS.items()
}


@dataclass
class RatioPlan:
    """What to change, why, and what it should do to the power balance."""

    changes: Dict[int, int] = field(default_factory=dict)
    before: Dict[int, int] = field(default_factory=dict)
    net_power: Optional[float] = None
    projected_net: Optional[float] = None
    reason: str = ""
    consumers: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "changes": {str(key): value for key, value in self.changes.items()},
            "before": {str(key): value for key, value in self.before.items()},
            "net_power": self.net_power,
            "projected_net": (
                round(self.projected_net, 1)
                if self.projected_net is not None
                else None
            ),
            "reason": self.reason,
            "consumers": list(self.consumers),
        }


@dataclass
class RatioResult:
    status: str  # "preview" | "applied" | "skipped"
    changes: Dict[int, int] = field(default_factory=dict)
    before: Dict[int, int] = field(default_factory=dict)
    after: Dict[int, int] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "changes": {str(key): value for key, value in self.changes.items()},
            "before": {str(key): value for key, value in self.before.items()},
            "after": {str(key): value for key, value in self.after.items()},
            "reason": self.reason,
        }


def _percent(value: int) -> str:
    return f"{int(value)}%"


def plan_power_response(
    report: Any,
    *,
    step: int = STEP,
    minimum: int = 0,
) -> RatioPlan:
    """Plan one downward step on the largest consumer when power is short."""

    item = (getattr(report, "resources", {}) or {}).get(POWER_RESOURCE)
    if item is None:
        return RatioPlan(reason="页面没有幽能数据，无法判断电力")
    net = float(getattr(item, "total", 0.0) or 0.0)
    ratios = {int(key): int(value) for key, value in (report.ratios or {}).items()}

    consumers = [
        {
            "element_id": int(element),
            "name": ELEMENT_NAMES.get(int(element), str(element)),
            "consumption": -float(value),
            "ratio": ratios.get(int(element)),
        }
        for element, value in (getattr(item, "buildings", {}) or {}).items()
        if float(value) < 0
    ]
    consumers.sort(key=lambda entry: entry["consumption"], reverse=True)

    if net >= 0:
        return RatioPlan(
            net_power=net,
            reason=f"电力净产 {net:+,.0f}/h，不需要降耗",
            consumers=consumers,
        )

    for consumer in consumers:
        element_id = consumer["element_id"]
        current = consumer["ratio"]
        if current is None or current <= minimum:
            continue
        new = max(int(minimum), int(current) - max(10, int(step)))
        relief = consumer["consumption"] * (int(current) - new) / 100.0
        return RatioPlan(
            changes={element_id: new},
            before={element_id: int(current)},
            net_power=net,
            projected_net=net + relief,
            reason=(
                f"电力净产 {net:+,.0f}/h，把{consumer['name']} 从 "
                f"{_percent(current)} 降到 {_percent(new)}（约补 {relief:,.0f}/h）"
            ),
            consumers=consumers,
        )

    return RatioPlan(
        net_power=net,
        reason="电力不足，但没有可继续下调的生产比例（都已是 0%）",
        consumers=consumers,
    )


def apply_ratio_plan(
    client: Any,
    plan: RatioPlan,
    *,
    dry_run: bool = True,
    allowed: Optional[Iterable[int]] = None,
) -> RatioResult:
    """Apply a plan; preview by default, allow-list required for the POST."""

    if not plan.changes:
        return RatioResult(status="skipped", reason=plan.reason)
    if dry_run:
        return RatioResult(
            status="preview",
            changes=dict(plan.changes),
            before=dict(plan.before),
            reason=plan.reason,
        )

    allowed_ids: Optional[Set[int]] = (
        {int(item) for item in allowed} if allowed is not None else None
    )
    if allowed_ids is None:
        raise ActionRejected("生产比例写入需要显式 allow-list（元素 id）", before_post=True)
    outside = sorted(set(plan.changes) - allowed_ids)
    if outside:
        raise ActionRejected(
            "这些元素不在 allow-list 里：" + ", ".join(str(item) for item in outside),
            before_post=True,
        )

    report = client.set_production_ratios(plan.changes)
    after = {
        element_id: int(report.ratios.get(element_id, -1))
        for element_id in plan.changes
    }
    return RatioResult(
        status="applied",
        changes=dict(plan.changes),
        before=dict(plan.before),
        after=after,
        reason=plan.reason,
    )

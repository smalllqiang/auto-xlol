"""Dispatch one expedition group behind an explicit safety gate.

Preview is the default: it runs the game's first two fleet steps and reports
fuel/missions without creating a fleet.  A real dispatch requires
``dry_run=False`` and an explicit allow-list of ship element ids; one call sends
at most one group.  Recalling, splitting across slots and retrying are left to
the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Dict, Iterable, Optional, Set

from newstar_sdk.constants import Mission
from newstar_sdk.exceptions import NewStarError

from .expedition import ExpeditionGroup, ExpeditionReport


def _export(value: Any) -> Any:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    return value


@dataclass
class ExpeditionDispatchResult:
    status: str  # "preview" | "dispatched" | "skipped" | "error"
    group: Optional[ExpeditionGroup] = None
    target: Optional[str] = None
    reason: str = ""
    preview: Any = None
    dispatch: Any = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "target": self.target,
            "reason": self.reason,
            "group": self.group.as_dict() if self.group is not None else None,
        }
        if self.preview is not None:
            payload["preview"] = _export(self.preview)
        if self.dispatch is not None:
            payload["dispatch"] = _export(self.dispatch)
        return payload


def _select_group(
    report: ExpeditionReport,
    group_index: int,
) -> Optional[ExpeditionGroup]:
    if group_index < 1 or group_index > len(report.fleet_groups):
        return None
    return report.fleet_groups[group_index - 1]


def dispatch_expedition(
    client: Any,
    report: ExpeditionReport,
    *,
    group_index: int = 1,
    target: Optional[str] = None,
    speed: int = 10,
    stay_time: int = 1,
    dry_run: bool = True,
    allowed_elements: Optional[Iterable[int]] = None,
) -> ExpeditionDispatchResult:
    """Preview or dispatch one expedition group from an expedition report."""

    target = target or report.target
    if not target:
        return ExpeditionDispatchResult(
            status="skipped",
            reason="没有确定目标坐标",
        )

    group = _select_group(report, int(group_index))
    if group is None:
        return ExpeditionDispatchResult(
            status="skipped",
            target=target,
            reason=f"没有第 {group_index} 支编队（当前 {len(report.fleet_groups)} 支）",
        )
    if not group.ships:
        return ExpeditionDispatchResult(
            status="skipped",
            group=group,
            target=target,
            reason="编队没有舰船",
        )

    allowed: Optional[Set[int]] = (
        set(int(item) for item in allowed_elements)
        if allowed_elements is not None
        else None
    )
    if not dry_run and allowed is None:
        return ExpeditionDispatchResult(
            status="skipped",
            group=group,
            target=target,
            reason="执行模式必须提供舰船允许列表",
        )
    if allowed is not None:
        disallowed = sorted(set(group.ships) - allowed)
        if disallowed:
            return ExpeditionDispatchResult(
                status="skipped",
                group=group,
                target=target,
                reason=f"编队含未允许舰船：{disallowed}",
            )

    if dry_run:
        try:
            preview = client.preview_fleet(
                target,
                group.ships,
                mission=Mission.EXPEDITION,
                speed=speed,
            )
        except NewStarError as exc:
            return ExpeditionDispatchResult(
                status="error",
                group=group,
                target=target,
                reason=f"{type(exc).__name__}: {exc}",
            )
        return ExpeditionDispatchResult(
            status="preview",
            group=group,
            target=target,
            reason=(
                f"预览：探险队 {group.index} 前往 {target}，"
                f"燃料 {preview.consumption if preview.consumption is not None else '-'}"
            ),
            preview=preview,
        )

    try:
        dispatch = client.send_fleet(
            target,
            group.ships,
            mission=Mission.EXPEDITION,
            speed=speed,
            stay_time=stay_time,
        )
    except NewStarError as exc:
        return ExpeditionDispatchResult(
            status="error",
            group=group,
            target=target,
            reason=f"{type(exc).__name__}: {exc}",
        )
    return ExpeditionDispatchResult(
        status="dispatched",
        group=group,
        target=target,
        reason=(
            f"已派出探险队 {group.index} 前往 {target}，"
            f"燃料 {dispatch.consumption if dispatch.consumption is not None else '-'}"
        ),
        dispatch=dispatch,
    )

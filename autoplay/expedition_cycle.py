"""Run one bounded multi-slot expedition cycle.

Each iteration re-reads the report, takes the first group for the currently free
slots, and dispatches it.  Re-reading matters: after a send the free-slot count
and available ships change.  A single call is capped by ``max_dispatch`` so a
stale report cannot turn into a burst of fleets.  With ``auto_build``, a cycle
may first build one recommended batch when it has a free slot but no group.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from newstar_sdk.exceptions import NewStarError

from .expedition import ExpeditionReport, build_expedition_report
from .expedition_executor import ExpeditionDispatchResult, dispatch_expedition
from .ship_build import ShipBuildResult, build_recommended_ships

ReportLoader = Callable[[Any], ExpeditionReport]


@dataclass
class ExpeditionCycleResult:
    status: str  # "dry_run" | "dispatched" | "partial" | "skipped" | "error"
    dispatches: List[ExpeditionDispatchResult] = field(default_factory=list)
    reason: str = ""
    build: Optional[ShipBuildResult] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "build": self.build.as_dict() if self.build is not None else None,
            "dispatches": [item.as_dict() for item in self.dispatches],
        }


def run_expedition_cycle(
    client: Any,
    *,
    dry_run: bool = True,
    allowed_elements: Optional[Iterable[int]] = None,
    max_dispatch: int = 1,
    speed: int = 10,
    stay_time: int = 1,
    target: Optional[str] = None,
    auto_build: bool = False,
    build_count: int = 1,
    build_timeout: float = 300.0,
    report_loader: ReportLoader = build_expedition_report,
) -> ExpeditionCycleResult:
    """Preview or dispatch up to ``max_dispatch`` expedition groups."""

    max_dispatch = int(max_dispatch)
    if max_dispatch < 1:
        return ExpeditionCycleResult(status="skipped", reason="max_dispatch 必须至少为 1")
    if not dry_run and allowed_elements is None:
        return ExpeditionCycleResult(
            status="skipped",
            reason="执行模式必须提供舰船允许列表",
        )

    if dry_run:
        report = report_loader(client)
        result = dispatch_expedition(
            client,
            report,
            group_index=1,
            target=target,
            speed=speed,
            stay_time=stay_time,
            dry_run=True,
            allowed_elements=allowed_elements,
        )
        status = "dry_run" if result.status == "preview" else result.status
        return ExpeditionCycleResult(
            status=status,
            dispatches=[result],
            reason=result.reason,
        )

    build_result: Optional[ShipBuildResult] = None
    if auto_build:
        report = report_loader(client)
        if report.free_slots > 0 and not report.fleet_groups:
            build_result = build_recommended_ships(
                client,
                report,
                count=build_count,
                dry_run=False,
                allowed_elements=allowed_elements,
            )
            if build_result.status != "built":
                return ExpeditionCycleResult(
                    status=(
                        "error" if build_result.status == "error" else "skipped"
                    ),
                    reason=build_result.reason,
                    build=build_result,
                )
            wait = getattr(client, "wait_for_ship_queue", None)
            if wait is not None:
                try:
                    wait(timeout=build_timeout, poll_interval=2)
                except (NewStarError, TimeoutError) as exc:
                    return ExpeditionCycleResult(
                        status="error",
                        reason=f"wait_for_ship_queue: {exc}",
                        build=build_result,
                    )

    dispatched: List[ExpeditionDispatchResult] = []
    reason = ""
    for _ in range(max_dispatch):
        report = report_loader(client)
        if report.free_slots <= 0:
            reason = "没有空闲探险航道"
            break
        if not report.fleet_groups:
            reason = "没有可派遣编队"
            break
        result = dispatch_expedition(
            client,
            report,
            group_index=1,
            target=target,
            speed=speed,
            stay_time=stay_time,
            dry_run=False,
            allowed_elements=allowed_elements,
        )
        dispatched.append(result)
        reason = result.reason
        if result.status != "dispatched":
            break

    if not dispatched:
        return ExpeditionCycleResult(
            status="skipped",
            reason=reason or "没有可执行动作",
            build=build_result,
        )
    if len(dispatched) == 1 and dispatched[0].status == "dispatched":
        return ExpeditionCycleResult(
            status="dispatched",
            dispatches=dispatched,
            reason=dispatched[0].reason,
            build=build_result,
        )
    if all(item.status == "dispatched" for item in dispatched):
        return ExpeditionCycleResult(
            status="dispatched",
            dispatches=dispatched,
            reason=f"已派出 {len(dispatched)} 队（本轮上限 {max_dispatch}）：{reason}",
            build=build_result,
        )
    return ExpeditionCycleResult(
        status="partial",
        dispatches=dispatched,
        reason=reason,
        build=build_result,
    )

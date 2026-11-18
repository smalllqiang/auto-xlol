"""Recall fleets behind an explicit allow-list (P2-2 write path).

Same rules as the rest of the executors: preview by default, one action per
call, explicit allow-list for the real POST, and success judged by page
evidence (the fleet disappears or is flagged as returning) instead of HTTP 200.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

from newstar_sdk.exceptions import ActionRejected
from newstar_sdk.models import FleetInfo


@dataclass
class RecallResult:
    status: str  # "preview" | "recalled" | "skipped"
    fleet_id: Optional[int] = None
    mission: str = ""
    target: str = ""
    reason: str = ""
    fleets_before: int = 0
    fleets_after: Optional[int] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "fleet_id": self.fleet_id,
            "mission": self.mission,
            "target": self.target,
            "reason": self.reason,
            "fleets_before": self.fleets_before,
            "fleets_after": self.fleets_after,
            "evidence": dict(self.evidence),
        }


def recall_candidates(fleets: Iterable[FleetInfo]) -> List[FleetInfo]:
    """Fleets that can still be turned around, in table order."""

    return [
        fleet
        for fleet in fleets
        if fleet.id is not None and fleet.can_recall and not fleet.returning
    ]


def _describe(fleet: FleetInfo) -> str:
    target = str(fleet.target_coordinates) if fleet.target_coordinates is not None else "-"
    return f"{fleet.mission or '?'} → {target}"


def _select(
    candidates: List[FleetInfo], fleet_ids: Optional[Iterable[int]]
) -> Optional[FleetInfo]:
    if not candidates:
        return None
    if fleet_ids is None:
        return candidates[0]
    wanted = {int(item) for item in fleet_ids}
    return next((fleet for fleet in candidates if int(fleet.id) in wanted), None)


def _verify(after: List[FleetInfo], fleet_id: int) -> Optional[Dict[str, Any]]:
    """Return evidence when the recall is visible, else ``None``."""

    for fleet in after:
        if fleet.id != fleet_id:
            continue
        if fleet.returning:
            return {"fleet_id": fleet_id, "returning": True, "mission": fleet.mission}
        return None
    return {"fleet_id": fleet_id, "gone": True}


def recall_fleets(
    client: Any,
    *,
    fleet_ids: Optional[Iterable[int]] = None,
    dry_run: bool = True,
    allowed: Optional[Iterable[int]] = None,
    fleets: Optional[List[FleetInfo]] = None,
) -> RecallResult:
    """Recall one fleet (the first candidate, or ``fleet_ids``), verifying it."""

    before = list(fleets) if fleets is not None else list(client.get_fleets())
    candidates = recall_candidates(before)
    target = _select(candidates, fleet_ids)
    if target is None:
        return RecallResult(
            status="skipped",
            fleets_before=len(before),
            reason="没有可召回的舰队（都在返航或没有召回入口）",
        )

    if dry_run:
        return RecallResult(
            status="preview",
            fleet_id=int(target.id),
            mission=target.mission or "",
            target=_describe(target),
            reason=f"可召回 {len(candidates)} 支舰队，本步只预览第一支",
            fleets_before=len(before),
        )

    allowed_ids: Optional[Set[int]] = (
        {int(item) for item in allowed} if allowed is not None else None
    )
    if allowed_ids is None:
        raise ActionRejected("召回需要显式 allow-list（舰队 id）", before_post=True)
    if int(target.id) not in allowed_ids:
        raise ActionRejected(
            f"舰队 {target.id} 不在 allow-list 里", before_post=True
        )

    after = client.recall_fleet(int(target.id))
    list_after = list(after or [])
    evidence = _verify(list_after, int(target.id))
    if evidence is None:
        raise ActionRejected(
            f"召回舰队 {target.id} 后页面没有出现返航标记，结果不明，不重试",
            before_post=False,
        )
    return RecallResult(
        status="recalled",
        fleet_id=int(target.id),
        mission=target.mission or "",
        target=_describe(target),
        reason="已召回，页面确认返航",
        fleets_before=len(before),
        fleets_after=len(list_after),
        evidence=evidence,
    )

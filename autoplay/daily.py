"""Daily sign-in (每日签到) check/claim for the unattended loop.

The game grants the daily bonus as a side effect of ``GET game.php?page=bonus``;
a second visit inside the 24h cooldown only redirects to the overview.  That
makes the decision trivial -- claim when the overview navigation says it is
available -- but it also means the *check* must never touch ``page=bonus``.
:func:`run_daily_bonus` therefore reads the navigation first (dry run) and lets
:meth:`NewStarClient.claim_daily_bonus` do the same pre-check in execute mode.

The module holds no timers: the caller decides how often to ask.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Outcome states reported to the journal/dashboard.
CLAIMED = "claimed"
AVAILABLE = "available"
COOLDOWN = "cooldown"
UNKNOWN = "unknown"


@dataclass
class DailyBonusOutcome:
    status: str
    reason: str = ""
    items: List[Dict[str, Any]] = field(default_factory=list)
    available_at: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def interesting(self) -> bool:
        """Whether a loop should journal this outcome.

        Cooldown is the steady state for most of the day; repeating it every
        cycle would bury the real events.
        """

        return self.status != COOLDOWN

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "items": list(self.items),
            "available_at": self.available_at,
            "evidence": dict(self.evidence),
        }


def _when(value: Optional[Any]) -> Optional[str]:
    return value.isoformat(sep=" ") if value else None


def run_daily_bonus(client: Any, *, dry_run: bool = True) -> DailyBonusOutcome:
    """Return the sign-in status, claiming it when ``dry_run`` is false.

    ``client`` only needs :meth:`get_daily_bonus_status` and (in execute mode)
    :meth:`claim_daily_bonus`.  A claim that the game refuses raises the SDK's
    own :class:`~newstar_sdk.exceptions.ActionRejected`; a cooldown is reported,
    not raised.
    """

    if dry_run:
        status = client.get_daily_bonus_status()
        if status.available is True:
            return DailyBonusOutcome(
                AVAILABLE,
                "签到可领取（dry-run 未领取）",
                available_at=_when(status.available_at),
            )
        if status.available is False:
            return DailyBonusOutcome(
                COOLDOWN, "签到冷却中", available_at=_when(status.available_at)
            )
        return DailyBonusOutcome(UNKNOWN, "导航中没有识别到签到状态")

    bonus = client.claim_daily_bonus()
    if bonus.claimed:
        return DailyBonusOutcome(
            CLAIMED,
            "已领取每日签到",
            items=[item.as_dict() for item in bonus.items],
            available_at=_when(bonus.status.available_at),
            evidence={
                "amounts": {str(key): value for key, value in bonus.amounts.items()},
                "server_time": _when(bonus.server_time),
            },
        )
    if bonus.status.available is False:
        return DailyBonusOutcome(
            COOLDOWN, "签到冷却中", available_at=_when(bonus.status.available_at)
        )
    return DailyBonusOutcome(UNKNOWN, "签到状态未知（导航中没有识别到）")

"""Threat assessment from the inbox and the fleet table (P2-2, read-only).

The account is a farm: someone scanning it is the early warning, a battle
report is the alarm.  This module turns messages + fleets into a deterministic
:class:`ThreatAssessment` -- who scanned, when, which colony, how much is on
hand, which fleets could come home -- and nothing else.  It never posts; the
opt-in recall lives in :mod:`autoplay.threat_executor` behind an allow-list.

Message text is server wall time and the parsing here is heuristic (one real
espionage sample so far), so treat the details as hints, not facts.  The level
is what the loop acts on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from newstar_sdk.models import Coordinates, FleetInfo, Message, Resources

#: 间谍报告 / espionage reports.
ESPIONAGE_CATEGORY = 0
#: 战斗报告 / battle reports.
BATTLE_CATEGORY = 3

#: A scan inside this window means "someone is looking right now".
DANGER_MINUTES = 60
#: A scan/battle report inside this window is worth watching.
WATCH_HOURS = 24

#: Body tokens that suggest the battle report is about *us* being hit.
DEFENSIVE_TOKENS = (
    "被攻击",
    "遭到攻击",
    "被掠夺",
    "被回收",
    "你的防御",
    "你的舰队被摧毁",
    "我方损失",
)

_COORD_RE = re.compile(r"\[\s*(\d{1,3})\s*:\s*(\d{1,3})\s*:\s*(\d{1,3})\s*\]")
_LABELS = ("敌对行星", "敌对", "敌方", "enemy planet", "enemy", "hostile")


@dataclass(frozen=True)
class EspionageEvent:
    """One espionage report after best-effort text parsing."""

    message_id: int = 0
    at: Optional[datetime] = None
    attacker: str = ""
    attacker_coordinates: Optional[Coordinates] = None
    target: str = ""
    target_coordinates: Optional[Coordinates] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "at": self.at.isoformat() if self.at is not None else None,
            "attacker": self.attacker,
            "attacker_coordinates": (
                self.attacker_coordinates.as_dict()
                if self.attacker_coordinates is not None
                else None
            ),
            "target": self.target,
            "target_coordinates": (
                self.target_coordinates.as_dict()
                if self.target_coordinates is not None
                else None
            ),
        }


def _coords(match: Any) -> Coordinates:
    return Coordinates(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _name_before(text: str, start: int, end: int) -> str:
    """Best-effort name: the last word before the coordinates, minus labels."""

    segment = text[start:end].strip(" ，。,.:：")
    tokens = [token for token in segment.split() if token]
    tokens = [token for token in tokens if token.casefold() not in _LABELS]
    if tokens:
        return tokens[-1].strip(" ，。,.:：")
    return segment


def parse_espionage(
    body: str,
    *,
    message_id: int = 0,
    at: Optional[datetime] = None,
) -> Optional[EspionageEvent]:
    """Parse one espionage body; ``None`` when it is not an espionage text.

    Real sample (2026-11-14)::

        敌对行星 母星 [3:494:3] 发射的间谍探针在您的下列殖民地周围被发现 母星 [3:505:8]

    The first coordinate is where the probes came from, the last one is the
    colony that was scanned.
    """

    text = (body or "").strip()
    if not text or "间谍" not in text:
        return None
    matches = list(_COORD_RE.finditer(text))
    if not matches:
        return EspionageEvent(message_id=message_id, at=at, attacker=text)
    first = matches[0]
    event = EspionageEvent(
        message_id=message_id,
        at=at,
        attacker=_name_before(text, 0, first.start()),
        attacker_coordinates=_coords(first),
    )
    if len(matches) >= 2:
        last = matches[-1]
        event = replace(
            event,
            target=_name_before(text, first.end(), last.start()),
            target_coordinates=_coords(last),
        )
    return event


def _battle_is_defensive(body: str) -> bool:
    folded = (body or "").casefold()
    return any(token.casefold() in folded for token in DEFENSIVE_TOKENS)


def _exposure(resources: Optional[Resources]) -> Optional[float]:
    """How many times over storage the three main resources are (None if unknown)."""

    if resources is None:
        return None
    ratios: List[float] = []
    for rid in (901, 902, 903):
        capacity = float(resources.capacities.get(rid, 0.0) or 0.0)
        if capacity <= 0:
            continue
        ratios.append(float(resources.amounts.get(rid, 0.0)) / capacity)
    return max(ratios) if ratios else None


@dataclass
class ThreatAssessment:
    """What the inbox and fleet table say about being a target."""

    level: str = "calm"  # "calm" | "watch" | "danger"
    reasons: List[str] = field(default_factory=list)
    espionage: List[EspionageEvent] = field(default_factory=list)
    battle_reports: List[int] = field(default_factory=list)
    in_flight: int = 0
    recallable: List[int] = field(default_factory=list)
    exposure: Optional[float] = None
    recommendations: List[str] = field(default_factory=list)

    @property
    def danger(self) -> bool:
        return self.level == "danger"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "reasons": list(self.reasons),
            "espionage": [event.as_dict() for event in self.espionage],
            "battle_reports": list(self.battle_reports),
            "in_flight": self.in_flight,
            "recallable": list(self.recallable),
            "exposure": round(self.exposure, 2) if self.exposure is not None else None,
            "recommendations": list(self.recommendations),
        }


def _window(now: datetime, delta: timedelta) -> datetime:
    return now - delta


def assess_threat(
    messages: Iterable[Message],
    fleets: Iterable[FleetInfo] = (),
    *,
    now: Optional[datetime] = None,
    resources: Optional[Resources] = None,
    danger_minutes: int = DANGER_MINUTES,
    watch_hours: int = WATCH_HOURS,
) -> ThreatAssessment:
    """Classify the current threat level.  Pure: no requests, no writes.

    ``now`` must be server wall time (the same clock ``Message.time`` uses);
    it defaults to the local naive clock.
    """

    clock = now or datetime.now()
    watch_from = _window(clock, timedelta(hours=watch_hours))
    danger_from = _window(clock, timedelta(minutes=danger_minutes))

    scans: List[EspionageEvent] = []
    battles: List[int] = []
    recent_scans: List[EspionageEvent] = []
    dangerous_scans: List[EspionageEvent] = []
    recent_battles = False
    dangerous_battles = False

    for message in messages:
        category = message.category
        at = message.time
        if category == ESPIONAGE_CATEGORY:
            event = parse_espionage(
                message.body or message.subject,
                message_id=message.id,
                at=at,
            )
            if event is None:
                continue
            scans.append(event)
            if at is None or at >= watch_from:
                recent_scans.append(event)
                if at is None or at >= danger_from:
                    dangerous_scans.append(event)
        elif category == BATTLE_CATEGORY:
            battles.append(message.id)
            if at is None or at >= watch_from:
                recent_battles = True
                if _battle_is_defensive(message.body):
                    dangerous_battles = True

    fleet_list = list(fleets)
    recallable = sorted(
        int(fleet.id)
        for fleet in fleet_list
        if fleet.id is not None and fleet.can_recall and not fleet.returning
    )
    exposure = _exposure(resources)

    reasons: List[str] = []
    recommendations: List[str] = []
    level = "calm"

    if dangerous_scans or dangerous_battles:
        level = "danger"
        for event in dangerous_scans:
            where = f" {event.target}" if event.target else ""
            origin = (
                f" {event.attacker_coordinates}"
                if event.attacker_coordinates is not None
                else ""
            )
            reasons.append(
                f"{danger_minutes} 分钟内被侦察：{event.attacker}{origin} →{where or '本星球'}"
            )
        if dangerous_battles:
            reasons.append("最近的战斗报告指向我方损失")
        recommendations.append("先把资源花掉或运走（造舰队/探险/仓库），别留在星球上")
        if recallable:
            recommendations.append(
                "召回在途舰队：.venv/bin/python scripts/threat.py --recall "
                f"--execute --allow {recallable[0]}"
            )
        recommendations.append("必要时写 state/STOP，人工处理后再恢复")
    elif recent_scans or recent_battles:
        level = "watch"
        if recent_scans:
            reasons.append(f"{watch_hours} 小时内有 {len(recent_scans)} 次侦察")
        if recent_battles:
            reasons.append("最近有战斗报告（正文无法判断攻守）")
        recommendations.append("留意后续侦察/战斗报告；不需要改变支出")
    else:
        reasons.append("没有近期侦察或战斗报告")

    if exposure is not None and exposure >= 1.0 and level != "calm":
        recommendations.append(
            f"资源已达仓容的 {exposure:.0f} 倍，是明显的目标"
        )

    return ThreatAssessment(
        level=level,
        reasons=reasons,
        espionage=scans,
        battle_reports=battles,
        in_flight=len(fleet_list),
        recallable=recallable,
        exposure=exposure,
        recommendations=recommendations,
    )



def assess_client(client: Any, *, now: Optional[datetime] = None) -> ThreatAssessment:
    """Read the inbox tabs and fleet table, then assess.  Read-only.

    Three GETs plus the overview: espionage reports, battle reports, fleets and
    resource exposure.  Message time is server wall time; pass ``now`` in the
    same clock when you have it.
    """

    messages: List[Message] = []
    for category in (ESPIONAGE_CATEGORY, BATTLE_CATEGORY):
        page = client.get_messages(category=category)
        messages.extend(getattr(page, "messages", []) or [])
    fleets = client.get_fleets()
    resources = client.get_overview().resources
    return assess_threat(messages, fleets, now=now, resources=resources)

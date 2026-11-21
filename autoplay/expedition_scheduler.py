"""Continuous expedition farming: keep every expedition lane occupied.

The game allows ``floor(sqrt(探险技术))`` expedition fleets at once and counts
both outbound and returning fleets against that limit.  This module keeps a
small timetable of expected return times, sends a fixed composition while a
lane is free, and wakes up shortly after the earliest return plus a configurable
delay (5 s by default) to send the next wave.

It deliberately contains no automatic ship building or recalling.  The game is
the authority for fuel, fleet slots, ship availability and stay-time options;
a rejected send is reported instead of being retried immediately.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from newstar_sdk.constants import CATEGORY_SHIP, SHIPS, Mission, resolve_element
from newstar_sdk.exceptions import NewStarError
from newstar_sdk.models import Coordinates, FleetDispatch, FleetInfo, FleetShips

from .expedition import (
    expedition_slot_count,
    expedition_tech_level,
    is_expedition_fleet,
)

DEFAULT_SHIP_ID = 207
DEFAULT_SHIP_COUNT = 8621
DEFAULT_CHECK_SECONDS = 10 * 3600.0
DEFAULT_RETURN_DELAY = 5.0
DEFAULT_RETRY_DELAY = 60.0
DEFAULT_MAX_SLEEP = 900.0
HISTORY_LIMIT = 200


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_TIME_FORMATS = (
    "%Y年%m月%d日, %H:%M:%S",
    "%Y年%m月%d日 %H:%M:%S",
    "%Y年%m月%d日, %H:%M",
    "%Y年%m月%d日 %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
)

_DURATION_RE = re.compile(r"(\d+)\s*(天|小时|时|分|秒|d|h|m|s)", re.IGNORECASE)
_DURATION_UNITS = {
    "d": 86400,
    "天": 86400,
    "h": 3600,
    "小时": 3600,
    "时": 3600,
    "m": 60,
    "分": 60,
    "s": 1,
    "秒": 1,
}


def parse_composition(raw: str) -> Dict[int, int]:
    """Parse a fleet composition into a ship-id -> count map.

    Accepted forms: ``207=8621``, ``215:1000``, ``215x1000``,
    ``战列巡洋舰=1000``, ``battlecruiser=1000``.  Several types are separated
    by commas or semicolons, e.g. ``207=1000,203=100``.  Numeric pairs may also
    be space separated (``207=1000 203=100``) for compatibility.

    The composition is used verbatim; this function never scores fleets or
    enforces a fleet-points target.
    """

    ships: Dict[int, int] = {}
    text = (
        (raw or "")
        .replace("，", ",")
        .replace("：", ":")
        .replace("＝", "=")
        .strip()
    )
    if not text:
        raise ValueError("舰船组成为空（示例 207=8621 或 战列舰=8621）")

    tokens = [item.strip() for item in re.split(r"[,;\n]+", text) if item.strip()]
    # Compatibility: old CLI used spaces between numeric pairs.
    if len(tokens) == 1 and re.fullmatch(
        r"(?:\d+\s*[=:xX*]\s*\d+\s*)+", tokens[0]
    ):
        tokens = re.findall(r"\d+\s*[=:xX*]\s*\d+", tokens[0])

    for token in tokens:
        if token.count("=") + token.count(":") > 1:
            raise ValueError(
                f"无法解析舰船组成：{token!r}（多种舰船请用逗号分隔）"
            )
        match = re.fullmatch(r"(.+?)\s*[=:]\s*(\d+)", token)
        if match is None:
            match = re.fullmatch(r"(\d+)\s*[xX*]\s*(\d+)", token)
        if match is None:
            raise ValueError(
                f"无法解析舰船组成：{token!r}"
                "（示例 207=8621 或 战列舰=8621；多种舰船用逗号分隔）"
            )
        key = match.group(1).strip()
        count = int(match.group(2))
        if count <= 0:
            raise ValueError(f"舰船数量必须为正：{token!r}")
        try:
            ship_id = resolve_element(key, CATEGORY_SHIP)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"未知舰船：{key!r}") from exc
        ships[ship_id] = ships.get(ship_id, 0) + count
    if not ships:
        raise ValueError("舰船组成为空（示例 207=8621 或 战列舰=8621）")
    return ships


def ship_label(ship_id: int) -> str:
    element = SHIPS.get(int(ship_id))
    return element.label if element is not None else f"#{int(ship_id)}"


def format_ships(ships: Mapping[Any, int]) -> str:
    parts: List[str] = []
    for key, count in ships.items():
        try:
            ship_id = int(key)
            parts.append(f"{ship_label(ship_id)}({ship_id})×{int(count)}")
        except (TypeError, ValueError):
            parts.append(f"{key}×{int(count)}")
    return "、".join(parts)


def parse_game_time(value: Optional[str]) -> Optional[datetime]:
    """Parse a game wall-clock timestamp (naive, in the viewer's timezone)."""

    if not value:
        return None
    text = str(value).strip().replace("\u00a0", " ").replace("，", ",")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_duration(value: Optional[str]) -> Optional[float]:
    """Parse the fleet table's remaining-time string (``05h 12m 03s``).

    Chinese and English unit suffixes are accepted; ``None`` means the string
    did not look like a duration.
    """

    if not value:
        return None
    total = 0
    matched = False
    for amount, unit in _DURATION_RE.findall(str(value)):
        key = unit.lower() if unit.isascii() else unit
        seconds = _DURATION_UNITS.get(key)
        if seconds is None:
            continue
        total += int(amount) * seconds
        matched = True
    return float(total) if matched else None


def coordinate_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        coordinates = Coordinates.parse(str(value))
    except ValueError:
        return None
    return f"{coordinates.galaxy}:{coordinates.system}:{coordinates.planet}"


def iso_local(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch)).astimezone().isoformat(
        timespec="seconds"
    )


def _as_epoch(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Server clock
# ---------------------------------------------------------------------------


@dataclass
class GameClock:
    """Map game wall-clock timestamps to host epoch seconds.

    The game renders fleet times in the viewer's configured timezone while the
    host may run in UTC.  The overview's ``server_time`` is in that same
    timezone, so its difference from the host clock is the offset to apply.
    """

    offset_seconds: float = 0.0

    @classmethod
    def from_overview(cls, overview: Any, now: Optional[datetime] = None) -> "GameClock":
        server_time = getattr(overview, "server_time", None)
        if server_time is None:
            return cls()
        host_now = now or datetime.now()
        return cls((server_time - host_now).total_seconds())

    def now(self) -> datetime:
        return datetime.now() + timedelta(seconds=self.offset_seconds)

    def epoch(self, wall: Optional[datetime]) -> Optional[float]:
        if wall is None:
            return None
        return time.time() + (wall - self.now()).total_seconds()


# ---------------------------------------------------------------------------
# Plan and timetable
# ---------------------------------------------------------------------------


@dataclass
class ExpeditionPlan:
    target: str
    ships: Dict[int, int]
    speed: int = 10
    stay_time: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "ships": {str(key): int(value) for key, value in self.ships.items()},
            "speed": int(self.speed),
            "stay_time": int(self.stay_time),
        }


@dataclass
class ScheduledSlot:
    """One occupied expedition lane, with its expected home-return time."""

    target: Optional[str] = None
    ships: Dict[str, int] = field(default_factory=dict)
    mission: str = ""
    returning: bool = False
    expected_return_at: Optional[float] = None
    expected_return_game: str = ""
    remaining: str = ""
    ours: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "ships": {str(key): int(value) for key, value in self.ships.items()},
            "mission": self.mission,
            "returning": bool(self.returning),
            "expected_return_at": self.expected_return_at,
            "expected_return_local": iso_local(self.expected_return_at),
            "expected_return_game": self.expected_return_game,
            "remaining": self.remaining,
            "ours": bool(self.ours),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScheduledSlot":
        raw_ships = data.get("ships") or {}
        ships: Dict[str, int] = {}
        if isinstance(raw_ships, Mapping):
            for key, value in raw_ships.items():
                try:
                    ships[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue
        return cls(
            target=str(data["target"]) if data.get("target") else None,
            ships=ships,
            mission=str(data.get("mission") or ""),
            returning=bool(data.get("returning")),
            expected_return_at=_as_epoch(data.get("expected_return_at")),
            expected_return_game=str(data.get("expected_return_game") or ""),
            remaining=str(data.get("remaining") or ""),
            ours=bool(data.get("ours")),
        )

    @classmethod
    def from_fleet(
        cls,
        fleet: FleetInfo,
        clock: GameClock,
        *,
        plan_target: Optional[str] = None,
    ) -> "ScheduledSlot":
        """Build a slot from a fleet-table row.

        In the fleet table, column 4 (``FleetInfo.start_time``) is the arrival
        at the target, column 6 (``FleetInfo.target_time``) is the absolute
        home-return time and column 7 (``FleetInfo.arrival_time``) is the
        *remaining-time string* (``05h 12m``), despite the model names.
        """

        target = (
            str(fleet.target_coordinates)
            if fleet.target_coordinates is not None
            else None
        )
        expected_game = fleet.target_time or ""
        wall = parse_game_time(expected_game)
        expected = clock.epoch(wall)
        if expected is None:
            remaining_seconds = parse_duration(fleet.arrival_time)
            if remaining_seconds is not None:
                expected = time.time() + remaining_seconds
        ships = {
            str(name): int(count)
            for name, count in (fleet.ships or {}).items()
        }
        return cls(
            target=target,
            ships=ships,
            mission=fleet.mission or "",
            returning=bool(fleet.returning),
            expected_return_at=expected,
            expected_return_game=expected_game,
            remaining=fleet.arrival_time or "",
            ours=(
                plan_target is not None
                and coordinate_key(target) == coordinate_key(plan_target)
            ),
        )

    @classmethod
    def from_dispatch(
        cls,
        dispatch: FleetDispatch,
        plan: ExpeditionPlan,
        clock: GameClock,
    ) -> "ScheduledSlot":
        """Build a slot from the dispatch confirmation page.

        ``FleetDispatch.arrival_time`` is the confirmation template's
        ``fleetEndTime``: the absolute time the fleet is back home.
        """

        expected_game = dispatch.arrival_time or ""
        wall = parse_game_time(expected_game)
        target = (
            str(dispatch.target_coordinates)
            if dispatch.target_coordinates is not None
            else plan.target
        )
        return cls(
            target=target,
            ships={
                f"{ship_label(ship_id)}({ship_id})": int(count)
                for ship_id, count in plan.ships.items()
            },
            mission=dispatch.mission_name or "探险",
            returning=False,
            expected_return_at=clock.epoch(wall),
            expected_return_game=expected_game,
            remaining="",
            ours=coordinate_key(target) == coordinate_key(plan.target),
        )


@dataclass
class ExpeditionTimetable:
    """Persistent view of the lanes and their expected return times."""

    path: Path = field(
        default_factory=lambda: Path("state/expedition_timetable.json")
    )
    schema: int = 1
    planet_id: Optional[int] = None
    planet_label: str = ""
    planet_coordinates: str = ""
    target: str = ""
    ships: Dict[int, int] = field(default_factory=dict)
    speed: int = 10
    stay_time: int = 1
    max_slots: int = 0
    tech_level: int = 0
    checked_at: Optional[float] = None
    next_check_at: float = 0.0
    slots: List[ScheduledSlot] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    load_error: str = ""
    save_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "planet": {
                "id": self.planet_id,
                "name": self.planet_label,
                "coordinates": self.planet_coordinates,
            },
            "plan": {
                "target": self.target,
                "ships": {str(key): int(value) for key, value in self.ships.items()},
                "speed": int(self.speed),
                "stay_time": int(self.stay_time),
            },
            "max_slots": int(self.max_slots),
            "tech_level": int(self.tech_level),
            "checked_at": self.checked_at,
            "checked_at_local": iso_local(self.checked_at),
            "next_check_at": self.next_check_at or None,
            "next_check_local": iso_local(self.next_check_at or None),
            "slots": [slot.as_dict() for slot in self.slots],
            "history": list(self.history),
            "updated_at": self.updated_at,
            "updated_at_local": iso_local(self.updated_at),
        }

    @classmethod
    def load(cls, path: Any) -> "ExpeditionTimetable":
        timetable = cls(path=Path(path))
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return timetable
        except OSError as exc:
            timetable.load_error = f"{type(exc).__name__}: {exc}"
            return timetable
        try:
            data = json.loads(raw)
        except ValueError as exc:
            timetable.load_error = f"{type(exc).__name__}: {exc}"
            return timetable
        if not isinstance(data, Mapping):
            timetable.load_error = "时间表不是 JSON 对象"
            return timetable

        planet = data.get("planet") or {}
        if isinstance(planet, Mapping):
            planet_id = planet.get("id")
            try:
                timetable.planet_id = int(planet_id) if planet_id is not None else None
            except (TypeError, ValueError):
                timetable.planet_id = None
            timetable.planet_label = str(planet.get("name") or "")
            timetable.planet_coordinates = str(planet.get("coordinates") or "")
        plan = data.get("plan") or {}
        if isinstance(plan, Mapping):
            timetable.target = str(plan.get("target") or "")
            raw_ships = plan.get("ships") or {}
            if isinstance(raw_ships, Mapping):
                for key, value in raw_ships.items():
                    try:
                        timetable.ships[int(key)] = int(value)
                    except (TypeError, ValueError):
                        continue
            try:
                timetable.speed = int(plan.get("speed", 10))
            except (TypeError, ValueError):
                timetable.speed = 10
            try:
                timetable.stay_time = int(plan.get("stay_time", 1))
            except (TypeError, ValueError):
                timetable.stay_time = 1
        try:
            timetable.schema = int(data.get("schema", 1))
            timetable.max_slots = int(data.get("max_slots", 0))
            timetable.tech_level = int(data.get("tech_level", 0))
        except (TypeError, ValueError):
            pass
        timetable.checked_at = _as_epoch(data.get("checked_at"))
        timetable.next_check_at = _as_epoch(data.get("next_check_at")) or 0.0
        raw_slots = data.get("slots") or []
        if isinstance(raw_slots, list):
            timetable.slots = [
                ScheduledSlot.from_dict(item)
                for item in raw_slots
                if isinstance(item, Mapping)
            ]
        raw_history = data.get("history") or []
        if isinstance(raw_history, list):
            timetable.history = [
                dict(item) for item in raw_history if isinstance(item, Mapping)
            ][-HISTORY_LIMIT:]
        timetable.updated_at = _as_epoch(data.get("updated_at")) or 0.0
        return timetable

    def save(self) -> bool:
        self.updated_at = time.time()
        path = Path(self.path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            self.save_error = f"{type(exc).__name__}: {exc}"
            return False
        self.save_error = ""
        return True

    def record_slots(self, slots: Iterable[ScheduledSlot], now: Optional[float] = None) -> None:
        self.slots = list(slots)
        if now is not None:
            self.updated_at = now

    def record_send(self, slot: ScheduledSlot, now: Optional[float] = None) -> None:
        self.slots.append(slot)
        at = float(now if now is not None else time.time())
        entry = slot.as_dict()
        entry["event"] = "sent"
        entry["at"] = at
        entry["at_local"] = iso_local(at)
        self.history.append(entry)
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]
        self.updated_at = at

    def next_wake(
        self,
        now: float,
        *,
        return_delay: float = DEFAULT_RETURN_DELAY,
        retry_delay: float = DEFAULT_RETRY_DELAY,
        max_sleep: float = DEFAULT_MAX_SLEEP,
    ) -> float:
        """Earliest of the next return (+delay), slot check and safety poll."""

        now = float(now)
        candidates: List[float] = [now + max(1.0, float(max_sleep))]
        if self.next_check_at:
            candidates.append(self.next_check_at)
        for slot in self.slots:
            if slot.expected_return_at is None:
                continue
            candidate = slot.expected_return_at + max(0.0, float(return_delay))
            if candidate <= now:
                # The game may still list a returning fleet after its ETA
                # (arrival processing is not instantaneous); retry later.
                candidate = now + max(1.0, float(retry_delay))
            candidates.append(candidate)
        future = [candidate for candidate in candidates if candidate > now]
        return min(future) if future else now + max(1.0, float(retry_delay))


# ---------------------------------------------------------------------------
# Slot reading and filling
# ---------------------------------------------------------------------------


def read_max_slots(client: Any) -> Tuple[int, int]:
    """Read the research page and return ``(expedition_tech, max_slots)``."""

    research = client.get_research()
    tech = expedition_tech_level(research)
    return tech, expedition_slot_count(tech)


def read_live_slots(
    client: Any,
    clock: GameClock,
    *,
    plan_target: Optional[str] = None,
) -> List[ScheduledSlot]:
    """Read the fleet table and return one slot per occupied expedition lane."""

    fleets: Iterable[FleetInfo] = client.get_fleets()
    return [
        ScheduledSlot.from_fleet(fleet, clock, plan_target=plan_target)
        for fleet in fleets
        if is_expedition_fleet(fleet)
    ]


@dataclass
class ExpeditionFillResult:
    status: str  # "dry_run" | "dispatched" | "partial" | "skipped" | "error"
    active: int = 0
    max_slots: int = 0
    free: int = 0
    sent: List[ScheduledSlot] = field(default_factory=list)
    reason: str = ""
    preview: Any = None

    def as_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status,
            "active": int(self.active),
            "max_slots": int(self.max_slots),
            "free": int(self.free),
            "sent": [slot.as_dict() for slot in self.sent],
            "reason": self.reason,
        }
        preview = self.preview
        if preview is not None:
            payload["preview"] = {
                "consumption": getattr(preview, "consumption", None),
                "stay_times": list(getattr(preview, "stay_times", []) or []),
                "available_missions": [
                    {"id": option.id, "name": option.name}
                    for option in (getattr(preview, "available_missions", []) or [])
                ],
            }
        return payload


def _available_counts(client: Any) -> Dict[int, int]:
    raw = client.get_available_ships()
    counts: Dict[int, int] = {}
    for key, value in (raw or {}).items():
        try:
            ship_id = int(key)
        except (TypeError, ValueError):
            continue
        if isinstance(value, FleetShips):
            count = int(value.count)
        else:
            count = int(getattr(value, "count", value))
        counts[ship_id] = count
    return counts


def fill_expedition_slots(
    client: Any,
    *,
    plan: ExpeditionPlan,
    clock: GameClock,
    max_slots: int,
    active: int,
    dry_run: bool = False,
    max_sends: Optional[int] = None,
    available_ships: Optional[Mapping[int, int]] = None,
    check_ships: bool = True,
    stop_check: Optional[Callable[[], bool]] = None,
) -> ExpeditionFillResult:
    """Preview or send the composition until no free lane remains.

    With ``check_ships=False`` the local available-ship count is skipped and
    the game decides whether the composition can be sent.  This is useful when
    the caller wants to send exactly the configured counts and does not trust a
    cached/partial fleet-table count.
    """

    max_slots = max(0, int(max_slots))
    active = max(0, int(active))
    free = max(0, max_slots - active)
    result = ExpeditionFillResult(
        status="skipped",
        active=active,
        max_slots=max_slots,
        free=free,
    )
    if max_slots <= 0:
        result.reason = "没有探险航道（探险技术为 0）"
        return result
    if free <= 0:
        result.reason = f"探险航道已满（{active} / {max_slots}）"
        return result
    if not plan.ships:
        result.status = "error"
        result.reason = "舰船组成为空"
        return result

    if dry_run:
        try:
            preview = client.preview_fleet(
                plan.target,
                plan.ships,
                mission=Mission.EXPEDITION,
                speed=plan.speed,
            )
        except NewStarError as exc:
            result.status = "error"
            result.reason = f"{type(exc).__name__}: {exc}"
            return result
        notes = [
            f"预览：空闲 {free} 条航道，将派出 {free} 队"
            f"（{format_ships(plan.ships)}，速度 {plan.speed}，停留 {plan.stay_time}）"
        ]
        stay_times = list(getattr(preview, "stay_times", []) or [])
        if stay_times and int(plan.stay_time) not in stay_times:
            notes.append(f"警告：停留 {plan.stay_time} 不在可选 {stay_times}")
        result.status = "dry_run"
        result.reason = "；".join(notes)
        result.preview = preview
        return result

    available: Optional[Dict[int, int]] = None
    if check_ships:
        if available_ships is None:
            try:
                available = _available_counts(client)
            except NewStarError as exc:
                result.status = "error"
                result.reason = f"{type(exc).__name__}: {exc}"
                return result
        else:
            available = {
                int(key): int(value) for key, value in available_ships.items()
            }

        missing = {
            ship_id: int(count) - int(available.get(int(ship_id), 0))
            for ship_id, count in plan.ships.items()
            if int(available.get(int(ship_id), 0)) < int(count)
        }
        if missing:
            detail = "、".join(
                f"{ship_label(ship_id)}({ship_id}) 需要 {plan.ships[ship_id]}，"
                f"当前 {available.get(int(ship_id), 0)}"
                for ship_id in sorted(missing)
            )
            result.reason = f"舰船不足：{detail}"
            return result

    limit = free if max_sends is None or int(max_sends) <= 0 else min(free, int(max_sends))
    failed = ""
    stopped = False
    for _ in range(limit):
        if stop_check is not None and stop_check():
            stopped = True
            break
        if available is not None:
            shortage = {
                ship_id: int(count) - int(available.get(int(ship_id), 0))
                for ship_id, count in plan.ships.items()
                if int(available.get(int(ship_id), 0)) < int(count)
            }
            if shortage:
                failed = "舰船不足：" + "、".join(
                    f"{ship_label(ship_id)}({ship_id}) 需要 {plan.ships[ship_id]}，"
                    f"当前 {available.get(int(ship_id), 0)}"
                    for ship_id in sorted(shortage)
                )
                break
        try:
            dispatch = client.send_fleet(
                plan.target,
                plan.ships,
                mission=Mission.EXPEDITION,
                speed=plan.speed,
                stay_time=plan.stay_time,
            )
        except NewStarError as exc:
            failed = f"{type(exc).__name__}: {exc}"
            break
        result.sent.append(ScheduledSlot.from_dispatch(dispatch, plan, clock))
        if available is not None:
            for ship_id, count in plan.ships.items():
                available[int(ship_id)] = int(available.get(int(ship_id), 0)) - int(
                    count
                )

    result.free = max(0, free - len(result.sent))
    if stopped and not result.sent:
        result.status = "skipped"
        result.reason = "急停：state/STOP"
        return result
    if result.sent and (failed or stopped):
        result.status = "partial"
        detail = failed or "急停"
        result.reason = (
            f"已派出 {len(result.sent)} 队，随后停止：{detail}；"
            f"剩余空闲 {result.free}"
        )
    elif result.sent:
        result.status = "dispatched"
        result.reason = f"已派出 {len(result.sent)} 队；剩余空闲 {result.free}"
    elif failed:
        result.status = "error"
        result.reason = f"发送失败：{failed}"
    return result


__all__ = [
    "DEFAULT_CHECK_SECONDS",
    "DEFAULT_MAX_SLEEP",
    "DEFAULT_RETRY_DELAY",
    "DEFAULT_RETURN_DELAY",
    "DEFAULT_SHIP_COUNT",
    "DEFAULT_SHIP_ID",
    "ExpeditionFillResult",
    "ExpeditionPlan",
    "ExpeditionTimetable",
    "GameClock",
    "ScheduledSlot",
    "coordinate_key",
    "fill_expedition_slots",
    "format_ships",
    "iso_local",
    "parse_composition",
    "parse_duration",
    "parse_game_time",
    "read_live_slots",
    "read_max_slots",
    "ship_label",
]

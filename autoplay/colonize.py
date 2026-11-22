"""Automatic colonization: scan a coordinate box, colonize, keep or abandon.

This module holds the reusable, request-free parts of ``scripts/colonize.py``:

* :class:`ColonizeRange` -- the ``1:2:3 4:5:6`` (two corners) and
  ``1-4:2-5:3-6`` (three ranges) specifications;
* :class:`PlanetFilter` -- optional ``面积`` / ``最高温度`` / ``最低温度``
  conditions plus the global ``accept`` switch;
* :func:`available_colony_lanes` -- free fleet lanes after reserving the
  expedition share (``舰队航道 - 最大探险队``);
* :func:`effective_limit` -- ``min(user cap, colony slots still free)``;
* :class:`ColonizeState` -- the persistent timetable/ledger: scan cursor,
  candidate queue, kept/discarded/failed planets and the in-flight colony
  fleets with their expected arrival.

Nothing here talks HTTP; the game is still the authority for whether a send
works, whether a position is free and how big/temperate a new colony is.
"""

from __future__ import annotations

import json
import operator
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from newstar_sdk.constants import Mission
from newstar_sdk.models import Coordinates, FleetSlots, GalaxyView

#: This universe's layout: ``1:1:1`` - ``99:999:15``.
GALAXY_MAX = 99
SYSTEM_MAX = 999
POSITION_MAX = 15

HISTORY_LIMIT = 200

STATUS_FREE = "free"
STATUS_COLONIZING = "colonizing"
STATUS_KEPT = "kept"
STATUS_DISCARDED = "discarded"
STATUS_FAILED = "failed"

#: Statuses that count as "this position has been explored and decided".
TERMINAL_STATUSES = frozenset({STATUS_KEPT, STATUS_DISCARDED, STATUS_FAILED})

#: Rendered by the galaxy page for a colony that is being cleaned up.
_ABANDONED_NAMES = frozenset({"被遗弃的殖民地", "abandoned colony"})


# ---------------------------------------------------------------------------
# Coordinate ranges
# ---------------------------------------------------------------------------

_COORD_RE = re.compile(r"\[?\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*\]?")
_AXIS_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


@dataclass(frozen=True)
class IntRange:
    """An inclusive integer interval on one axis."""

    low: int
    high: int

    def __post_init__(self) -> None:
        if self.low > self.high:
            object.__setattr__(self, "low", self.high)
            object.__setattr__(self, "high", self.low)

    def __contains__(self, value: object) -> bool:
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        return self.low <= number <= self.high

    @property
    def span(self) -> int:
        return self.high - self.low + 1

    def as_text(self) -> str:
        return f"{self.low}-{self.high}"

    def as_dict(self) -> Dict[str, int]:
        return {"low": self.low, "high": self.high}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntRange":
        return cls(int(data["low"]), int(data["high"]))

    @classmethod
    def parse(cls, text: str) -> "IntRange":
        match = _AXIS_RE.match(text or "")
        if match is None:
            raise ValueError(f"无法解析范围：{text!r}")
        low = int(match.group(1))
        high = int(match.group(2)) if match.group(2) is not None else low
        return cls(low, high)


@dataclass(frozen=True)
class ColonizeRange:
    """A box in the universe: every galaxy × system × position in it."""

    galaxy: IntRange
    system: IntRange
    position: IntRange

    # -- parsing ---------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "ColonizeRange":
        """Parse ``"1:2:3 4:5:6"`` (two corners) or ``"1-4:2-5:3-6"``.

        The corners may come in any order; the range is normalised to low/high
        per axis.  A single coordinate (``"1:2:3"``) is accepted too and means
        exactly that one position.
        """

        raw = (text or "").strip()
        if not raw:
            raise ValueError("殖民范围为空（示例：1:2:3 4:5:6 或 1-4:2-5:3-6）")
        points = [
            (int(match.group(1)), int(match.group(2)), int(match.group(3)))
            for match in _COORD_RE.finditer(raw)
        ]
        without_points = _COORD_RE.sub(" ", raw)
        looks_like_corners = len(points) >= 2 and not re.search(r"[\d-]", without_points)

        if looks_like_corners:
            galaxies = [item[0] for item in points]
            systems = [item[1] for item in points]
            positions = [item[2] for item in points]
            result = cls(
                IntRange(min(galaxies), max(galaxies)),
                IntRange(min(systems), max(systems)),
                IntRange(min(positions), max(positions)),
            )
        else:
            axes = [part.strip() for part in raw.strip("[] ").split(":")]
            if len(axes) != 3:
                raise ValueError(
                    f"无法解析殖民范围：{text!r}"
                    "（示例：1:2:3 4:5:6 或 1-4:2-5:3-6）"
                )
            try:
                result = cls(*(IntRange.parse(axis) for axis in axes))
            except ValueError as exc:
                raise ValueError(f"无法解析殖民范围：{text!r}（{exc}）") from exc
        _validate_range(result)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ColonizeRange":
        return cls(
            IntRange.from_dict(data["galaxy"]),
            IntRange.from_dict(data["system"]),
            IntRange.from_dict(data["position"]),
        )

    # -- geometry --------------------------------------------------------

    def __contains__(self, coordinates: object) -> bool:
        if not isinstance(coordinates, Coordinates):
            try:
                coordinates = Coordinates.parse(str(coordinates))
            except (TypeError, ValueError):
                return False
        return (
            coordinates.galaxy in self.galaxy
            and coordinates.system in self.system
            and coordinates.planet in self.position
        )

    @property
    def system_count(self) -> int:
        return self.galaxy.span * self.system.span

    @property
    def position_count(self) -> int:
        return self.position.span

    @property
    def slot_count(self) -> int:
        return self.system_count * self.position_count

    def system_at(self, index: int) -> Tuple[int, int]:
        """The ``index``-th ``(galaxy, system)`` in scan order."""

        offset = int(index)
        if offset < 0 or offset >= self.system_count:
            raise IndexError(f"system index {index} outside 0..{self.system_count - 1}")
        return (
            self.galaxy.low + offset // self.system.span,
            self.system.low + offset % self.system.span,
        )

    def systems(self) -> Iterator[Tuple[int, int]]:
        for galaxy in range(self.galaxy.low, self.galaxy.high + 1):
            for system in range(self.system.low, self.system.high + 1):
                yield galaxy, system

    def positions(self) -> List[int]:
        return list(range(self.position.low, self.position.high + 1))

    def as_text(self) -> str:
        return (
            f"{self.galaxy.as_text()}:{self.system.as_text()}:"
            f"{self.position.as_text()}"
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "galaxy": self.galaxy.as_dict(),
            "system": self.system.as_dict(),
            "position": self.position.as_dict(),
            "systems": self.system_count,
            "slots": self.slot_count,
        }


def _validate_range(box: ColonizeRange) -> None:
    checks = (
        ("河系", box.galaxy, 1, GALAXY_MAX),
        ("太阳系", box.system, 1, SYSTEM_MAX),
        ("位置", box.position, 1, POSITION_MAX),
    )
    for label, axis, low, high in checks:
        if axis.low < low or axis.high > high:
            raise ValueError(
                f"{label}范围 {axis.as_text()} 超出本宇宙 1..{high}（{label}）"
            )


# ---------------------------------------------------------------------------
# Planet filter
# ---------------------------------------------------------------------------

_FIELD_ALIASES: Dict[str, str] = {
    "area": "area",
    "field": "area",
    "fields": "area",
    "面积": "area",
    "temp_max": "temp_max",
    "max_temp": "temp_max",
    "maxtemp": "temp_max",
    "tmax": "temp_max",
    "最高温度": "temp_max",
    "温度上限": "temp_max",
    "temp_min": "temp_min",
    "min_temp": "temp_min",
    "mintemp": "temp_min",
    "tmin": "temp_min",
    "最低温度": "temp_min",
    "温度下限": "temp_min",
}

_FIELD_LABELS = {"area": "面积", "temp_max": "最高温度", "temp_min": "最低温度"}

_OPERATORS: Dict[str, Callable[[float, float], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
    "==": operator.eq,
    "!=": operator.ne,
    "≥": operator.ge,
    "≤": operator.le,
}

_OPERATOR_ALIASES = {"≥": ">=", "≤": "<="}

_CONDITION_RE = re.compile(
    r"^\s*([^\d<>=≤≥!]+?)\s*(>=|<=|==|!=|>|<|=|≥|≤)\s*(-?\d+(?:\.\d+)?)\s*$"
)


@dataclass(frozen=True)
class Condition:
    """One ``field op value`` requirement, e.g. ``面积 > 260``."""

    field_name: str
    op: str
    value: float

    def matches(self, value: Optional[float]) -> bool:
        if value is None:
            return False
        compare = _OPERATORS[self.op]
        return bool(compare(float(value), float(self.value)))

    @property
    def label(self) -> str:
        return _FIELD_LABELS.get(self.field_name, self.field_name)

    def as_text(self) -> str:
        return f"{self.label}{self.op}{_format_number(self.value)}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field_name,
            "op": self.op,
            "value": self.value,
            "text": self.as_text(),
        }

    @classmethod
    def parse(cls, text: str) -> "Condition":
        normalized = (
            (text or "")
            .replace("＞", ">")
            .replace("＜", "<")
            .replace("＝", "=")
            .replace("！", "!")
            .replace("　", " ")
        )
        match = _CONDITION_RE.match(normalized)
        if match is None:
            raise ValueError(
                f"无法解析条件：{text!r}（示例：面积>260、最高温度<50、最低温度>-50）"
            )
        raw_field = match.group(1).strip().casefold().replace(" ", "")
        field_name = _FIELD_ALIASES.get(raw_field)
        if field_name is None:
            known = "、".join(sorted({*_FIELD_LABELS.values(), "area", "temp_max", "temp_min"}))
            raise ValueError(f"未知条件字段：{match.group(1).strip()!r}（可用：{known}）")
        op = match.group(2)
        return cls(field_name, _OPERATOR_ALIASES.get(op, op), float(match.group(3)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Condition":
        return cls(str(data["field"]), str(data["op"]), float(data["value"]))


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


@dataclass
class PlanetFilter:
    """The ``accept`` switch plus the optional per-field conditions."""

    accept: bool = True
    conditions: List[Condition] = field(default_factory=list)

    @classmethod
    def parse(
        cls, where: Optional[Iterable[str]], *, accept: bool = True
    ) -> "PlanetFilter":
        conditions: List[Condition] = []
        for item in where or []:
            for part in str(item).replace("，", ",").split(","):
                text = part.strip()
                if text:
                    conditions.append(Condition.parse(text))
        return cls(accept=bool(accept), conditions=conditions)

    def fields(self) -> List[str]:
        seen: List[str] = []
        for condition in self.conditions:
            if condition.field_name not in seen:
                seen.append(condition.field_name)
        return seen

    def evaluate(
        self,
        *,
        area: Optional[float] = None,
        temp_max: Optional[float] = None,
        temp_min: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """Return ``(ok, reason)``; an unset field is never checked."""

        if not self.accept:
            return False, "accept=false：不接受任何星球"
        if not self.conditions:
            return True, "未指定条件（全部接受）"
        values = {"area": area, "temp_max": temp_max, "temp_min": temp_min}
        reasons: List[str] = []
        for condition in self.conditions:
            value = values.get(condition.field_name)
            if value is None:
                reasons.append(f"{condition.label} 未知")
            elif not condition.matches(value):
                reasons.append(
                    f"{condition.label} {_format_number(float(value))} "
                    f"不满足 {condition.as_text()}"
                )
        if reasons:
            return False, "；".join(reasons)
        return True, "满足全部条件"

    def as_text(self) -> str:
        if not self.accept:
            return "accept=false"
        return "、".join(condition.as_text() for condition in self.conditions) or "无"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "accept": bool(self.accept),
            "conditions": [condition.as_dict() for condition in self.conditions],
            "text": self.as_text(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanetFilter":
        return cls(
            accept=bool(data.get("accept", True)),
            conditions=[
                Condition.from_dict(item)
                for item in (data.get("conditions") or [])
                if isinstance(item, Mapping)
            ],
        )


# ---------------------------------------------------------------------------
# Fleet lanes and the acceptance cap
# ---------------------------------------------------------------------------


def available_colony_lanes(
    slots: FleetSlots, *, reserve_expeditions: bool = True
) -> int:
    """Free lanes a colony fleet may use.

    The expansions reserve the full expedition share (``最大探险队``), so the
    result is ``舰队航道 - 在飞 - max(0, 最大探险队 - 在飞探险队)`` -- i.e.
    ``舰队航道数 - 最大探险队数 - 非探险在飞数``, never negative.
    """

    available = int(slots.max_slots) - int(slots.active)
    if reserve_expeditions:
        available -= max(
            0, int(slots.max_expeditions) - int(slots.active_expeditions)
        )
    return max(0, available)


def game_colony_remaining(
    colony_used: Optional[int], colony_max: Optional[int]
) -> Optional[int]:
    """Colony slots still free, or ``None`` when the page did not say."""

    if colony_max is None or colony_used is None:
        return None
    return max(0, int(colony_max) - int(colony_used))


def effective_limit(
    user_limit: int,
    colony_used: Optional[int],
    colony_max: Optional[int],
    *,
    kept: int = 0,
) -> Optional[int]:
    """How many *newly colonized* planets this run may keep in total.

    ``colony_used`` already contains the planets this run kept, so the game-side
    allowance is ``(上限 - 已用) + 已保留`` (clamped to the absolute ``上限``);
    ``kept`` is the number of planets the script itself kept so far.  Taking
    ``min(user cap, that)`` gives the total new colonies allowed.

    ``user_limit <= 0`` means "no personal cap", leaving the game limit (or
    ``None`` when unknown) as the only bound.
    """

    game_total: Optional[int] = None
    if colony_max is not None:
        remaining = game_colony_remaining(colony_used, colony_max)
        if remaining is not None:
            game_total = min(int(colony_max), remaining + max(0, int(kept)))
    candidates = [
        value
        for value in (int(user_limit) if int(user_limit) > 0 else None, game_total)
        if value is not None
    ]
    return min(candidates) if candidates else None


# ---------------------------------------------------------------------------
# Galaxy scan
# ---------------------------------------------------------------------------


@dataclass
class ScanDecision:
    """What one galaxy row means for the colonization plan."""

    coordinates: str
    status: str
    area: Optional[int] = None
    temperature: Optional[int] = None
    note: str = ""

    @property
    def is_candidate(self) -> bool:
        return self.status == STATUS_FREE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinates": self.coordinates,
            "status": self.status,
            "area": self.area,
            "temperature": self.temperature,
            "note": self.note,
        }


def _coordinate_text(galaxy: int, system: int, position: int) -> str:
    return f"{galaxy}:{system}:{position}"


def _slot_planet_name(slot: Any) -> str:
    return str(getattr(slot, "planet_name", "") or "").strip()


def classify_system(
    view: GalaxyView,
    box: ColonizeRange,
    *,
    own_coordinates: Iterable[str] = (),
    inflight_targets: Iterable[str] = (),
    username: Optional[str] = None,
) -> List[ScanDecision]:
    """Classify every in-range position of one galaxy page.

    ``free`` means "the page offers the colonize action here"; everything else
    is already decided (owned by us, owned by someone else, or not colonizable
    at all -- e.g. an abandoned colony that the cleanup cronjob has not removed
    yet).  Targets with a colony fleet in flight are skipped, not re-queued.
    """

    own = {str(item) for item in own_coordinates}
    inflight = {str(item) for item in inflight_targets}
    decisions: List[ScanDecision] = []
    for slot in view.slots or []:
        position = getattr(slot, "position", None)
        if position is None or int(position) not in box.position:
            continue
        coordinates = getattr(slot, "coordinates", None)
        if coordinates is not None:
            galaxy, system = int(coordinates.galaxy), int(coordinates.system)
        else:
            galaxy, system = view.galaxy, view.system
        if galaxy is None or system is None:
            continue
        if int(galaxy) not in box.galaxy or int(system) not in box.system:
            continue
        key = _coordinate_text(galaxy, system, int(position))
        if key in inflight:
            continue

        name = _slot_planet_name(slot)
        player = str(getattr(slot, "player_name", "") or "").strip()
        actions = getattr(slot, "actions", {}) or {}
        area = getattr(slot, "area", None)
        temperature = getattr(slot, "temperature", None)

        if key in own or bool(getattr(slot, "is_own", False)) or (
            username
            and player
            and player.casefold() == str(username).casefold()
            and name
        ):
            decisions.append(ScanDecision(key, "own", area, temperature, name or player))
        elif name.casefold() in _ABANDONED_NAMES:
            decisions.append(
                ScanDecision(key, "unavailable", area, temperature, "被遗弃的殖民地")
            )
        elif name:
            decisions.append(ScanDecision(key, "occupied", area, temperature, name))
        elif Mission.COLONIZE in actions:
            decisions.append(ScanDecision(key, STATUS_FREE, area, temperature, ""))
        else:
            decisions.append(
                ScanDecision(key, "unavailable", area, temperature, "无殖民选项")
            )
    return decisions


def distance_key(origin: Coordinates, target: Coordinates) -> Tuple[int, int, int]:
    """Cheap-first ordering: same galaxy before other galaxies, etc."""

    return (
        abs(int(target.galaxy) - int(origin.galaxy)),
        abs(int(target.system) - int(origin.system)),
        abs(int(target.planet) - int(origin.planet)),
    )


def sort_candidates(
    coordinates: Iterable[str],
    origin: Optional[Coordinates] = None,
    *,
    strategy: str = "nearest",
) -> List[str]:
    """Order the candidate queue (default: nearest to the departure planet)."""

    items = [str(item) for item in coordinates]
    if strategy != "nearest" or origin is None:
        return items

    def key(item: str) -> Tuple[Tuple[int, int, int], str]:
        try:
            point = Coordinates.parse(item)
        except ValueError:
            return ((10**9, 10**9, 10**9), item)
        return (distance_key(origin, point), item)

    return sorted(items, key=key)


# ---------------------------------------------------------------------------
# Persistent state / timetable
# ---------------------------------------------------------------------------


@dataclass
class PlanetRecord:
    """A planet the script kept, discarded or failed to colonize."""

    coordinates: str
    status: str = STATUS_KEPT
    planet_id: Optional[int] = None
    area: Optional[int] = None
    temp_max: Optional[int] = None
    temp_min: Optional[int] = None
    at: float = 0.0
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "coordinates": self.coordinates,
            "status": self.status,
            "planet_id": self.planet_id,
            "area": self.area,
            "temp_max": self.temp_max,
            "temp_min": self.temp_min,
            "at": self.at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlanetRecord":
        def as_int(value: Any) -> Optional[int]:
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return cls(
            coordinates=str(data.get("coordinates") or ""),
            status=str(data.get("status") or STATUS_KEPT),
            planet_id=as_int(data.get("planet_id")),
            area=as_int(data.get("area")),
            temp_max=as_int(data.get("temp_max")),
            temp_min=as_int(data.get("temp_min")),
            at=float(data.get("at") or 0.0),
            note=str(data.get("note") or ""),
        )


@dataclass
class InflightColony:
    """One colony fleet on its way, with the timetable wake-up time."""

    target: str
    fleet_id: Optional[int] = None
    planet_id: Optional[int] = None
    sent_at: Optional[float] = None
    arrive_at: Optional[float] = None
    home_at: Optional[float] = None
    #: First time the fleet table no longer listed it (arrival processed).
    missing_since: Optional[float] = None
    #: True when this run sent the fleet.  Fleets found in the table but not
    #: sent here (a human or another process) are tracked to avoid duplicate
    #: sends, but their result is never evaluated or abandoned.
    managed: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "fleet_id": self.fleet_id,
            "planet_id": self.planet_id,
            "sent_at": self.sent_at,
            "arrive_at": self.arrive_at,
            "home_at": self.home_at,
            "missing_since": self.missing_since,
            "managed": bool(self.managed),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InflightColony":
        def as_float(value: Any) -> Optional[float]:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        fleet_id = data.get("fleet_id")
        planet_id = data.get("planet_id")
        return cls(
            target=str(data.get("target") or ""),
            fleet_id=int(fleet_id) if fleet_id is not None else None,
            planet_id=int(planet_id) if planet_id is not None else None,
            sent_at=as_float(data.get("sent_at")),
            arrive_at=as_float(data.get("arrive_at")),
            home_at=as_float(data.get("home_at")),
            missing_since=as_float(data.get("missing_since")),
            managed=bool(data.get("managed")),
        )


@dataclass
class ColonizeState:
    """The persisted scan cursor, ledger and in-flight timetable."""

    path: Path = field(default_factory=lambda: Path("state/colonize_state.json"))
    schema: int = 1
    departure_id: Optional[int] = None
    departure_name: str = ""
    departure_coordinates: str = ""
    box: Optional[ColonizeRange] = None
    filters: PlanetFilter = field(default_factory=PlanetFilter)
    user_limit: int = 0
    colony_used: Optional[int] = None
    colony_max: Optional[int] = None
    colony_checked_at: Optional[float] = None
    scan_next: int = 0
    scan_complete: bool = False
    candidates: List[str] = field(default_factory=list)
    kept: List[PlanetRecord] = field(default_factory=list)
    discarded: List[PlanetRecord] = field(default_factory=list)
    failed: List[PlanetRecord] = field(default_factory=list)
    inflight: List[InflightColony] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: float = 0.0
    load_error: str = ""
    save_error: str = ""

    # -- counters --------------------------------------------------------

    @property
    def kept_count(self) -> int:
        return len(self.kept)

    @property
    def scan_total(self) -> int:
        return self.box.system_count if self.box is not None else 0

    @property
    def scanned_systems(self) -> int:
        return min(self.scan_total, max(0, int(self.scan_next)))

    @property
    def explored(self) -> bool:
        """True when every in-range position has a terminal decision."""

        return bool(self.scan_complete) and not self.candidates and not self.inflight

    def inflight_targets(self) -> List[str]:
        return [item.target for item in self.inflight]

    def inflight_for(self, target: str) -> Optional[InflightColony]:
        for item in self.inflight:
            if item.target == target:
                return item
        return None

    def is_decided(self, coordinates: str) -> bool:
        key = str(coordinates)
        return (
            key in {item.coordinates for item in self.kept}
            or key in {item.coordinates for item in self.discarded}
            or key in {item.coordinates for item in self.failed}
            or key in self.candidates
            or key in set(self.inflight_targets())
        )

    # -- mutations -------------------------------------------------------

    def reset_scan(self) -> None:
        self.scan_next = 0
        self.scan_complete = False
        self.candidates = []

    def add_candidate(self, coordinates: str) -> bool:
        key = str(coordinates)
        if not key or self.is_decided(key):
            return False
        self.candidates.append(key)
        return True

    def pop_candidate(self) -> Optional[str]:
        return self.candidates.pop(0) if self.candidates else None

    def record_keep(self, record: PlanetRecord) -> None:
        self._purge(record.coordinates)
        record.status = STATUS_KEPT
        self.kept.append(record)
        self._note(
            f"保留 {record.coordinates}：面积 {record.area}，"
            f"温度 {record.temp_min}~{record.temp_max}"
        )

    def record_discard(self, record: PlanetRecord) -> None:
        self._purge(record.coordinates)
        record.status = STATUS_DISCARDED
        self.discarded.append(record)
        self._note(f"抛弃 {record.coordinates}：{record.note}")

    def record_failure(self, record: PlanetRecord) -> None:
        self._purge(record.coordinates)
        record.status = STATUS_FAILED
        self.failed.append(record)
        self._note(f"失败 {record.coordinates}：{record.note}")

    def record_send(self, entry: InflightColony) -> None:
        self.inflight = [item for item in self.inflight if item.target != entry.target]
        self.inflight.append(entry)
        if entry.target in self.candidates:
            self.candidates.remove(entry.target)
        self._note(f"发船 {entry.target}")

    def drop_inflight(self, target: str) -> Optional[InflightColony]:
        for index, item in enumerate(self.inflight):
            if item.target == target:
                return self.inflight.pop(index)
        return None

    def _forget(self, coordinates: str) -> None:
        if coordinates in self.candidates:
            self.candidates.remove(coordinates)
        self.drop_inflight(coordinates)

    def _purge(self, coordinates: str) -> None:
        """Drop earlier ledger entries for the same position (re-colonized)."""

        self.kept = [item for item in self.kept if item.coordinates != coordinates]
        self.discarded = [
            item for item in self.discarded if item.coordinates != coordinates
        ]
        self.failed = [item for item in self.failed if item.coordinates != coordinates]
        self._forget(coordinates)

    def _note(self, text: str) -> None:
        now = time.time()
        self.history.append({"at": now, "text": text})
        if len(self.history) > HISTORY_LIMIT:
            self.history = self.history[-HISTORY_LIMIT:]
        self.updated_at = now

    # -- wake-up math ----------------------------------------------------

    def next_wake(
        self,
        now: float,
        *,
        arrive_grace: float = 30.0,
        retry_delay: float = 60.0,
        max_sleep: float = 900.0,
    ) -> float:
        """Earliest interesting moment: an arrival, a retry, a safety poll."""

        now = float(now)
        candidates: List[float] = [now + max(1.0, float(max_sleep))]
        for item in self.inflight:
            if item.arrive_at is None:
                candidates.append(now + max(1.0, float(retry_delay)))
                continue
            candidates.append(float(item.arrive_at) + max(0.0, float(arrive_grace)))
        if self.candidates or not self.scan_complete:
            candidates.append(now + max(1.0, float(retry_delay)))
        future = [value for value in candidates if value > now]
        return min(future) if future else now + max(1.0, float(retry_delay))

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "departure": {
                "id": self.departure_id,
                "name": self.departure_name,
                "coordinates": self.departure_coordinates,
            },
            "range": self.box.as_dict() if self.box is not None else None,
            "filters": self.filters.as_dict(),
            "user_limit": int(self.user_limit),
            "colony_slots": {
                "used": self.colony_used,
                "max": self.colony_max,
                "checked_at": self.colony_checked_at,
            },
            "scan": {
                "next": int(self.scan_next),
                "total": self.scan_total,
                "complete": bool(self.scan_complete),
            },
            "candidates": list(self.candidates),
            "kept": [item.as_dict() for item in self.kept],
            "discarded": [item.as_dict() for item in self.discarded],
            "failed": [item.as_dict() for item in self.failed],
            "inflight": [item.as_dict() for item in self.inflight],
            "history": list(self.history),
            "updated_at": self.updated_at,
        }

    @classmethod
    def load(cls, path: Any) -> "ColonizeState":
        state = cls(path=Path(path))
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return state
        except OSError as exc:
            state.load_error = f"{type(exc).__name__}: {exc}"
            return state
        try:
            data = json.loads(raw)
        except ValueError as exc:
            state.load_error = f"{type(exc).__name__}: {exc}"
            return state
        if not isinstance(data, Mapping):
            state.load_error = "状态文件不是 JSON 对象"
            return state

        departure = data.get("departure") or {}
        if isinstance(departure, Mapping):
            planet_id = departure.get("id")
            try:
                state.departure_id = int(planet_id) if planet_id is not None else None
            except (TypeError, ValueError):
                state.departure_id = None
            state.departure_name = str(departure.get("name") or "")
            state.departure_coordinates = str(departure.get("coordinates") or "")
        raw_range = data.get("range")
        if isinstance(raw_range, Mapping):
            try:
                state.box = ColonizeRange.from_dict(raw_range)
            except (KeyError, TypeError, ValueError) as exc:
                state.load_error = f"殖民范围无法读取：{exc}"
        raw_filters = data.get("filters")
        if isinstance(raw_filters, Mapping):
            state.filters = PlanetFilter.from_dict(raw_filters)
        try:
            state.user_limit = int(data.get("user_limit") or 0)
        except (TypeError, ValueError):
            state.user_limit = 0

        colony = data.get("colony_slots") or {}
        if isinstance(colony, Mapping):
            for key, attr in (("used", "colony_used"), ("max", "colony_max")):
                value = colony.get(key)
                try:
                    setattr(state, attr, int(value) if value is not None else None)
                except (TypeError, ValueError):
                    setattr(state, attr, None)
            checked = colony.get("checked_at")
            try:
                state.colony_checked_at = float(checked) if checked is not None else None
            except (TypeError, ValueError):
                state.colony_checked_at = None

        scan = data.get("scan") or {}
        if isinstance(scan, Mapping):
            try:
                state.scan_next = int(scan.get("next") or 0)
            except (TypeError, ValueError):
                state.scan_next = 0
            state.scan_complete = bool(scan.get("complete"))

        raw_candidates = data.get("candidates") or []
        if isinstance(raw_candidates, list):
            state.candidates = [str(item) for item in raw_candidates if str(item)]
        for key, target in (
            ("kept", state.kept),
            ("discarded", state.discarded),
            ("failed", state.failed),
        ):
            raw_items = data.get(key) or []
            if isinstance(raw_items, list):
                target.extend(
                    PlanetRecord.from_dict(item)
                    for item in raw_items
                    if isinstance(item, Mapping)
                )
        raw_inflight = data.get("inflight") or []
        if isinstance(raw_inflight, list):
            state.inflight = [
                InflightColony.from_dict(item)
                for item in raw_inflight
                if isinstance(item, Mapping)
            ]
        raw_history = data.get("history") or []
        if isinstance(raw_history, list):
            state.history = [
                dict(item) for item in raw_history if isinstance(item, Mapping)
            ][-HISTORY_LIMIT:]
        try:
            state.updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            state.updated_at = 0.0
        return state

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


__all__ = [
    "GALAXY_MAX",
    "HISTORY_LIMIT",
    "POSITION_MAX",
    "SYSTEM_MAX",
    "STATUS_COLONIZING",
    "STATUS_DISCARDED",
    "STATUS_FAILED",
    "STATUS_FREE",
    "STATUS_KEPT",
    "TERMINAL_STATUSES",
    "ColonizeRange",
    "ColonizeState",
    "Condition",
    "InflightColony",
    "IntRange",
    "PlanetFilter",
    "PlanetRecord",
    "ScanDecision",
    "available_colony_lanes",
    "classify_system",
    "distance_key",
    "effective_limit",
    "game_colony_remaining",
    "sort_candidates",
]

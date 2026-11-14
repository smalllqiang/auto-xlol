"""Typed data returned by the SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .constants import ResourceId


@dataclass(frozen=True)
class Coordinates:
    """A location in the universe, e.g. ``3:505:8``."""

    galaxy: int
    system: int
    planet: int
    type: int = 1

    @classmethod
    def parse(cls, text: str, type: int = 1) -> "Coordinates":
        import re

        match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\s*\]", text)
        if not match:
            match = re.search(r"\b(\d+)\s*:\s*(\d+)\s*:\s*(\d+)\b", text)
        if not match:
            raise ValueError(f"not a coordinate: {text!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), type)

    def __str__(self) -> str:
        return f"[{self.galaxy}:{self.system}:{self.planet}]"

    def as_dict(self) -> Dict[str, int]:
        return {
            "galaxy": self.galaxy,
            "system": self.system,
            "planet": self.planet,
            "type": self.type,
        }


@dataclass
class Resources:
    """Resource amounts, capacities and hourly production, keyed by resource id."""

    amounts: Dict[int, float] = field(default_factory=dict)
    capacities: Dict[int, float] = field(default_factory=dict)
    production: Dict[int, float] = field(default_factory=dict)

    def amount(self, key: object) -> float:
        rid = _resource_id(key)
        return float(self.amounts.get(rid, 0.0))

    def capacity(self, key: object) -> float:
        rid = _resource_id(key)
        return float(self.capacities.get(rid, 0.0))

    def hourly(self, key: object) -> float:
        rid = _resource_id(key)
        return float(self.production.get(rid, 0.0))

    @property
    def metal(self) -> float:
        return self.amount(ResourceId.METAL)

    @property
    def crystal(self) -> float:
        return self.amount(ResourceId.CRYSTAL)

    @property
    def deuterium(self) -> float:
        return self.amount(ResourceId.DEUTERIUM)

    @property
    def energy(self) -> float:
        return self.amount(ResourceId.ENERGY)

    @property
    def dark_matter(self) -> float:
        return self.amount(ResourceId.DARK_MATTER)

    @property
    def metal_per_hour(self) -> float:
        return self.hourly(ResourceId.METAL)

    @property
    def crystal_per_hour(self) -> float:
        return self.hourly(ResourceId.CRYSTAL)

    @property
    def deuterium_per_hour(self) -> float:
        return self.hourly(ResourceId.DEUTERIUM)

    def as_dict(self) -> Dict[str, Dict[int, float]]:
        return {
            "amounts": dict(self.amounts),
            "capacities": dict(self.capacities),
            "production": dict(self.production),
        }


def _resource_id(key: object) -> int:
    if isinstance(key, ResourceId):
        return int(key)
    if isinstance(key, int):
        return key
    text = str(key).strip().casefold()
    aliases = {
        "metal": 901,
        "crystal": 902,
        "deuterium": 903,
        "deut": 903,
        "energy": 911,
        "dark_matter": 921,
        "darkmatter": 921,
        "anti_energy": 922,
        "stardust": 923,
        "chests": 924,
    }
    if text in aliases:
        return aliases[text]
    if text.isdigit():
        return int(text)
    raise KeyError(f"unknown resource: {key!r}")


@dataclass(frozen=True)
class PlanetSummary:
    """A planet as listed in the planet selector."""

    id: int
    name: str
    coordinates: Optional[Coordinates] = None
    is_current: bool = False


@dataclass
class PlanetInfo:
    """The currently selected planet, enriched with data from the overview page."""

    id: Optional[int] = None
    name: str = ""
    coordinates: Optional[Coordinates] = None
    diameter_km: Optional[float] = None
    fields_used: Optional[int] = None
    fields_max: Optional[int] = None
    temp_min: Optional[int] = None
    temp_max: Optional[int] = None
    points: Optional[int] = None
    rank: Optional[int] = None
    total_rank: Optional[int] = None


@dataclass
class OverviewStatus:
    research: Optional[str] = None
    building: Optional[str] = None
    shipyard: Optional[str] = None

    @staticmethod
    def _idle(text: Optional[str]) -> bool:
        return text is not None and text.strip().casefold() in {"空闲", "idle", "free", "-"}

    @property
    def research_idle(self) -> bool:
        return self._idle(self.research)

    @property
    def building_idle(self) -> bool:
        return self._idle(self.building)

    @property
    def shipyard_idle(self) -> bool:
        return self._idle(self.shipyard)


@dataclass
class Overview:
    username: Optional[str] = None
    universe: Optional[str] = None
    game_name: Optional[str] = None
    engine_version: Optional[str] = None
    language: Optional[str] = None
    server_time: Optional[datetime] = None
    resources: Resources = field(default_factory=Resources)
    planet: PlanetInfo = field(default_factory=PlanetInfo)
    planets: List[PlanetSummary] = field(default_factory=list)
    status: OverviewStatus = field(default_factory=OverviewStatus)


@dataclass
class BuildingInfo:
    id: int
    name: str
    level: int = 0
    level_to_build: int = 0
    max_level: int = 255
    factor: Optional[float] = None
    cost: Dict[int, float] = field(default_factory=dict)
    cost_overflow: Dict[int, float] = field(default_factory=dict)
    build_seconds: Optional[float] = None
    destroy_cost: Dict[int, float] = field(default_factory=dict)
    destroy_seconds: Optional[float] = None
    requirements_met: bool = True
    requirements: List[str] = field(default_factory=list)
    buyable: bool = True

    @property
    def next_level(self) -> int:
        return self.level_to_build + 1

    @property
    def can_build(self) -> bool:
        return (
            self.requirements_met
            and self.level < self.max_level
            and not self.missing_resources
        )

    @property
    def missing_resources(self) -> Dict[int, float]:
        return {k: v for k, v in self.cost_overflow.items() if v > 0}


@dataclass
class ResearchInfo:
    id: int
    name: str
    level: int = 0
    level_to_build: int = 0
    max_level: int = 255
    factor: Optional[float] = None
    cost: Dict[int, float] = field(default_factory=dict)
    cost_overflow: Dict[int, float] = field(default_factory=dict)
    research_seconds: Optional[float] = None
    requirements_met: bool = True
    requirements: List[str] = field(default_factory=list)
    buyable: bool = True

    @property
    def next_level(self) -> int:
        return self.level_to_build + 1

    @property
    def can_research(self) -> bool:
        return (
            self.requirements_met
            and self.level < self.max_level
            and not self.missing_resources
        )

    @property
    def missing_resources(self) -> Dict[int, float]:
        return {k: v for k, v in self.cost_overflow.items() if v > 0}


@dataclass
class ShipInfo:
    id: int
    name: str
    category: str = "fleet"  # "fleet" or "defense"
    available: int = 0
    max_buildable: int = 0
    cost: Dict[int, float] = field(default_factory=dict)
    build_seconds: Optional[float] = None
    requirements_met: bool = True
    requirements: List[str] = field(default_factory=list)

    @property
    def can_build(self) -> bool:
        return self.requirements_met and self.max_buildable > 0

    @property
    def is_defense(self) -> bool:
        return self.category == "defense"


@dataclass
class QueueEntry:
    """An entry in a construction/research/shipyard queue."""

    index: int
    element_id: Optional[int] = None
    name: str = ""
    amount: Optional[int] = None
    target_level: Optional[int] = None
    remaining_seconds: Optional[int] = None
    end_time: Optional[str] = None
    active: bool = False

    def __post_init__(self) -> None:
        if not self.name and self.element_id is not None:
            self.name = f"Element {self.element_id}"


@dataclass
class GalaxySlot:
    position: int
    planet_id: Optional[int] = None
    planet_name: str = ""
    player_name: str = ""
    coordinates: Optional[Coordinates] = None
    is_own: bool = False
    has_moon: bool = False
    has_debris: bool = False
    abandoned: bool = False
    area: Optional[int] = None
    temperature: Optional[int] = None
    actions: Dict[int, str] = field(default_factory=dict)


@dataclass
class GalaxyView:
    galaxy: Optional[int] = None
    system: Optional[int] = None
    slots: List[GalaxySlot] = field(default_factory=list)


@dataclass
class FleetInfo:
    id: Optional[int] = None
    number: Optional[int] = None
    mission: str = ""
    mission_id: Optional[int] = None
    amount: Optional[int] = None
    start_coordinates: Optional[Coordinates] = None
    start_time: Optional[str] = None
    target_coordinates: Optional[Coordinates] = None
    target_time: Optional[str] = None
    arrival_time: Optional[str] = None
    returning: bool = False
    can_recall: bool = False
    ships: Dict[str, int] = field(default_factory=dict)


@dataclass
class FleetShips:
    id: int
    name: str
    count: int


@dataclass
class MissionOption:
    id: int
    name: str


@dataclass
class FleetDispatch:
    mission: int = 0
    mission_name: str = ""
    from_coordinates: Optional[Coordinates] = None
    target_coordinates: Optional[Coordinates] = None
    distance: Optional[float] = None
    consumption: Optional[float] = None
    start_time: Optional[str] = None
    arrival_time: Optional[str] = None
    ships: Dict[int, int] = field(default_factory=dict)


@dataclass
class FleetPreview:
    """Result of the first two fleet-dispatch steps; no fleet is sent."""

    mission: int = 0
    mission_name: str = ""
    from_coordinates: Optional[Coordinates] = None
    target_coordinates: Optional[Coordinates] = None
    consumption: Optional[float] = None
    fleet_room: Optional[float] = None
    max_speed: Optional[float] = None
    available_missions: List[MissionOption] = field(default_factory=list)
    stay_times: List[int] = field(default_factory=list)


@dataclass
class ServerInfo:
    username: Optional[str] = None
    universe: Optional[str] = None
    game_name: Optional[str] = None
    engine_version: Optional[str] = None
    language: Optional[str] = None
    server_time: Optional[datetime] = None


@dataclass(frozen=True)
class MessageCounter:
    """Unread/total counters for one message category tab."""

    category: int
    unread: int = 0
    total: int = 0

    @property
    def category_name(self) -> str:
        from .constants import message_category_name

        return message_category_name(self.category)


@dataclass
class Message:
    """One row of the message inbox."""

    id: int
    sender: str = ""
    subject: str = ""
    body: str = ""
    time: Optional[datetime] = None
    time_text: str = ""
    category: Optional[int] = None
    category_name: str = ""
    unread: bool = False
    coordinates: List[Coordinates] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "sender": self.sender,
            "subject": self.subject,
            "body": self.body,
            "time": self.time.isoformat() if self.time is not None else None,
            "time_text": self.time_text,
            "category": self.category,
            "category_name": self.category_name,
            "unread": self.unread,
            "coordinates": [str(item) for item in self.coordinates],
        }


@dataclass
class MessagePage:
    """A page of the message inbox (the AJAX ``mode=view`` fragment)."""

    messages: List[Message] = field(default_factory=list)
    category: Optional[int] = None
    category_name: str = ""
    page: int = 1
    pages: List[int] = field(default_factory=list)

    def __iter__(self):
        return iter(self.messages)

    def __len__(self) -> int:
        return len(self.messages)

    @property
    def has_next_page(self) -> bool:
        return bool(self.pages) and self.page < max(self.pages)


@dataclass(frozen=True)
class Bonus:
    """One line of the ``infobonus`` (bonus summary) page."""

    name: str
    percent: float = 0.0
    multiplier: float = 1.0
    raw_percent: str = ""


@dataclass
class Bonuses:
    """All bonus lines, looked up by their Chinese/English label."""

    items: List[Bonus] = field(default_factory=list)

    def __iter__(self):
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, name: object) -> bool:
        return self.get(str(name)) is not None

    def as_dict(self) -> Dict[str, float]:
        """Map bonus name -> multiplier (``1.03`` for ``3%``)."""

        return {item.name: item.multiplier for item in self.items}

    def percent_dict(self) -> Dict[str, float]:
        return {item.name: item.percent for item in self.items}

    def get(self, name: str) -> Optional[Bonus]:
        wanted = str(name).strip().casefold()
        for item in self.items:
            if item.name.strip().casefold() == wanted:
                return item
        return None

    def percent(self, name: str, default: float = 0.0) -> float:
        item = self.get(name)
        return item.percent if item is not None else default

    def multiplier(self, name: str, default: float = 1.0) -> float:
        item = self.get(name)
        return item.multiplier if item is not None else default


@dataclass
class ResourceProduction:
    """One resource column of the production control page."""

    resource_id: int
    base: float = 0.0
    buildings: Dict[int, float] = field(default_factory=dict)
    bonus: float = 0.0
    capacity: Optional[float] = None
    total: float = 0.0
    daily: float = 0.0
    weekly: float = 0.0


@dataclass
class ProductionReport:
    """The per-planet production table from ``page=resources``."""

    planet: str = ""
    resources: Dict[int, ResourceProduction] = field(default_factory=dict)
    levels: Dict[int, int] = field(default_factory=dict)
    ratios: Dict[int, int] = field(default_factory=dict)

    def get(self, resource: object) -> Optional[ResourceProduction]:
        try:
            return self.resources.get(_resource_id(resource))
        except KeyError:
            return None

    def ratio(self, element: object, default: int = 100) -> int:
        from .constants import resolve_element

        try:
            return self.ratios.get(resolve_element(element, "building"), default)
        except (KeyError, ValueError):
            return default


@dataclass
class StatisticsRow:
    """One row of a leaderboard page."""

    rank: int
    name: str
    score: int
    player_id: Optional[int] = None
    alliance: Optional[str] = None
    alliance_id: Optional[int] = None
    tags: List[str] = field(default_factory=list)
    status: str = ""
    members: Optional[int] = None
    member_score: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "rank": self.rank,
            "name": self.name,
            "score": self.score,
            "player_id": self.player_id,
            "alliance": self.alliance,
            "alliance_id": self.alliance_id,
            "tags": list(self.tags),
            "status": self.status,
            "members": self.members,
            "member_score": self.member_score,
        }


@dataclass
class StatisticsBoard:
    """A parsed ``page=statistics`` ranking table.

    ``who``/``sort`` mirror the page's selects: 1=players, 2=alliances;
    sort 1=score, 2=fleet, 3=research, 4=buildings, 5=defense.
    """

    who: int = 1
    sort: int = 1
    range: int = 1
    rows: List[StatisticsRow] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "who": self.who,
            "sort": self.sort,
            "range": self.range,
            "rows": [row.as_dict() for row in self.rows],
        }


@dataclass
class BattleRow:
    """One entry of the battle hall (``page=battleHall``).

    ``result`` mirrors the page template: ``"a"`` means the attacker won,
    ``"r"`` the defender won, and ``""`` is an unknown/drawn result (the page
    does not color a winner).  ``date`` is the raw localized text and ``time``
    its naive ISO form (the game renders the viewer's wall clock).
    """

    rank: int = 0
    attacker: str = ""
    defender: str = ""
    result: str = ""
    date: str = ""
    time: str = ""
    units: int = 0
    report_id: str = ""

    @property
    def winner(self) -> Optional[str]:
        if self.result == "a":
            return self.attacker
        if self.result == "r":
            return self.defender
        return None

    @property
    def loser(self) -> Optional[str]:
        if self.result == "a":
            return self.defender
        if self.result == "r":
            return self.attacker
        return None

    def as_dict(self) -> Dict[str, object]:
        return {
            "rank": self.rank,
            "attacker": self.attacker,
            "defender": self.defender,
            "result": self.result,
            "winner": self.winner or "",
            "loser": self.loser or "",
            "date": self.date,
            "time": self.time,
            "units": self.units,
            "report_id": self.report_id,
        }


@dataclass
class BattleHall:
    """The top-100 battle list from ``page=battleHall``.

    ``order`` is ``units`` or ``date``; ``sort`` is ``asc`` or ``desc``.
    """

    order: str = "units"
    sort: str = "desc"
    rows: List[BattleRow] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "order": self.order,
            "sort": self.sort,
            "rows": [row.as_dict() for row in self.rows],
        }

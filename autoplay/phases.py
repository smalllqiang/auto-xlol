"""Game phase model.

The guide splits the game into stages (farming -> gravity/processor ->
expedition -> ascension).  Phase detection is intentionally simple and based on
facts we can read, not on elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

#: 资源处理单元 / Resource Processor.
RESOURCE_PROCESSOR_ID = 81
#: 探险技术 / Expedition Technology.
EXPEDITION_TECH_ID = 124


@dataclass(frozen=True)
class Phase:
    key: str
    name: str
    goal: str
    goal_kind: str
    goal_element: int
    goal_level: int


PRE_GRAVITY = Phase(
    "pre_gravity",
    "种田初起",
    "建成资源处理单元",
    "building",
    RESOURCE_PROCESSOR_ID,
    1,
)
EXPEDITION_PREP = Phase(
    "expedition_prep",
    "探险准备",
    "把探险技术升到 9 级（解锁第 3 条探险航道）",
    "research",
    EXPEDITION_TECH_ID,
    9,
)
EXPEDITION = Phase(
    "expedition",
    "探险纪元",
    "攒够 50 万 fleetPoints 并维持探险",
    "milestone",
    0,
    0,
)

PHASES = (PRE_GRAVITY, EXPEDITION_PREP, EXPEDITION)


def _level(items: Iterable[Any], element_id: int) -> int:
    for item in items:
        if int(getattr(item, "id", -1)) == element_id:
            return int(getattr(item, "level", 0) or 0)
    return 0


def detect_phase(buildings: Iterable[Any], research: Iterable[Any]) -> Phase:
    if _level(buildings, RESOURCE_PROCESSOR_ID) < 1:
        return PRE_GRAVITY
    if _level(research, EXPEDITION_TECH_ID) < 9:
        return EXPEDITION_PREP
    return EXPEDITION

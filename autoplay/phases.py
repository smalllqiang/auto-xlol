"""Game phase model.

The guide splits the game into stages (farming -> gravity/processor ->
expedition -> ascension).  Phase detection is intentionally simple and based on
facts we can read, not on elapsed time.

``PHASES`` is also the *default directive*: :func:`detect_phase` walks whatever
phase list the injected directive carries (AI-1), and only falls back to this
tuple when there is none.
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


def detect_phase(
    buildings: Iterable[Any],
    research: Iterable[Any],
    directive: Any = None,
) -> Phase:
    """First phase whose goal is not met, in directive order.

    ``directive`` is anything with ``phases`` (see
    :class:`autoplay.directive.Directive`); ``None`` uses the built-in
    three-phase default.  A ``milestone`` phase is never "met" by a level
    check, so it ends the walk -- which is why validation keeps it last.
    """

    phases = tuple(getattr(directive, "phases", None) or PHASES)
    for phase in phases:
        if phase.goal_kind == "building":
            if _level(buildings, phase.goal_element) < phase.goal_level:
                return phase
        elif phase.goal_kind == "research":
            if _level(research, phase.goal_element) < phase.goal_level:
                return phase
    return phases[-1]


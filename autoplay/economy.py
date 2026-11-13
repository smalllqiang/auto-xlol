"""Resource arithmetic shared by the strategy layer.

The guide's exchange ratio is 4 metal = 2 crystal = 1 deuterium, so every cost
is converted to a single "metal equivalent" number before comparing actions.
"""

from __future__ import annotations

from typing import Dict, Mapping, Optional

#: How many metal one unit of a resource is worth.
EQUIVALENT_WEIGHTS: Dict[int, float] = {901: 1.0, 902: 2.0, 903: 4.0}

#: Standard OGame/2Moons production growth per level.
PRODUCTION_GROWTH = 1.1


def equivalent_cost(cost: Mapping[int, float]) -> float:
    """Convert a resource cost dict to metal equivalents."""

    total = 0.0
    for resource_id, amount in cost.items():
        total += float(amount) * EQUIVALENT_WEIGHTS.get(int(resource_id), 1.0)
    return total


def production_delta(observed: float, level: int, growth: float = PRODUCTION_GROWTH) -> Optional[float]:
    """Estimate the hourly gain of one more level, from the observed output.

    The game's own numbers already include universe speed, bonuses and plasma
    tech, so the estimate is self-calibrating: only the growth curve is assumed.
    Returns ``None`` when there is nothing to extrapolate from.
    """

    if observed <= 0 or level <= 0:
        return None
    return observed * (growth * (level + 1) / level - 1.0)


def scale_cost(
    cost: Mapping[int, float], factor: Optional[float], steps: int
) -> Dict[int, float]:
    """Total cost of ``steps`` consecutive levels, given the per-level factor.

    ``cost`` is the price of the next level; each further level costs ``factor``
    times the previous one (the factor is read from the game page).
    """

    if steps <= 1:
        return {int(key): float(value) for key, value in cost.items()}
    growth = float(factor or 1.0)
    multiplier = sum(growth**index for index in range(steps))
    return {int(key): float(value) * multiplier for key, value in cost.items()}


def payback_hours(cost_equivalent: float, gain_per_hour: Optional[float]) -> Optional[float]:
    """Hours until the investment pays for itself; ``None`` if it never does."""

    if not gain_per_hour or gain_per_hour <= 0:
        return None
    return cost_equivalent / gain_per_hour

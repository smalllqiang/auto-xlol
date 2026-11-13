"""Deterministic strategy layer for auto-playing New-Star.

The SDK answers "what is"; this package answers "what next".  It is kept out of
``newstar_sdk`` on purpose: the SDK is a stable client library, while the
strategy here is opinionated and expected to change often.
"""

from __future__ import annotations

from .economy import equivalent_cost, payback_hours, production_delta
from .phases import PHASES, Phase, detect_phase
from .planner import Action, Plan, build_plan, compute_plan

__all__ = [
    "Action",
    "PHASES",
    "Phase",
    "Plan",
    "build_plan",
    "compute_plan",
    "detect_phase",
    "equivalent_cost",
    "payback_hours",
    "production_delta",
]

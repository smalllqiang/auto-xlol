"""Deterministic strategy layer for auto-playing New-Star.

The SDK answers "what is"; this package answers "what next".  It is kept out of
``newstar_sdk`` on purpose: the SDK is a stable client library, while the
strategy here is opinionated and expected to change often.
"""

from __future__ import annotations

from .economy import equivalent_cost, payback_hours, production_delta
from .executor import ExecutionResult, execute_plan
from .expedition import (
    ExpeditionGroup,
    ExpeditionReport,
    ShipRecommendation,
    build_expedition_report,
    compute_expedition_report,
)
from .expedition_executor import ExpeditionDispatchResult, dispatch_expedition
from .phases import PHASES, Phase, detect_phase
from .planner import Action, Plan, build_plan, compute_plan, expand_requirement_actions
from .ship_build import ShipBuildResult, build_recommended_ships

__all__ = [
    "Action",
    "ExecutionResult",
    "ExpeditionDispatchResult",
    "ExpeditionGroup",
    "ExpeditionReport",
    "PHASES",
    "Phase",
    "Plan",
    "ShipBuildResult",
    "ShipRecommendation",
    "build_expedition_report",
    "build_recommended_ships",
    "build_plan",
    "compute_expedition_report",
    "compute_plan",
    "detect_phase",
    "dispatch_expedition",
    "equivalent_cost",
    "execute_plan",
    "expand_requirement_actions",
    "payback_hours",
    "production_delta",
]

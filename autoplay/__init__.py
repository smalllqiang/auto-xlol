"""Deterministic strategy layer for auto-playing New-Star.

The SDK answers "what is"; this package answers "what next".  It is kept out of
``newstar_sdk`` on purpose: the SDK is a stable client library, while the
strategy here is opinionated and expected to change often.
"""

from __future__ import annotations

from .daily import DailyBonusOutcome, run_daily_bonus
from .directive import (
    DEFAULT_PATH as DEFAULT_DIRECTIVE_PATH,
)
from .directive import (
    Directive,
    DirectiveError,
    default_directive,
    load_directive,
    parse_directive,
    save_directive,
)
from .economy import equivalent_cost, payback_hours, production_delta
from .executor import ExecutionResult, execute_plan
from .expedition import (
    ExpeditionGroup,
    ExpeditionReport,
    ShipRecommendation,
    build_expedition_report,
    compute_expedition_report,
)
from .expedition_cycle import ExpeditionCycleResult, run_expedition_cycle
from .expedition_executor import ExpeditionDispatchResult, dispatch_expedition
from .phases import PHASES, Phase, detect_phase
from .planner import Action, Plan, build_plan, compute_plan, expand_requirement_actions
from .ship_build import ShipBuildResult, build_recommended_ships

__all__ = [
    "Action",
    "DEFAULT_DIRECTIVE_PATH",
    "DailyBonusOutcome",
    "Directive",
    "DirectiveError",
    "ExecutionResult",
    "ExpeditionCycleResult",
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
    "default_directive",
    "detect_phase",
    "dispatch_expedition",
    "run_daily_bonus",
    "run_expedition_cycle",
    "equivalent_cost",
    "execute_plan",
    "expand_requirement_actions",
    "load_directive",
    "parse_directive",
    "payback_hours",
    "production_delta",
    "save_directive",
]

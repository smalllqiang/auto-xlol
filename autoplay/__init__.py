"""Deterministic strategy layer for auto-playing New-Star.

The SDK answers "what is"; this package answers "what next".  It is kept out of
``newstar_sdk`` on purpose: the SDK is a stable client library, while the
strategy here is opinionated and expected to change often.
"""

from __future__ import annotations

from .agent import AgentConfig, AgentCycleResult, Decision, run_agent_cycle
from .brief import build_brief, collect_brief, render_brief
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
from .expedition_scheduler import (
    ExpeditionFillResult,
    ExpeditionPlan,
    ExpeditionTimetable,
    fill_expedition_slots,
    read_live_slots,
    read_max_slots,
)
from .llm import (
    ChatReply,
    FakeProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    provider_from_env,
)
from .phases import PHASES, Phase, detect_phase
from .planner import Action, Plan, build_plan, compute_plan, expand_requirement_actions
from .runner import RunReport, Step, parse_steps, run_steps
from .ship_build import ShipBuildResult, build_recommended_ships
from .threat import EspionageEvent, ThreatAssessment, assess_threat
from .threat_executor import RecallResult, recall_candidates, recall_fleets
from .tools import ToolRegistry, ToolResult, ToolSpec

__all__ = [
    "Action",
    "AgentConfig",
    "AgentCycleResult",
    "ChatReply",
    "DEFAULT_DIRECTIVE_PATH",
    "DailyBonusOutcome",
    "Decision",
    "Directive",
    "DirectiveError",
    "EspionageEvent",
    "ExecutionResult",
    "ExpeditionCycleResult",
    "ExpeditionDispatchResult",
    "ExpeditionFillResult",
    "ExpeditionGroup",
    "ExpeditionPlan",
    "ExpeditionTimetable",
    "ExpeditionReport",
    "FakeProvider",
    "OpenAICompatibleProvider",
    "PHASES",
    "Phase",
    "Plan",
    "Provider",
    "ProviderError",
    "RecallResult",
    "RunReport",
    "ShipBuildResult",
    "ShipRecommendation",
    "Step",
    "ThreatAssessment",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "assess_threat",
    "build_brief",
    "build_expedition_report",
    "build_plan",
    "build_recommended_ships",
    "collect_brief",
    "compute_expedition_report",
    "compute_plan",
    "default_directive",
    "detect_phase",
    "dispatch_expedition",
    "fill_expedition_slots",
    "parse_steps",
    "provider_from_env",
    "render_brief",
    "run_agent_cycle",
    "run_daily_bonus",
    "run_expedition_cycle",
    "run_steps",
    "equivalent_cost",
    "execute_plan",
    "expand_requirement_actions",
    "load_directive",
    "parse_directive",
    "payback_hours",
    "production_delta",
    "read_live_slots",
    "read_max_slots",
    "recall_candidates",
    "recall_fleets",
    "save_directive",
]

"""Strategic directive: where the phase goals come from (AI-1).

The planner reads facts from the pages and decides the *actions*; the directive
only says **what to aim at** -- phases, constraints, risk posture and when it
expires.  It never names a building or a research to run, and it carries no
numbers that could be read from a page (ADR-006: AI stays out of runtime
decisions).

The default directive reproduces the hard-coded three-phase strategy in
:mod:`autoplay.phases` exactly, so an untouched account plans the same as
before.  ``state/directive.json`` overrides it; anything unreadable or invalid
falls back to the default and records *why* in ``Directive.errors`` so the
journal can show it instead of silently running with a half-broken strategy.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .phases import PHASES, Phase

#: Where the operator-editable directive lives (relative to the repo root).
DEFAULT_PATH = "state/directive.json"

#: Only this schema version is understood; bump it when fields change shape.
SUPPORTED_VERSION = 1

#: Goal shapes the planner knows how to expand.
GOAL_KINDS = ("building", "research", "milestone")

#: Safety limits that must not be relaxed by a directive.  They mirror what the
#: executor already enforces; the directive only records them for the journal.
DEFAULT_CONSTRAINTS: Dict[str, Any] = {
    "one_action_per_round": True,
    "require_allow_list": True,
    "respect_queues": True,
    "skip_when_unaffordable": True,
}


class DirectiveError(ValueError):
    """Raised when a directive document cannot be used as written."""


@dataclass(frozen=True)
class Directive:
    """A validated strategy: ordered phase goals plus constraints."""

    phases: Tuple[Phase, ...]
    version: int = SUPPORTED_VERSION
    source: str = "default"  # "default" | "file"
    path: Optional[str] = None
    constraints: Dict[str, Any] = field(default_factory=dict)
    risk_posture: str = "conservative"
    expires_when: str = ""
    errors: Tuple[str, ...] = ()
    file_mtime: Optional[float] = None

    def age_seconds(self, now: Optional[float] = None) -> Optional[float]:
        """How old the directive file is; ``None`` for the built-in default."""

        if self.file_mtime is None:
            return None
        clock = time.time() if now is None else now
        return max(0.0, clock - self.file_mtime)

    def as_dict(self) -> Dict[str, Any]:
        """Journal-friendly summary (no page data, no secrets)."""

        age = self.age_seconds()
        return {
            "source": self.source,
            "version": self.version,
            "path": self.path,
            "age_seconds": round(age, 1) if age is not None else None,
            "risk_posture": self.risk_posture,
            "expires_when": self.expires_when,
            "phases": [phase.key for phase in self.phases],
            "errors": list(self.errors),
        }

    def to_payload(self) -> Dict[str, Any]:
        """The document written back to disk / shown by the doctor script."""

        return {
            "version": self.version,
            "phases": [
                {
                    "key": phase.key,
                    "name": phase.name,
                    "goal": phase.goal,
                    "goal_kind": phase.goal_kind,
                    "goal_element": phase.goal_element,
                    "goal_level": phase.goal_level,
                }
                for phase in self.phases
            ],
            "constraints": dict(self.constraints),
            "risk_posture": self.risk_posture,
            "expires_when": self.expires_when,
        }


def default_directive() -> Directive:
    """The strategy the code has always used, expressed as a directive."""

    return Directive(
        phases=tuple(PHASES),
        constraints=dict(DEFAULT_CONSTRAINTS),
        risk_posture="conservative",
        expires_when="阶段目标达成",
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_phases(phases: Tuple[Phase, ...]) -> List[str]:
    """Return human-readable problems; an empty list means usable."""

    errors: List[str] = []
    if not phases:
        return ["至少需要一个阶段"]

    seen: Dict[str, int] = {}
    last = len(phases) - 1
    for index, phase in enumerate(phases):
        label = f"第 {index + 1} 段（{phase.key or '?'}）"
        if not phase.key.strip():
            errors.append(f"{label} 缺少 key")
        elif phase.key in seen:
            errors.append(f"{label} 的 key 与第 {seen[phase.key] + 1} 段重复")
        else:
            seen[phase.key] = index
        if not phase.name.strip():
            errors.append(f"{label} 缺少 name")
        if not phase.goal.strip():
            errors.append(f"{label} 缺少 goal")
        if phase.goal_kind not in GOAL_KINDS:
            errors.append(
                f"{label} 的 goal_kind 必须是 {'/'.join(GOAL_KINDS)}，"
                f"当前是 {phase.goal_kind!r}"
            )
            continue
        if phase.goal_kind == "milestone":
            if index != last:
                errors.append(
                    f"{label} 是 milestone，必须是最后一段，"
                    "否则它后面的阶段永远无法到达"
                )
            continue
        if int(phase.goal_element) <= 0:
            errors.append(f"{label} 的 goal_element 必须是正整数")
        if int(phase.goal_level) < 1:
            errors.append(f"{label} 的 goal_level 必须 ≥ 1")
    return errors


# ---------------------------------------------------------------------------
# Parsing / loading / saving
# ---------------------------------------------------------------------------


def _phase_from_payload(item: Any, index: int) -> Phase:
    label = f"第 {index + 1} 段"
    if not isinstance(item, dict):
        raise DirectiveError(f"{label}必须是 JSON 对象")
    missing = [key for key in ("key", "name", "goal", "goal_kind") if key not in item]
    if missing:
        raise DirectiveError(f"{label}缺少字段 {'、'.join(missing)}")
    try:
        element = int(item.get("goal_element") or 0)
        level = int(item.get("goal_level") or 0)
    except (TypeError, ValueError) as exc:
        raise DirectiveError(f"{label}的 goal_element/goal_level 不是整数") from exc
    return Phase(
        key=str(item["key"]),
        name=str(item["name"]),
        goal=str(item["goal"]),
        goal_kind=str(item["goal_kind"]),
        goal_element=element,
        goal_level=level,
    )


def parse_directive(payload: Any, *, path: Optional[str] = None) -> Directive:
    """Parse and validate a directive document; raise :class:`DirectiveError`."""

    if not isinstance(payload, dict):
        raise DirectiveError("directive 必须是 JSON 对象")

    version = payload.get("version", SUPPORTED_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise DirectiveError("version 必须是整数")
    if version != SUPPORTED_VERSION:
        raise DirectiveError(
            f"不支持的 directive 版本 {version}（当前只认 {SUPPORTED_VERSION}）"
        )

    raw_phases = payload.get("phases")
    if not isinstance(raw_phases, list) or not raw_phases:
        raise DirectiveError("phases 必须是非空数组")
    phases = tuple(_phase_from_payload(item, index) for index, item in enumerate(raw_phases))

    errors = validate_phases(phases)
    if errors:
        raise DirectiveError("；".join(errors))

    constraints = payload.get("constraints")
    if constraints is None:
        constraints = {}
    if not isinstance(constraints, dict):
        raise DirectiveError("constraints 必须是 JSON 对象")

    risk = payload.get("risk_posture", "conservative")
    if not isinstance(risk, str):
        raise DirectiveError("risk_posture 必须是字符串")
    expires = payload.get("expires_when", "")
    if not isinstance(expires, str):
        raise DirectiveError("expires_when 必须是字符串")

    return Directive(
        phases=phases,
        version=version,
        source="file",
        path=path,
        constraints=dict(constraints),
        risk_posture=risk,
        expires_when=expires,
    )


def load_directive(path: str = DEFAULT_PATH) -> Directive:
    """Read ``path``; on any problem fall back to the default and say why.

    Never raises for a bad file on purpose: a broken directive must not stop
    the loop, but it must be visible in ``errors`` (journal / dashboard).
    """

    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_directive()
    except OSError as exc:
        return replace(
            default_directive(),
            path=str(target),
            errors=(f"directive 文件读不了，已回退默认：{exc}",),
        )

    try:
        payload = json.loads(raw)
        directive = parse_directive(payload, path=str(target))
    except json.JSONDecodeError as exc:
        return replace(
            default_directive(),
            path=str(target),
            errors=(f"directive 不是合法 JSON，已回退默认：{exc}",),
        )
    except DirectiveError as exc:
        return replace(
            default_directive(),
            path=str(target),
            errors=(f"directive 校验失败，已回退默认：{exc}",),
        )

    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = None
    return replace(directive, file_mtime=mtime, errors=(), source="file")


def save_directive(path: str, directive: Directive) -> None:
    """Write the directive atomically (temp file in the same directory)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = directive.to_payload()
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, target)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
